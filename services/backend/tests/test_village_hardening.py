"""U2 hardening (Phase 1 review): poison writes never wedge the writer, close
cancels while `closing`, lock loss drops pending writes."""
import pytest
from sqlalchemy import select

from agentplatform.db import (Run, RunState, VillageCommand, VillageEvent, VillageRun,
                              VillageSession, VillageSnapshot)
from agentplatform.events import FakeProducer
from agentplatform.village import runs
from agentplatform.village.contract import Event
from agentplatform.village.journal import LockLost
from agentplatform.village.main import NullLock
from agentplatform.village.store import StoreWriter
from tests.test_village_store import count, live_engine, make_engine


# --- poison items ----------------------------------------------------------------------

async def test_a_2000_char_refusal_reason_is_truncated_at_the_journal(sf):
    eng = await live_engine(sf)
    sub = await eng.journal.submit(eng.session_id, "admin", "add", {"key": "x"})
    await eng.journal.mark_applied(sub.command.seq, 3, "refused", "r" * 2000)
    async with sf() as s:
        row = (await s.execute(select(VillageCommand).where(VillageCommand.seq == sub.command.seq))).scalar_one()
    assert len(row.reason) == 256 and row.state == "refused"


async def test_event_text_is_truncated_at_the_store(sf):
    eng = await live_engine(sf)
    await eng.store.write_events(eng.session_id, "e", [Event(tick=1, kind="say", actor="a" * 500,
                                                             public_text="t" * 2000)])
    async with sf() as s:
        row = (await s.execute(select(VillageEvent).where(VillageEvent.tick == 1))).scalars().first()
    assert len(row.public_text) == 200 and len(row.actor) == 128


async def test_a_poison_event_batch_falls_back_to_a_minimal_row_and_the_queue_moves_on(sf):
    eng = await live_engine(sf)
    real = eng.store.write_events

    async def picky(session_id, epoch, events):          # stands in for a DB constraint violation
        if any(e.public_text for e in events):
            raise ValueError("value too long")
        return await real(session_id, epoch, events)
    eng.store.write_events = picky
    w = StoreWriter(eng.store, eng.journal, retry_delay=0)
    w.push_events(eng.session_id, [Event(tick=77, kind="say", public_text="poison")])
    w.push_events(eng.session_id, [Event(tick=78, kind="say")])
    await w.flush()
    assert w.pending == 0 and w.fallbacks == 1 and w.failures == StoreWriter.MAX_ATTEMPTS
    async with sf() as s:
        ticks = sorted((await s.execute(select(VillageEvent.tick).where(
            VillageEvent.tick.in_([77, 78])))).scalars())
    assert ticks == [77, 78]


async def test_an_item_that_cannot_be_written_at_all_is_abandoned_not_retried_forever(sf):
    eng = await live_engine(sf)

    async def down(*a, **kw):
        raise RuntimeError("db down")
    eng.store.write_events = down
    w = StoreWriter(eng.store, eng.journal, retry_delay=0)
    w.push_events(eng.session_id, [Event(tick=5, kind="say")])
    w.push_applied(1, 5, "applied", None)
    await w.flush()
    assert w.pending == 0 and w.abandoned == 1


# --- lock loss ---------------------------------------------------------------------------

class SwitchLock(NullLock):
    ok = True

    async def alive(self):
        return self.ok


async def test_lock_loss_drops_pending_writes_instead_of_flushing_them(sf):
    lock = SwitchLock()
    eng = await live_engine(sf, lock=lock)
    before = await count(sf, VillageEvent)
    eng.writer.push_events(eng.session_id, [Event(tick=9999, kind="say", public_text="late")])
    lock.ok = False                                      # another engine took over
    await eng.writer.flush()
    assert eng.writer.dropped and eng.writer.pending == 0 and not eng.has_lock and not eng.live
    assert await count(sf, VillageEvent) == before
    eng.writer.push_events(eng.session_id, [Event(tick=1, kind="say")])
    assert eng.writer.pending == 0                       # new work is refused too
    assert eng.tick_once() == []
    with pytest.raises(LockLost):
        await eng.journal.submit(eng.session_id, "admin", "add", {"key": "x"})
    with pytest.raises(LockLost):
        await eng.close_session()


# --- close: cancel while `closing`, then commit `closed` -----------------------------------

