"""Persistence for the village engine (plan §2, §2.1, §3 "never awaits the DB").

`VillageStore` is the thin async layer over the `village_*` tables the engine
itself reads and writes: config, sessions, snapshots, events. The command
journal lives in journal.py. `StoreWriter` is the bounded FIFO queue the tick
loop pushes to (the tick never awaits the DB): events, `apply_tick` marks and
snapshots go through one queue in order, which is what makes "a snapshot that
reached the DB implies every earlier apply mark did too" true (recovery relies
on it, §3.1). `VillagePruner` is the hourly storage pruner (§2.1).
"""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy import and_, delete, func, not_, or_, select, update

from agentplatform.db import (VillageCommand, VillageConfig, VillageEvent, VillageMark,
                              VillagePromptChange, VillageSession, VillageSnapshot, utcnow)
from agentplatform.village.contract import (TICK_HZ, TICK_S, Event, Snapshot, merged_rules)

log = logging.getLogger("village.store")

# --- storage budget (plan §2.1) ---------------------------------------------
PERIODIC_SNAPSHOT_KEEP = timedelta(days=2)
EVENT_COMMAND_KEEP = timedelta(days=90)
CLIP_HALF_WINDOW_TICKS = int(5 / TICK_S)      # a clip is the event window [tick-5s, tick+5s]
PRUNE_EVERY_S = 3600
SIZE_LOG_EVERY_S = 86400

# Snapshot cadence (§2): every 5 s for the first 5 min of a session, then 1/min;
# only ever written when state_hash changed.
FAST_EVERY_TICKS = 5 * TICK_HZ
FAST_UNTIL_TICKS = 5 * 60 * TICK_HZ
SLOW_EVERY_TICKS = 60 * TICK_HZ

# Writer limits (§3): events are never dropped; past the cap we only log loudly.
EVENT_BACKLOG_WARN = 10_000


def aware(ts: datetime | None) -> datetime | None:
    from datetime import timezone
    if ts is None:
        return None
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts


class SnapshotPolicy:
    """When a periodic snapshot is due. Pure: the engine feeds it tick counts
    since the session opened and, only when due, the current state hash."""

    def __init__(self) -> None:
        self.last_hash: str | None = None
        self.last_tick: int | None = None

    def reset(self, tick: int, state_hash: str | None = None) -> None:
        """Called at session open/close, where an `open`/`close` snapshot was written."""
        self.last_tick, self.last_hash = tick, state_hash

    @staticmethod
    def interval(ticks_since_open: int) -> int:
        return FAST_EVERY_TICKS if ticks_since_open < FAST_UNTIL_TICKS else SLOW_EVERY_TICKS

    def due(self, ticks_since_open: int) -> bool:
        return ticks_since_open > 0 and ticks_since_open % self.interval(ticks_since_open) == 0

    def should_write(self, ticks_since_open: int, state_hash: str) -> bool:
        return self.due(ticks_since_open) and state_hash != self.last_hash

    def wrote(self, tick: int, state_hash: str) -> None:
        self.last_tick, self.last_hash = tick, state_hash


