"""`python -m agentplatform.village.main`: the village engine process (plan §1, §3).

One replica (Deployment `strategy: Recreate`) AND a Postgres advisory lock, so
there is exactly one writer of the world. The fixed-step tick loop never awaits
the DB or the network: commands arrive already committed to the journal, and
events, `apply_tick` marks and snapshots go to `StoreWriter`'s queue.

Other units attach by module: after boot, each of `commands` (U4, the :8001
port), `runs` and `reflection` (U3/U5) that exists and defines
`async def run(engine)` is started as one more task of this process. Session
hooks: append to `engine.open_hooks` / `engine.close_hooks` (async callables
taking the session row) to start planner runs at open and cancel them at T-0.
"""
import asyncio
import importlib
import logging
import os
import sys
import time
import uuid
from datetime import datetime
from typing import Any, Awaitable, Callable

from sqlalchemy import text

from agentplatform.db import utcnow
from agentplatform.events import Producer
from agentplatform.village.bus import VillageBus
from agentplatform.village.clock import SessionClock, codex_in_use_reader, snapshot_reader
from agentplatform.village.contract import (FRAME_EVERY_TICKS, TICK_S, Command, CommandResult,
                                            Event, Snapshot, World)
from agentplatform.village.journal import Journal, advance, recover
from agentplatform.village.store import SnapshotPolicy, StoreWriter, VillagePruner, VillageStore, aware

log = logging.getLogger("village.main")

LOCK_KEY = 0x56494C4C            # "VILL": the single-writer advisory lock
LOCK_RETRY_S = 5.0
CLOCK_POLL_S = 5.0
MAX_CATCH_UP_TICKS = 4           # §3: then sim time slips (logged)
COMPONENTS = ("commands", "runs", "reflection")

WorldFactory = Callable[[dict[str, Any] | None], World]


def default_make_world(state: dict[str, Any] | None) -> World:
    """Fresh world, or the one a snapshot describes. U1's `world.new_world()`
    builds the starting village; its class is the one that restores snapshots."""
    from agentplatform.village import world as w
    fresh = w.new_world()
    return fresh if state is None else type(fresh).from_snapshot(state)


# --- single writer ---------------------------------------------------------------

class PgAdvisoryLock:
    """Session-level `pg_try_advisory_lock` on a dedicated connection. The lock
    lives exactly as long as that connection, so a dropped connection is a lost
    lock and `alive()` says so."""

    def __init__(self, engine, key: int = LOCK_KEY):
        self.engine, self.key, self.conn = engine, key, None

    async def try_acquire(self) -> bool:
        conn = await self.engine.connect()
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        got = (await conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key})).scalar()
        if not got:
            await conn.close()
            return False
        self.conn = conn
        return True

    async def alive(self) -> bool:
        try:
            await self.conn.execute(text("SELECT 1"))
            return True
        except Exception:
            log.exception("village lock connection lost")
            return False

    async def release(self) -> None:
        if self.conn is not None:
            try:
                await self.conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
                await self.conn.close()
            except Exception:
                pass
            self.conn = None


class NullLock:
    """sqlite/dev: always the writer."""

    async def try_acquire(self) -> bool:
        return True

    async def alive(self) -> bool:
        return True

    async def release(self) -> None:
        return None


# --- the engine --------------------------------------------------------------------

