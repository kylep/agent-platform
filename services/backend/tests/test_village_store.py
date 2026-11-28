"""U2: the village store, journal, bus and tick plumbing (plan §2, §3, §11, App. A).

Also home of `FakeWorld` and `make_engine`, which the recovery/clock/pruner
tests import: a tiny deterministic World (the real one is U1's) whose hash
depends on WHICH tick each command was applied at, so a replay that applies a
command at the wrong tick shows up as a hash mismatch."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from agentplatform.db import (VillageCommand, VillageEvent, VillageKeepsake, VillageMark,
                              VillageNote, VillageSession, VillageSnapshot)
from agentplatform.events import (FakeProducer, TOPIC_VILLAGE_COMMANDS, TOPIC_VILLAGE_EVENTS,
                                  TOPIC_VILLAGE_FRAMES)
from agentplatform.village.contract import (Command, CommandResult, Event, Snapshot, TICK_S,
                                            hash_state)
from agentplatform.village.journal import IdempotencyConflict
from agentplatform.village.main import Engine, NullLock
from agentplatform.village.store import (FAST_EVERY_TICKS, FAST_UNTIL_TICKS, SLOW_EVERY_TICKS,
                                         SnapshotPolicy, StoreWriter)

T0 = datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc)      # 12:00 in Toronto (EST)


class FakeWorld:
    """Deterministic: `acc` folds in every tick and every command with the tick it ran at."""
    tick = 0

    def __init__(self, tick=0, acc=1, values=None, frozen=False, pulls=None):
        self.tick, self.acc, self.frozen = tick, acc, frozen
        self.values = dict(values or {})
        self.pulls = list(pulls or [])

    def step(self, dt=TICK_S):
        assert dt == TICK_S
        if self.frozen:
            return []
        self.tick += 1
        self.acc = (self.acc * 31 + self.tick) % 1_000_003
        return [Event(tick=self.tick, kind="say", actor="pai", public_text=f"t{self.tick}")] \
            if self.tick % 5 == 0 else []

    def apply(self, command: Command) -> CommandResult:
        if command.action == "freeze":
            self.frozen = True
        elif command.action == "add":
            key, n = command.args["key"], command.args.get("n", 1)
            self.values[key] = self.values.get(key, 0) + n
            self.acc = (self.acc + n * 7 + self.tick * 13) % 1_000_003
        elif command.action == "pull":
            self.pulls.append(command.args["line"])
        elif command.action == "bad":
            return CommandResult("refused", "no such thing")
        return CommandResult("applied")

    def to_snapshot(self):
        return {"tick": self.tick, "acc": self.acc, "values": self.values,
                "frozen": self.frozen, "pulls": self.pulls}

    def state_hash(self):
        return hash_state(self.to_snapshot())

    @classmethod
    def from_snapshot(cls, state):
        return cls(state["tick"], state["acc"], state["values"], state["frozen"], state["pulls"])

    def frame(self):
        return {"pos": {"pai": [self.tick, 0]}}


def fake_world(state):
    return FakeWorld() if state is None else FakeWorld.from_snapshot(state)


class Now:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def make_engine(sf, *, now=None, lock=None, epoch=None, quota=None, producer=None):
    async def no_codex():
        return False

    async def no_quota(provider):
        return None
    return Engine(sf, producer or FakeProducer(), make_world=fake_world, lock=lock or NullLock(),
                  now=now or Now(), epoch=epoch, quota=quota or no_quota, codex_in_use=no_codex)


async def live_engine(sf, **kw):
    """An engine with one live session opened directly (no clock), world fresh."""
    eng = make_engine(sf, **kw)
    await eng.acquire_lock_once()
    await eng.boot()
    row = await eng.store.add_session(day="2026-11-27", kind="test", state="live", length_min=60,
                                      planned_start=T0, planned_end=T0 + timedelta(hours=1),
                                      started_at=T0)
    await eng.open_session(row)
    return eng


async def run_ticks(eng, n, commands=None, flush_each=True):
    """Tick `n` times; `commands` maps tick index -> (action, args) submitted just before it.
    The writer is flushed every tick (the real one drains concurrently), so periodic
    snapshots are not coalesced away."""
    for i in range(n):
        if commands and i in commands:
            action, args = commands[i]
            await eng.journal.submit(eng.session_id, "admin", action, args)
        eng.tick_once()
        if flush_each:
            await eng.writer.flush()
    await eng.writer.flush()


async def count(sf, model, *where):
    async with sf() as s:
        return (await s.execute(select(func.count()).select_from(model).where(*where))).scalar()


# --- snapshot policy ------------------------------------------------------------

def test_snapshot_cadence_is_5s_for_5_minutes_then_once_a_minute():
    assert FAST_EVERY_TICKS == 20 and FAST_UNTIL_TICKS == 1200 and SLOW_EVERY_TICKS == 240
    p = SnapshotPolicy()
    due = [t for t in range(0, 2000) if p.due(t)]
    assert due[:3] == [20, 40, 60] and 1180 in due and 1200 in due
    assert [t for t in due if t > 1200] == [1440, 1680, 1920]
    assert not p.due(0)


def test_snapshot_only_written_when_the_hash_changed():
    p = SnapshotPolicy()
    assert p.should_write(20, "a")
    p.wrote(20, "a")
    assert not p.should_write(40, "a")          # due, but nothing changed
    assert p.should_write(40, "b")
    assert not p.should_write(41, "c")          # changed, but not due


async def test_ticking_writes_periodic_snapshots_on_cadence(sf):
    eng = await live_engine(sf)
    await run_ticks(eng, 130)
    async with sf() as s:
        ticks = (await s.execute(select(VillageSnapshot.tick).where(
            VillageSnapshot.kind == "periodic").order_by(VillageSnapshot.tick))).scalars().all()
    assert ticks == [20, 40, 60, 80, 100, 120]
    assert await count(sf, VillageSnapshot, VillageSnapshot.kind == "open") == 1


async def test_unchanged_state_skips_snapshots(sf):
    class Still(FakeWorld):                  # hash never moves: the tick is not part of its state
        def to_snapshot(self):
            return {"fixed": 1}

        @classmethod
        def from_snapshot(cls, state):
            return cls()
    eng = await live_engine(sf)
    eng.world = Still()
    eng.policy.reset(0, eng.world.state_hash())
    await run_ticks(eng, 100)
    assert await count(sf, VillageSnapshot, VillageSnapshot.kind == "periodic") == 0


# --- persistence between sessions --------------------------------------------------

async def test_state_persists_between_sessions(sf):
    eng = await live_engine(sf)
    await run_ticks(eng, 30, {3: ("add", {"key": "wood", "n": 2}), 9: ("pull", {"line": "toward the pond"})})
    await eng.journal.system(eng.session_id, "freeze")
    eng.tick_once()
    assert eng.pending_close
    await eng.close_session()
    closed_hash = eng.world.state_hash()
    async with sf() as s:
        sess = (await s.execute(select(VillageSession))).scalars().one()
    assert sess.state == "closed" and sess.tick_end == eng.world.tick and sess.end_reason == "time"

    # a fresh process, a new day: the world is the one that was frozen
    eng2 = make_engine(sf, epoch="e2")
    await eng2.boot()
    assert eng2.world.state_hash() == closed_hash
    assert eng2.world.values == {"wood": 2} and eng2.world.pulls == ["toward the pond"]
    next_day = await eng2.store.add_session(day="2026-11-28", kind="test", state="live", length_min=30,
                                            planned_start=T0, planned_end=T0 + timedelta(minutes=30))
    await eng2.open_session(next_day)
    assert eng2.world.state_hash() == closed_hash
    assert eng2.tick_start == eng2.world.tick


async def test_the_world_is_frozen_between_sessions(sf):
    eng = await live_engine(sf)
    await run_ticks(eng, 10)
    await eng.journal.system(eng.session_id, "freeze")
    eng.tick_once()
    await eng.close_session()
    before, events_before = eng.world.state_hash(), await count(sf, VillageEvent)
    for _ in range(50):
        assert eng.tick_once() == []
    await eng.writer.flush()
    assert eng.world.state_hash() == before and await count(sf, VillageEvent) == events_before


async def test_notes_marks_keepsakes_persist_across_sessions(sf):
    async with sf() as s:
        s.add_all([VillageNote(agent="pai", session_id="s1", text="the reeds were slippery"),
                   VillageMark(agent="pai", session_id="s1", tick=40, x=3, y=4, sentence="first rope"),
                   VillageKeepsake(agent="pai", object_id="o1", session_id="s1", sentence="for Kai")])
        await s.commit()
    eng = await live_engine(sf)
    await run_ticks(eng, 20)
    await eng.journal.system(eng.session_id, "freeze")
    eng.tick_once()
    await eng.close_session()
    for model in (VillageNote, VillageMark, VillageKeepsake):
        assert await count(sf, model) == 1


# --- journal -------------------------------------------------------------------------

async def test_a_command_is_committed_before_it_is_enqueued(sf):
    eng = await live_engine(sf)
    out = await eng.journal.submit(eng.session_id, "planner", "edit_queue", {"ops": []},
                                   run_id="r1", persona="pai", request_id="q1")
    assert out.command.seq and not out.duplicate and out.command.state == "accepted"
    assert list(eng.journal.inbox)[-1].seq == out.command.seq
    async with sf() as s:
        row = (await s.execute(select(VillageCommand).where(VillageCommand.seq == out.command.seq))).scalar_one()
    assert row.args_hash == out.command.args_hash and row.state == "accepted" and row.apply_tick is None


async def test_same_request_id_returns_the_first_receipt_different_args_conflict(sf):
    eng = await live_engine(sf)
    first = await eng.journal.submit(eng.session_id, "planner", "say", {"line": "hi"},
                                     run_id="r1", request_id="a")
    n = len(eng.journal.inbox)
    again = await eng.journal.submit(eng.session_id, "planner", "say", {"line": "hi", "request_id": "a"},
                                     run_id="r1", request_id="a")
    assert again.duplicate and again.command.seq == first.command.seq
    assert len(eng.journal.inbox) == n                 # not applied twice
    with pytest.raises(IdempotencyConflict):
        await eng.journal.submit(eng.session_id, "planner", "say", {"line": "different"},
                                 run_id="r1", request_id="a")
    other = await eng.journal.submit(eng.session_id, "planner", "say", {"line": "hi"},
                                     run_id="r2", request_id="a")     # another run, same id: fine
    assert other.command.seq != first.command.seq


async def test_tick_applies_in_seq_order_marks_apply_tick_and_resolves_waiters(sf):
    eng = await live_engine(sf)
    a = await eng.journal.submit(eng.session_id, "admin", "add", {"key": "x"})
    b = await eng.journal.submit(eng.session_id, "admin", "bad", {})
    fa, fb = eng.wait_for(a.command.seq), eng.wait_for(b.command.seq)
    at = eng.world.tick
    eng.tick_once()
    await eng.writer.flush()
    assert fa.result().state == "applied" and fb.result().state == "refused"
    async with sf() as s:
        rows = {r.seq: r for r in (await s.execute(select(VillageCommand))).scalars()}
    assert rows[a.command.seq].apply_tick == at and rows[a.command.seq].state == "applied"
    assert rows[b.command.seq].state == "refused" and rows[b.command.seq].reason == "no such thing"
    assert eng.last_applied_seq == b.command.seq


async def test_the_tick_never_awaits(sf):
    """tick_once is a plain function: it cannot await the DB (§3)."""
    import inspect
    eng = await live_engine(sf)
    assert not inspect.iscoroutinefunction(eng.tick_once)
    eng.tick_once()
    assert eng.writer.pending > 0                      # work was queued, not done inline


# --- writer and events ---------------------------------------------------------------

async def test_events_get_seq_epoch_and_reach_the_bus(sf):
    producer = FakeProducer()
    eng = await live_engine(sf, producer=producer, epoch="epoch-a")
    await run_ticks(eng, 12)
    await eng.bus.drain()
    async with sf() as s:
        rows = (await s.execute(select(VillageEvent).where(VillageEvent.kind == "say").order_by(VillageEvent.seq))).scalars().all()
    assert [r.tick for r in rows] == [5, 10] and {r.epoch for r in rows} == {"epoch-a"}
    sent = [d for t, k, d in producer.published if t == TOPIC_VILLAGE_EVENTS and d["kind"] == "say"]
    assert [d["seq"] for d in sent] == [r.seq for r in rows]
    assert all(k == eng.session_id for t, k, d in producer.published if t == TOPIC_VILLAGE_EVENTS)
    assert any(t == TOPIC_VILLAGE_FRAMES for t, _, _ in producer.published)      # 2 Hz frames


async def test_applied_commands_are_mirrored_to_the_commands_topic(sf):
    producer = FakeProducer()
    eng = await live_engine(sf, producer=producer)
    await eng.journal.submit(eng.session_id, "planner", "say", {"line": "hi"}, run_id="r", persona="pai", request_id="1")
    eng.tick_once()
    await eng.bus.drain()
    msgs = [(k, d) for t, k, d in producer.published if t == TOPIC_VILLAGE_COMMANDS and d["action"] == "say"]
    assert msgs and msgs[0][0] == "pai" and msgs[0][1]["state"] == "applied"


async def test_periodic_snapshots_coalesce_but_marks_keep_their_order(sf):
    eng = await live_engine(sf)
    w = StoreWriter(eng.store, eng.journal)
    for tick in (20, 40, 60):
        w.push_snapshot(Snapshot("s", tick, 0, "h", {}, "periodic"))
    assert w.pending == 1                              # an unwritten older periodic is replaced
    w.push_snapshot(Snapshot("s", 80, 0, "h", {}, "open"))
    assert w.pending == 2                              # open/close are never dropped
    w.push_events("s", [Event(tick=1, kind="say")] * 3)
    assert w.pending == 3                              # events are never dropped


async def test_a_failing_snapshot_write_does_not_stall_the_queue(sf):
    eng = await live_engine(sf)

    async def boom(snap):
        raise RuntimeError("db down")
    eng.store.write_snapshot, orig = boom, eng.store.write_snapshot
    w = StoreWriter(eng.store, eng.journal)
    w.push_snapshot(Snapshot(eng.session_id, 20, 0, "h", {}, "periodic"))
    w.push_events(eng.session_id, [Event(tick=20, kind="say", public_text="x")])
    await w.drain()
    assert w.failures == 1 and w.pending == 0
    assert await count(sf, VillageEvent, VillageEvent.public_text == "x") == 1