class VillageStore:
    """Async reads/writes of config, sessions, snapshots and events."""

    def __init__(self, session_factory):
        self.sf = session_factory

    # --- config ---------------------------------------------------------
    async def rules(self) -> dict[str, Any]:
        async with self.sf() as s:
            row = await s.get(VillageConfig, 1)
        return merged_rules(row.rules if row else None)

    # --- sessions -------------------------------------------------------
    async def get_session(self, session_id: str) -> VillageSession | None:
        async with self.sf() as s:
            return await s.get(VillageSession, session_id)

    async def daily_for(self, day: str) -> VillageSession | None:
        async with self.sf() as s:
            return (await s.execute(select(VillageSession).where(
                VillageSession.day == day, VillageSession.kind == "daily"))).scalar_one_or_none()

    async def sessions_in(self, states: tuple[str, ...]) -> list[VillageSession]:
        async with self.sf() as s:
            return list((await s.execute(select(VillageSession).where(
                VillageSession.state.in_(states)).order_by(VillageSession.created_at))).scalars())

    async def live_session(self) -> VillageSession | None:
        """The session the engine is running (live or closing); latest wins."""
        rows = await self.sessions_in(("live", "closing"))
        return rows[-1] if rows else None

    async def add_session(self, **fields: Any) -> VillageSession:
        async with self.sf() as s:
            row = VillageSession(**fields)
            s.add(row)
            await s.commit()
            return row

    async def update_session(self, session_id: str, **fields: Any) -> VillageSession:
        async with self.sf() as s:
            row = await s.get(VillageSession, session_id)
            for k, v in fields.items():
                setattr(row, k, v)
            await s.commit()
            return row

    # --- snapshots ------------------------------------------------------
    async def write_snapshot(self, snap: Snapshot) -> None:
        async with self.sf() as s:
            s.add(VillageSnapshot(session_id=snap.session_id, tick=snap.tick,
                                  last_applied_seq=snap.last_applied_seq,
                                  state_hash=snap.state_hash, state=snap.state, kind=snap.kind))
            await s.commit()

    @staticmethod
    def _to_snapshot(row: VillageSnapshot) -> Snapshot:
        return Snapshot(session_id=row.session_id, tick=row.tick,
                        last_applied_seq=row.last_applied_seq, state_hash=row.state_hash,
                        state=row.state, kind=row.kind)

    async def latest_snapshot(self, session_id: str | None = None,
                              kinds: tuple[str, ...] | None = None) -> Snapshot | None:
        """Newest by (tick, created_at); a session's, or any, optionally by kind."""
        stmt = select(VillageSnapshot)
        if session_id is not None:
            stmt = stmt.where(VillageSnapshot.session_id == session_id)
        if kinds:
            stmt = stmt.where(VillageSnapshot.kind.in_(kinds))
        stmt = stmt.order_by(VillageSnapshot.tick.desc(), VillageSnapshot.created_at.desc()).limit(1)
        async with self.sf() as s:
            row = (await s.execute(stmt)).scalar_one_or_none()
        return self._to_snapshot(row) if row else None

    async def start_state(self, session_id: str | None) -> Snapshot | None:
        """What boot loads (§3.1): the live session's latest snapshot, else the
        last `close` snapshot (the world is frozen between sessions)."""
        if session_id:
            snap = await self.latest_snapshot(session_id)
            if snap:
                return snap
        return await self.latest_snapshot(kinds=("close",))

    # --- events ---------------------------------------------------------
    async def write_events(self, session_id: str, epoch: str,
                           events: list[Event]) -> list[VillageEvent]:
        if not events:
            return []
        async with self.sf() as s:
            # Bounded columns are truncated here, at the write boundary: Postgres
            # rejects an over-long value and one bad row must not wedge the queue.
            rows = [VillageEvent(session_id=session_id, epoch=epoch, tick=e.tick, kind=e.kind[:32],
                                 actor=(e.actor or None) and e.actor[:128], subjects=e.subjects,
                                 public_text=(e.public_text or None) and e.public_text[:200],
                                 data=e.data) for e in events]
            s.add_all(rows)
            await s.commit()
            return rows

    async def supersede(self, session_id: str, after_tick: int, new_epoch: str) -> int:
        """§3.1 step 3: previous-epoch events past the snapshot are replaced by
        the replay's re-emission."""
        async with self.sf() as s:
            res = await s.execute(update(VillageEvent).where(
                VillageEvent.session_id == session_id, VillageEvent.epoch != new_epoch,
                VillageEvent.tick > after_tick, VillageEvent.superseded.is_(False)
            ).values(superseded=True))
            await s.commit()
            return res.rowcount or 0

    async def heartbeats_after(self, session_id: str, after_tick: int,
                               new_epoch: str) -> dict[int, str]:
        """tick -> state_hash of the previous epoch's heartbeats past the snapshot."""
        async with self.sf() as s:
            rows = (await s.execute(select(VillageEvent.tick, VillageEvent.data).where(
                VillageEvent.session_id == session_id, VillageEvent.kind == "heartbeat",
                VillageEvent.epoch != new_epoch, VillageEvent.tick > after_tick,
                VillageEvent.superseded.is_(False)))).all()
        return {t: d.get("state_hash") for t, d in rows}

    async def max_event_tick(self, session_id: str, after_tick: int, new_epoch: str) -> int:
        async with self.sf() as s:
            value = (await s.execute(select(func.max(VillageEvent.tick)).where(
                VillageEvent.session_id == session_id, VillageEvent.epoch != new_epoch,
                VillageEvent.tick > after_tick, VillageEvent.superseded.is_(False)))).scalar()
        return value or after_tick