async def freeze_and_tick(eng):
    await eng.journal.system(eng.session_id, "freeze")
    eng.tick_once()
    assert eng.pending_close


async def state_of(sf, sid):
    async with sf() as s:
        return (await s.get(VillageSession, sid)).state


async def test_close_stays_closing_until_the_hooks_succeed(sf):
    eng = await live_engine(sf)
    calls, states = [], []

    async def cancel_hook(row):
        calls.append(1)
        states.append(await state_of(sf, row.id))
        if len(calls) == 1:
            raise RuntimeError("cancel publish failed")
    eng.close_hooks.append(cancel_hook)
    await freeze_and_tick(eng)
    sid = eng.session_id
    await eng.close_session()
    assert await state_of(sf, sid) == "closing" and eng.pending_close      # not closed: runs may be alive
    await eng.close_session()
    assert states == ["closing", "closing"] and await state_of(sf, sid) == "closed"
    assert await count(sf, VillageSnapshot, VillageSnapshot.kind == "close") == 1


async def test_a_crash_during_the_close_hooks_is_finished_at_boot(sf):
    a = await live_engine(sf)

    async def dies(row):
        raise KeyboardInterrupt            # the process dies inside the hook
    a.close_hooks.append(dies)
    await freeze_and_tick(a)
    sid = a.session_id
    with pytest.raises(KeyboardInterrupt):
        await a.close_session()
    assert await state_of(sf, sid) == "closing"
    ran = []
    b = make_engine(sf, epoch="epoch-b")

    async def hook(row):
        ran.append(row.id)
    b.close_hooks.append(hook)
    await b.acquire_lock_once()
    await b.boot()
    assert b.pending_close
    await b.close_session()
    assert ran == [sid] and await state_of(sf, sid) == "closed"


class Flaky:
    def __init__(self, fails):
        self.inner, self.fails = FakeProducer(), fails

    async def publish(self, *a, **kw):
        if self.fails:
            self.fails -= 1
            raise ConnectionError("kafka down")
        await self.inner.publish(*a, **kw)


async def test_a_failed_cancel_publish_is_an_error_and_is_retried(sf, monkeypatch):
    async def instant(_):
        return None
    monkeypatch.setattr(runs.asyncio, "sleep", instant)
    async with sf() as s:
        for rid, sess in (("r1", "s1"), ("r2", "s2")):
            s.add(Run(id=rid, agent="pai", trigger="village", requested_by="v", prompt="", state=RunState.RUNNING))
            s.add(VillageRun(session_id=sess, persona="pai", role="planner", run_id=rid, state="running"))
        await s.commit()
    flaky = Flaky(2)
    assert await runs.cancel_runs(sf, flaky, "s1") == 1             # third attempt went through
    assert any(d.get("type") == "cancel" for _, _, d in flaky.inner.published)
    with pytest.raises(runs.CancelFailed):
        await runs.cancel_runs(sf, Flaky(99), "s2")
    async with sf() as s:
        row = (await s.execute(select(VillageRun).where(VillageRun.run_id == "r2"))).scalar_one()
    assert row.state == "running"                                   # not claimed cancelled


async def test_boot_sweeps_runs_of_closed_sessions(sf):
    async with sf() as s:
        s.add(VillageSession(id="old", day="2026-11-26", kind="daily", state="closed"))
        s.add(VillageSession(id="now", day="2026-11-27", kind="daily", state="live"))
        for rid, sess, row_state, run_state in (("a", "old", "running", RunState.RUNNING),
                                                ("b", "old", "cancelled", RunState.RUNNING),
                                                ("c", "old", "done", RunState.SUCCEEDED),
                                                ("d", "now", "running", RunState.RUNNING)):
            s.add(Run(id=rid, agent="pai", trigger="village", requested_by="v", prompt="", state=run_state))
            s.add(VillageRun(session_id=sess, persona="pai", role="planner", run_id=rid, state=row_state))
        await s.commit()
    p = FakeProducer()
    assert await runs.sweep_stale_runs(sf, p) == 2
    cancelled = {d["run_id"] for _, _, d in p.published if d.get("type") == "cancel"}
    assert cancelled == {"a", "b"}                                  # never the live session's run
    async with sf() as s:
        states = {r.run_id: r.state for r in (await s.execute(select(VillageRun))).scalars()}
    assert states == {"a": "cancelled", "b": "cancelled", "c": "done", "d": "running"}