class Engine:
    def __init__(self, session_factory, producer: Producer, *, make_world: WorldFactory = default_make_world,
                 lock=None, now: Callable[[], datetime] = utcnow, epoch: str | None = None,
                 quota=None, codex_in_use=None):
        self.sf = session_factory
        self.epoch = epoch or uuid.uuid4().hex
        self.make_world, self.lock, self._now = make_world, lock or NullLock(), now
        self.store = VillageStore(session_factory)
        self.journal = Journal(session_factory)
        self.bus = VillageBus(producer)
        self.writer = StoreWriter(self.store, self.journal, self.bus, self.epoch)
        self.policy = SnapshotPolicy()
        self.clock = SessionClock(
            self.store, self.journal, self.bus, quota=quota or snapshot_reader(session_factory),
            codex_in_use=codex_in_use or codex_in_use_reader(session_factory, self.store),
            now=now, on_open=self.open_session)
        self.pruner = VillagePruner(session_factory, now=now, live=self._is_live)
        self.world: World | None = None
        self.session_id: str | None = None
        self.session_row = None
        self.tick_start = 0
        self.live = False
        self.has_lock = False
        self.pending_close = False
        self.last_applied_seq = 0
        self.waiters: dict[int, asyncio.Future] = {}
        self.open_hooks: list[Callable[[Any], Awaitable[None]]] = []
        self.close_hooks: list[Callable[[Any], Awaitable[None]]] = []
        self.recovery = None
        self.slipped_ticks = 0
        self.stop = asyncio.Event()
        self.settings = None          # set by main()
        self.port = 8001              # the command port commands.py binds

    async def _is_live(self) -> bool:
        return self.live

    # --- lock ---------------------------------------------------------------
    async def acquire_lock_once(self) -> bool:
        self.has_lock = await self.lock.try_acquire()
        return self.has_lock

    async def acquire_lock(self) -> None:
        while not await self.acquire_lock_once():
            log.warning("village: another engine holds the writer lock; retrying in %.0fs", LOCK_RETRY_S)
            await asyncio.sleep(LOCK_RETRY_S)

    async def watch_lock(self, every_s: float = 10.0) -> None:
        while await self.lock.alive():
            await asyncio.sleep(every_s)
        self.has_lock = False
        self.live = False
        raise RuntimeError("village writer lock lost; exiting so Kubernetes restarts us")

    # --- boot (§3.1) ----------------------------------------------------------
    async def boot(self) -> None:
        live = await self.store.live_session()
        rec = await recover(self.store, self.journal, self.make_world,
                            live.id if live else None, self.epoch)
        self.recovery, self.world, self.last_applied_seq = rec, rec.world, rec.last_applied_seq
        if rec.fresh:
            log.info("village boot: no snapshot, starting a new world")
        if rec.mismatches:
            log.error("village boot: %d heartbeat hash mismatches in replay", len(rec.mismatches))
            self.bus.session((live.day if live else "-"), {
                "event": "recovery_mismatch", "mismatches": rec.mismatches[:5]}, "village.recovery_warning")
        if live is not None:
            self.session_id, self.session_row = live.id, live
            self.tick_start = live.tick_start if live.tick_start is not None else rec.snapshot_tick
            snap = await self.store.start_state(live.id)
            self.policy.reset(self.world.tick, snap.state_hash if snap else None)
            if rec.events:
                self.writer.push_events(live.id, rec.events)
            for seq, tick, result in rec.unrecorded_applied:
                self.writer.push_applied(seq, tick, result.state, result.reason)
            self.live = True
            if await self.journal.system_sent(live.id, "freeze"):
                self.pending_close = True      # crashed between T-0 and the close snapshot
        log.info("village boot: tick=%d replayed=%d ticks=%d superseded=%d live=%s",
                 self.world.tick, rec.replayed, rec.ticks, rec.superseded, bool(live))

    # --- session lifecycle ------------------------------------------------------
    async def open_session(self, row) -> None:
        """Called by the clock when a session goes live (§3 "Between sessions")."""
        if self.world is None:
            snap = await self.store.start_state(None)
            self.world = self.make_world(snap.state if snap else None)
            self.last_applied_seq = snap.last_applied_seq if snap else 0
        await self.writer.flush()
        self.tick_start = self.world.tick
        row = await self.store.update_session(row.id, tick_start=self.tick_start)
        snap = Snapshot(session_id=row.id, tick=self.world.tick, last_applied_seq=self.last_applied_seq,
                        state_hash=self.world.state_hash(), state=self.world.to_snapshot(), kind="open")
        await self.store.write_snapshot(snap)
        self.policy.reset(self.world.tick, snap.state_hash)
        self.session_id, self.session_row, self.pending_close, self.live = row.id, row, False, True
        await self.journal.system(row.id, "session_open",
                                  {"length_min": row.length_min, "day": row.day, "kind": row.kind})
        for hook in self.open_hooks:
            await hook(row)

    async def close_session(self) -> None:
        """T-0: the freeze command was applied. Write the close snapshot, mark
        the session closed, then run close hooks (cancel runs, recap, prompt apply)."""
        row = await self.store.get_session(self.session_id)
        self.live = False
        await self.writer.flush()
        snap = Snapshot(session_id=row.id, tick=self.world.tick, last_applied_seq=self.last_applied_seq,
                        state_hash=self.world.state_hash(), state=self.world.to_snapshot(), kind="close")
        await self.store.write_snapshot(snap)
        row = await self.store.update_session(row.id, state="closed", ended_at=self._now(),
                                              end_reason=row.end_reason or "time",
                                              tick_end=self.world.tick)
        self.policy.reset(self.world.tick, snap.state_hash)
        self.pending_close = False
        self.bus.session(row.day, {"session_id": row.id, "day": row.day, "kind": row.kind,
                                   "state": "closed", "event": "closed", "end_reason": row.end_reason})
        for hook in self.close_hooks:
            try:
                await hook(row)
            except Exception:
                log.exception("village close hook failed")
        self.session_id = self.session_row = None

    # --- the tick (sync: never awaits the DB or the network) ----------------------
    def tick_once(self) -> list[Event]:
        if not (self.live and self.has_lock) or self.world is None:
            return []
        world, sid = self.world, self.session_id
        while self.journal.inbox:
            cmd: Command = self.journal.inbox.popleft()
            result: CommandResult = world.apply(cmd)
            self.last_applied_seq = max(self.last_applied_seq, cmd.seq or 0)
            self.writer.push_applied(cmd.seq, world.tick, result.state, result.reason)
            self.bus.command(cmd.persona, {"seq": cmd.seq, "action": cmd.action, "source": cmd.source,
                                           "tick": world.tick, "state": result.state, "reason": result.reason})
            fut = self.waiters.pop(cmd.seq, None)
            if fut is not None and not fut.done():
                fut.set_result(result)
            if cmd.source == "system" and cmd.action == "freeze":
                self.pending_close = True
        events = advance(world)
        self.writer.push_events(sid, events)
        since_open = world.tick - self.tick_start
        if self.policy.due(since_open):
            digest = world.state_hash()
            if digest != self.policy.last_hash:
                self.writer.push_snapshot(Snapshot(
                    session_id=sid, tick=world.tick, last_applied_seq=self.last_applied_seq,
                    state_hash=digest, state=world.to_snapshot(), kind="periodic"))
                self.policy.wrote(world.tick, digest)
        if since_open % FRAME_EVERY_TICKS == 0:
            frame = getattr(world, "frame", None)
            if frame is not None:
                self.bus.frame(sid, world.tick, frame())
        return events

    def wait_for(self, seq: int) -> asyncio.Future:
        """A future resolved with the CommandResult when the tick applies `seq`
        (for commands.py: reply to the tool with the outcome)."""
        fut = asyncio.get_running_loop().create_future()
        self.waiters[seq] = fut
        return fut

    async def tick_loop(self) -> None:
        loop = asyncio.get_running_loop()
        next_at = loop.time()
        while not self.stop.is_set():
            if not (self.live and self.has_lock):
                await asyncio.sleep(0.5)
                next_at = loop.time()
                continue
            now = loop.time()
            if now < next_at:
                await asyncio.sleep(next_at - now)
                continue
            for _ in range(MAX_CATCH_UP_TICKS):
                if next_at > now:
                    break
                self.tick_once()
                next_at += TICK_S
            if next_at <= now:
                self.slipped_ticks += 1
                log.warning("village: tick loop %.2fs behind; sim time slips", now - next_at)
                next_at = now + TICK_S
            if self.pending_close:
                await self.close_session()
            await asyncio.sleep(0)

    async def clock_loop(self) -> None:
        while not self.stop.is_set():
            try:
                await self.clock.poll()
                if self.pending_close:      # frozen via replay with no tick running
                    await self.close_session()
            except Exception:
                log.exception("village clock poll failed")
            await asyncio.sleep(CLOCK_POLL_S)

    # --- run ---------------------------------------------------------------------
    async def run(self) -> None:
        await self.acquire_lock()
        self.writer.start()
        self.bus.start()
        await self.boot()
        tasks = [asyncio.create_task(c) for c in (
            self.tick_loop(), self.clock_loop(), self.pruner.run_forever(), self.watch_lock())]
        for name in COMPONENTS:
            try:
                mod = importlib.import_module(f"agentplatform.village.{name}")
            except ModuleNotFoundError as exc:
                if exc.name != f"agentplatform.village.{name}":
                    raise
                continue
            if hasattr(mod, "run"):
                tasks.append(asyncio.create_task(mod.run(self), name=f"village.{name}"))
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
            for t in done:
                t.result()
        finally:
            self.stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.writer.stop()
            await self.bus.stop()
            await self.lock.release()


async def main() -> None:
    from agentplatform.config import get_settings
    from agentplatform.db import init_db, make_engine, make_session_factory

    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    db = make_engine(settings.db_url)
    await init_db(db, settings.relay_default_grant, settings.tickets_default_grant,
                  settings.wiki_default_grant, quota_grant=settings.quota_default_grant,
                  artifacts_grant=settings.artifacts_default_grant,
                  memory_grant=settings.memory_default_grant, self_grant=settings.self_default_grant,
                  tasks_grant=settings.tasks_default_grant)
    producer = Producer(settings.kafka_bootstrap, source="village")
    await producer.start()
    lock = PgAdvisoryLock(db) if db.dialect.name == "postgresql" else NullLock()
    engine = Engine(make_session_factory(db), producer, lock=lock)
    engine.settings = settings
    engine.port = int(os.environ.get("AP_VILLAGE_PORT", "8001"))
    try:
        await engine.run()
    finally:
        await producer.stop()
        await db.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception:
        log.exception("village engine exited")
        sys.exit(1)