@dataclass
class _Applied:
    seq: int
    tick: int
    state: str
    reason: str | None


class StoreWriter:
    """The tick's way to persist without awaiting. `push_*` are plain methods
    (no await); one background task drains the FIFO into the store.

    - events are never dropped; past EVENT_BACKLOG_WARN pending we log loudly
    - a pending periodic snapshot is replaced by a newer one (coalescing), which
      keeps the snapshot's place after every mark queued before it
    - `open`/`close` snapshots are written directly by the engine, after `flush()`
    """

    MAX_ATTEMPTS = 5

    def __init__(self, store: VillageStore, journal, bus=None, epoch: str = "", *,
                 fence: Callable[[], Awaitable[bool]] | None = None,
                 retry_delay: float = 1.0):
        self.store, self.journal, self.bus, self.epoch = store, journal, bus, epoch
        self.fence = fence              # async () -> still the single writer?
        self.retry_delay = retry_delay
        self.dropped = False            # lock lost: nothing more is ever written
        self.fallbacks = 0
        self.abandoned = 0
        self._q: deque = deque()
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._task: asyncio.Task | None = None
        self._stopping = False
        self.written_events = 0
        self.failures = 0

    # --- producers (sync) -----------------------------------------------
    def drop(self) -> None:
        """Lock lost: another engine may be the writer now. Discard everything
        pending and refuse new work; flushing would be a second writer."""
        self.dropped = True
        self._q.clear()
        self._idle.set()

    def _push(self, item: tuple) -> None:
        if self.dropped:
            return
        self._q.append(item)
        self._idle.clear()
        self._wake.set()

    def push_events(self, session_id: str, events: list[Event]) -> None:
        if not events:
            return
        pending = sum(len(i[2]) for i in self._q if i[0] == "events")
        if pending > EVENT_BACKLOG_WARN:
            log.error("village writer: %d events pending (DB slow?); not dropping", pending)
        self._push(("events", session_id, events))

    def push_applied(self, seq: int, tick: int, state: str, reason: str | None) -> None:
        self._push(("applied", _Applied(seq, tick, state, reason)))

    def push_snapshot(self, snap: Snapshot) -> None:
        for i, item in enumerate(self._q):
            if item[0] == "snapshot" and item[1].kind == "periodic" and snap.kind == "periodic":
                del self._q[i]
                break
        self._push(("snapshot", snap))

    @property
    def pending(self) -> int:
        return len(self._q)

    # --- consumer ---------------------------------------------------------
    async def _write(self, item: tuple) -> None:
        kind = item[0]
        if kind == "events":
            rows = await self.store.write_events(item[1], self.epoch, item[2])
            self.written_events += len(rows)
            if self.bus is not None:
                self.bus.events(item[1], self.epoch, rows)
        elif kind == "applied":
            a: _Applied = item[1]
            await self.journal.mark_applied(a.seq, a.tick, a.state, a.reason)
        else:
            await self.store.write_snapshot(item[1])

    async def _fallback(self, item: tuple) -> None:
        """A minimal row for an item that keeps failing, so the narrative and
        the journal keep their place and the queue moves on."""
        kind = item[0]
        if kind == "events":
            await self.store.write_events(item[1], self.epoch,
                                          [Event(tick=e.tick, kind=e.kind) for e in item[2]])
        elif kind == "applied":
            a: _Applied = item[1]
            await self.journal.mark_applied(a.seq, a.tick, a.state, "(reason dropped)")

    async def drain(self) -> None:
        while self._q and not self.dropped:
            if self.fence is not None and not await self.fence():
                self.drop()
                log.error("village writer: lock lost; dropping pending writes")
                break
            item = self._q.popleft()
            for attempt in range(1, self.MAX_ATTEMPTS + 1):
                try:
                    await self._write(item)
                    break
                except Exception:
                    self.failures += 1
                    log.exception("village writer: %s write failed (attempt %d)", item[0], attempt)
                    if item[0] == "snapshot":        # re-taken on the next cadence
                        break
                    if attempt == self.MAX_ATTEMPTS:
                        log.error("village writer: %s item keeps failing; writing a minimal row", item[0])
                        try:
                            await self._fallback(item)
                            self.fallbacks += 1
                        except Exception:
                            self.abandoned += 1
                            log.error("village writer: ABANDONED a %s item after the fallback "
                                      "failed too", item[0], exc_info=True)
                    else:
                        await asyncio.sleep(self.retry_delay)
        self._idle.set()

    async def run(self) -> None:
        while not self._stopping:
            await self._wake.wait()
            self._wake.clear()
            await self.drain()

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def flush(self) -> None:
        """Wait until everything queued so far is in the DB (close/boot/tests)."""
        if self._task is None:
            await self.drain()
            return
        self._wake.set()
        await self._idle.wait()

    async def stop(self) -> None:
        if not self.dropped:
            await self.flush()
        self._stopping = True
        self._wake.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass


