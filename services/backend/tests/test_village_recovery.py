"""U2: snapshot + command-journal recovery and the single writer (plan §3.1, App. A)."""
import pytest
from sqlalchemy import delete, select, update

from agentplatform.db import VillageCommand, VillageEvent, VillageSession, VillageSnapshot
from agentplatform.village.main import Engine, NullLock
from tests.test_village_store import (FakeWorld, count, live_engine, make_engine, run_ticks)

COMMANDS = {7: ("add", {"key": "wood", "n": 2}), 41: ("add", {"key": "reeds", "n": 1}),
            66: ("add", {"key": "wood", "n": 5}), 101: ("pull", {"line": "toward the dock"})}


async def crashed_engine(sf, ticks=130):
    """Run a session for `ticks` ticks (everything reached the DB), then 'kill' it."""
    eng = await live_engine(sf, epoch="epoch-a")
    await run_ticks(eng, ticks, COMMANDS)
    return eng


async def non_superseded(sf):
    async with sf() as s:
        return (await s.execute(select(VillageEvent).where(
            VillageEvent.superseded.is_(False)).order_by(VillageEvent.seq))).scalars().all()


async def test_replay_reproduces_the_exact_state_hash(sf):
    a = await crashed_engine(sf)
    want = a.world.state_hash()
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    assert b.world.state_hash() == want and b.world.tick == a.world.tick
    assert b.recovery.mismatches == []


async def test_state_hash_matches_every_heartbeat_when_replaying_from_the_open_snapshot(sf):
    a = await crashed_engine(sf)
    want = a.world.state_hash()
    async with sf() as s:                      # periodic snapshots never landed: replay from `open`
        await s.execute(delete(VillageSnapshot).where(VillageSnapshot.kind == "periodic"))
        await s.commit()
    heartbeats = await count(sf, VillageEvent, VillageEvent.kind == "heartbeat")
    assert heartbeats == 130 // 20
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    assert b.recovery.ticks == 130 and b.recovery.replayed == len(COMMANDS) + 1   # + session_open
    assert b.world.state_hash() == want and b.recovery.mismatches == []


async def test_a_heartbeat_that_disagrees_is_reported(sf):
    await crashed_engine(sf)
    async with sf() as s:
        await s.execute(delete(VillageSnapshot).where(VillageSnapshot.kind == "periodic"))
        hb = (await s.execute(select(VillageEvent).where(VillageEvent.kind == "heartbeat",
                                                         VillageEvent.tick == 60))).scalar_one()
        hb.data = {"state_hash": "0" * 64}
        await s.commit()
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    assert [m["tick"] for m in b.recovery.mismatches] == [60]


async def test_a_command_applied_before_the_snapshot_is_not_reapplied(sf):
    a = await crashed_engine(sf)
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    # tick 7's add(wood, 2) is in snapshot 120; replaying only 120..130 must not add it again
    assert b.world.values["wood"] == 7 and a.world.values["wood"] == 7
    assert b.recovery.replayed == 0


async def test_a_committed_but_unapplied_command_is_applied_exactly_once(sf):
    a = await crashed_engine(sf, ticks=50)
    pending = await a.journal.submit(a.session_id, "admin", "add", {"key": "stone", "n": 3})
    # crash: committed to the journal, the inbox died with the process
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    assert b.world.values["stone"] == 3 and b.recovery.unrecorded == 1
    await b.writer.flush()
    async with sf() as s:
        row = (await s.execute(select(VillageCommand).where(VillageCommand.seq == pending.command.seq))).scalar_one()
    assert row.state == "applied" and row.apply_tick == b.world.tick
    # a second crash replays it from its recorded tick: still exactly once
    c = make_engine(sf, epoch="epoch-c")
    await c.boot()
    assert c.world.values["stone"] == 3
    assert not c.journal.inbox


async def test_previous_epoch_events_past_the_snapshot_are_superseded(sf):
    a = await crashed_engine(sf)
    snap_tick = 120
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    await b.writer.flush()
    live = await non_superseded(sf)
    by_tick = {}
    for e in live:
        by_tick.setdefault((e.tick, e.kind), []).append(e)
    assert all(len(v) == 1 for v in by_tick.values())           # replay did not duplicate the narrative
    late = [e for e in live if e.tick > snap_tick and e.kind == "say"]
    assert late and {e.epoch for e in late} == {"epoch-b"}
    early = [e for e in live if e.tick <= snap_tick]
    assert {e.epoch for e in early} == {"epoch-a"}
    async with sf() as s:
        old = (await s.execute(select(VillageEvent).where(VillageEvent.superseded.is_(True)))).scalars().all()
    assert old and all(e.epoch == "epoch-a" and e.tick > snap_tick for e in old)


async def test_replays_persist_after_restart(sf):
    a = await crashed_engine(sf)
    before = [(e.tick, e.kind, e.public_text) for e in await non_superseded(sf)]
    await a.journal.system(a.session_id, "freeze")
    a.tick_once()
    await a.close_session()
    closed = a.world.state_hash()
    for epoch in ("epoch-b", "epoch-c"):                        # restart twice
        eng = make_engine(sf, epoch=epoch)
        await eng.boot()
        assert eng.world.state_hash() == closed and not eng.live
    after = [(e.tick, e.kind, e.public_text) for e in await non_superseded(sf)]
    assert after[:len(before)] == before                         # nothing the session saved was lost


async def test_a_crash_after_the_freeze_command_still_closes_the_session(sf):
    a = await crashed_engine(sf, ticks=40)
    await a.journal.system(a.session_id, "freeze")
    a.tick_once()                                               # freeze applied, process dies before close
    await a.writer.flush()
    b = make_engine(sf, epoch="epoch-b")
    await b.boot()
    assert b.pending_close and b.world.frozen
    await b.close_session()
    async with sf() as s:
        sess = (await s.execute(select(VillageSession))).scalar_one()
        closes = (await s.execute(select(VillageSnapshot).where(VillageSnapshot.kind == "close"))).scalars().all()
    assert sess.state == "closed" and len(closes) == 1 and closes[0].state_hash == b.world.state_hash()


async def test_a_second_engine_without_the_lock_does_not_tick(sf):
    class Registry:
        held = False

    class MemoryLock:
        def __init__(self, reg):
            self.reg, self.mine = reg, False

        async def try_acquire(self):
            if self.reg.held:
                return False
            self.reg.held = self.mine = True
            return True

        async def alive(self):
            return self.mine

        async def release(self):
            if self.mine:
                self.reg.held = self.mine = False

    reg = Registry()
    first = await live_engine(sf, lock=MemoryLock(reg))
    assert first.has_lock
    second = make_engine(sf, lock=MemoryLock(reg), epoch="epoch-b")
    assert await second.acquire_lock_once() is False and not second.has_lock
    second.world, second.live, second.session_id = FakeWorld(), True, first.session_id
    assert second.tick_once() == [] and second.world.tick == 0   # refuses to tick
    await first.lock.release()
    assert await second.acquire_lock_once() is True


async def test_losing_the_lock_connection_stops_the_engine(sf):
    class Dead(NullLock):
        async def alive(self):
            return False
    eng = make_engine(sf, lock=Dead())
    eng.live = eng.has_lock = True
    with pytest.raises(RuntimeError, match="lock lost"):
        await eng.watch_lock(every_s=0)
    assert not eng.live and not eng.has_lock