class VillagePruner:
    """Hourly, outside sessions only (§2.1)."""

    def __init__(self, session_factory, *, now: Callable[[], datetime] = utcnow,
                 live: Callable[[], Awaitable[bool]] | None = None):
        self.sf = session_factory
        self._now = now
        self._live = live          # async () -> True when a session is live/closing
        self._last_size_log: datetime | None = None

    async def _clip_windows(self, s) -> list[tuple[str, int, int]]:
        """The ±5 s windows of marks a prompt change points to: kept as long as a change does."""
        marks = (await s.execute(select(VillageMark.session_id, VillageMark.tick).where(
            VillageMark.id.in_(select(VillagePromptChange.mark_id).where(
                VillagePromptChange.mark_id.is_not(None)))))).all()
        return [(sid, t - CLIP_HALF_WINDOW_TICKS, t + CLIP_HALF_WINDOW_TICKS) for sid, t in marks]

    async def prune_once(self) -> dict[str, int]:
        if self._live is not None and await self._live():
            return {}
        now = self._now()
        out: dict[str, int] = {}
        async with self.sf() as s:
            res = await s.execute(delete(VillageSnapshot).where(
                VillageSnapshot.kind == "periodic",
                VillageSnapshot.created_at < now - PERIODIC_SNAPSHOT_KEEP))
            out["snapshots"] = res.rowcount or 0
            cutoff = now - EVENT_COMMAND_KEEP
            keep = [and_(VillageEvent.session_id == sid, VillageEvent.tick >= lo,
                         VillageEvent.tick <= hi) for sid, lo, hi in await self._clip_windows(s)]
            stmt = delete(VillageEvent).where(VillageEvent.created_at < cutoff)
            if keep:
                stmt = stmt.where(not_(or_(*keep)))
            out["events"] = (await s.execute(stmt)).rowcount or 0
            out["commands"] = (await s.execute(delete(VillageCommand).where(
                VillageCommand.created_at < cutoff))).rowcount or 0
            await s.commit()
        if any(out.values()):
            log.info("village prune: %s", out)
        if self._last_size_log is None or now - self._last_size_log >= timedelta(seconds=SIZE_LOG_EVERY_S):
            self._last_size_log = now
            await self.log_sizes()
        return out

    async def log_sizes(self) -> dict[str, int]:
        sizes: dict[str, int] = {}
        async with self.sf() as s:
            for model in (VillageSnapshot, VillageEvent, VillageCommand):
                sizes[model.__tablename__] = (await s.execute(
                    select(func.count()).select_from(model))).scalar() or 0
        log.info("village table rows: %s", sizes)
        return sizes

    async def run_forever(self, interval_seconds: int = PRUNE_EVERY_S) -> None:
        while True:
            try:
                await self.prune_once()
            except Exception:
                log.exception("village prune failed")
            await asyncio.sleep(interval_seconds)
