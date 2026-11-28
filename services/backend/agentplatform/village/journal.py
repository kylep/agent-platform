"""The command journal and boot recovery (plan §3.1).

Every command is committed to `village_commands` BEFORE anything acknowledges
it, then enqueued for the next tick. The tick records `apply_tick` (the world
tick the command was applied at: before that iteration's `step()`). A boot is
"snapshot + deterministic replay": step fixed ticks, apply each journaled
command at its recorded tick, and check the re-simulated state hash against
every heartbeat the previous epoch recorded.
"""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from agentplatform.db import VillageCommand
from agentplatform.village.contract import (HEARTBEAT_EVERY_TICKS, Command, CommandResult,
                                            Event, World, args_hash)
from agentplatform.village.store import VillageStore

log = logging.getLogger("village.journal")


class IdempotencyConflict(Exception):
    """Same (run_id, request_id) as an earlier command, different arguments (§7)."""

    def __init__(self, first: Command):
        super().__init__("request_id was already used with different arguments")
        self.first = first


@dataclass
class Submitted:
    command: Command
    duplicate: bool = False        # same request_id + args: the first receipt, not re-enqueued


def to_command(row: VillageCommand) -> Command:
    return Command(session_id=row.session_id, source=row.source, action=row.action,
                   args=row.args or {}, seq=row.seq, run_id=row.run_id, persona=row.persona,
                   request_id=row.request_id, args_hash=row.args_hash, state=row.state,
                   reason=row.reason, apply_tick=row.apply_tick)


class Journal:
    """`submit` is the only way a command enters the engine."""

    def __init__(self, session_factory, inbox: deque | None = None):
        self.sf = session_factory
        self.inbox: deque[Command] = inbox if inbox is not None else deque()
        # Insert+commit+enqueue are one critical section so enqueue order is
        # seq order: recovery's "seq > last_applied_seq" rule depends on it.
        self._lock = asyncio.Lock()
        self._sent: set[tuple[str, str]] = set()

    async def submit(self, session_id: str, source: str, action: str,
                     args: dict[str, Any] | None = None, *, run_id: str | None = None,
                     persona: str | None = None, request_id: str | None = None,
                     digest: str | None = None,
                     side: Callable[[Any, VillageCommand], None] | None = None) -> Submitted:
        """`digest` overrides the args hash: the hash of the tool call as made,
        when what is journaled is a validated subset of it.

        `side(session, row)` adds side-table rows (a private note) in the SAME
        transaction as the journal row, so both land or neither does. It runs
        only for a new command, never for a duplicate."""
        args = args or {}
        digest = digest or args_hash(args)
        async with self._lock:
            if run_id and request_id:
                prior = await self.find(run_id, request_id)
                if prior is not None:
                    if prior.args_hash != digest:
                        raise IdempotencyConflict(prior)
                    return Submitted(prior, duplicate=True)
            async with self.sf() as s:
                row = VillageCommand(session_id=session_id, source=source, run_id=run_id,
                                     persona=persona, request_id=request_id, args_hash=digest,
                                     action=action, args=args, state="accepted")
                s.add(row)
                try:
                    if side is not None:
                        await s.flush()                # row.seq for the side rows
                        side(s, row)
                    await s.commit()                   # durable before the ack
                except IntegrityError:                  # another writer raced the unique key
                    await s.rollback()
                    prior = await self.find(run_id, request_id)
                    if prior is not None and prior.args_hash == digest:
                        return Submitted(prior, duplicate=True)
                    if prior is None:
                        raise
                    raise IdempotencyConflict(prior)
                cmd = to_command(row)
            self.inbox.append(cmd)
            return Submitted(cmd)

    async def system(self, session_id: str, action: str, args: dict[str, Any] | None = None) -> Command:
        """A clock/engine command (T-10, T-3, T-0, quota close): journaled so replay matches."""
        out = await self.submit(session_id, "system", action, args)
        self._sent.add((session_id, action))
        return out.command

    async def system_sent(self, session_id: str, action: str) -> bool:
        if (session_id, action) in self._sent:
            return True
        async with self.sf() as s:
            n = (await s.execute(select(func.count()).select_from(VillageCommand).where(
                VillageCommand.session_id == session_id, VillageCommand.source == "system",
                VillageCommand.action == action))).scalar()
        if n:
            self._sent.add((session_id, action))
        return bool(n)

    async def find(self, run_id: str | None, request_id: str | None) -> Command | None:
        if not run_id or not request_id:
            return None
        async with self.sf() as s:
            row = (await s.execute(select(VillageCommand).where(
                VillageCommand.run_id == run_id,
                VillageCommand.request_id == request_id))).scalar_one_or_none()
        return to_command(row) if row else None

    async def mark_applied(self, seq: int, tick: int, state: str, reason: str | None = None) -> None:
        async with self.sf() as s:
            await s.execute(update(VillageCommand).where(VillageCommand.seq == seq).values(
                apply_tick=tick, state=state, reason=reason))
            await s.commit()

    async def after(self, seq: int, session_id: str | None = None) -> list[Command]:
        stmt = select(VillageCommand).where(VillageCommand.seq > seq)
        if session_id:
            stmt = stmt.where(VillageCommand.session_id == session_id)
        async with self.sf() as s:
            rows = (await s.execute(stmt.order_by(VillageCommand.seq))).scalars().all()
        return [to_command(r) for r in rows]

    async def max_seq(self) -> int:
        async with self.sf() as s:
            return (await s.execute(select(func.max(VillageCommand.seq)))).scalar() or 0


# --- ticking, shared by the live loop and replay ---------------------------------

def advance(world: World) -> list[Event]:
    """One fixed tick plus the heartbeat. The ONLY place a tick is stepped, so
    the live loop and recovery cannot disagree about heartbeat ticks."""
    events = list(world.step())
    if world.tick % HEARTBEAT_EVERY_TICKS == 0:
        events.append(Event(tick=world.tick, kind="heartbeat",
                            data={"state_hash": world.state_hash()}))
    return events


@dataclass
class Recovery:
    world: World | None = None
    snapshot_tick: int = 0
    last_applied_seq: int = 0
    replayed: int = 0               # journal commands re-applied
    unrecorded: int = 0             # accepted but never marked applied: applied at the end
    ticks: int = 0                  # ticks re-simulated
    events: list[Event] = field(default_factory=list)   # re-emitted under the new epoch
    applied: list[tuple[int, int, CommandResult]] = field(default_factory=list)  # every replayed (seq, tick, result)
    unrecorded_applied: list[tuple[int, int, CommandResult]] = field(default_factory=list)  # the ones to mark
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    superseded: int = 0
    fresh: bool = False             # no snapshot at all: a brand-new world


async def recover(store: VillageStore, journal: Journal, make_world: Callable[[dict | None], World],
                  session_id: str | None, epoch: str) -> Recovery:
    """§3.1 boot steps 1-4. Does not write the re-emitted events (the engine
    pushes `Recovery.events` through its writer so they carry the new epoch)."""
    snap = await store.start_state(session_id)
    rec = Recovery()
    if snap is None:
        rec.world, rec.fresh = make_world(None), True
        return rec
    world = make_world(snap.state)
    rec.world, rec.snapshot_tick, rec.last_applied_seq = world, snap.tick, snap.last_applied_seq
    if session_id is None or snap.session_id != session_id:
        return rec                                   # frozen world between sessions: nothing to replay
    commands = await journal.after(snap.last_applied_seq, session_id)
    heartbeats = await store.heartbeats_after(session_id, snap.tick, epoch)
    seen_ticks = [c.apply_tick for c in commands if c.apply_tick is not None]
    target = max([await store.max_event_tick(session_id, snap.tick, epoch), snap.tick, *seen_ticks])
    rec.superseded = await store.supersede(session_id, snap.tick, epoch)

    def check() -> None:
        want = heartbeats.get(world.tick)
        if want is not None and want != world.state_hash():
            log.error("village recovery: state hash mismatch at tick %d", world.tick)
            rec.mismatches.append({"tick": world.tick, "expected": want, "got": world.state_hash()})

    def apply(cmd: Command) -> None:
        result = world.apply(cmd)
        rec.applied.append((cmd.seq, world.tick, result))
        if cmd.seq is not None and cmd.seq > rec.last_applied_seq:
            rec.last_applied_seq = cmd.seq

    recorded = sorted((c for c in commands if c.apply_tick is not None), key=lambda c: (c.apply_tick, c.seq))
    pending = sorted((c for c in commands if c.apply_tick is None), key=lambda c: c.seq)
    i = 0
    while world.tick < target or i < len(recorded):
        while i < len(recorded) and recorded[i].apply_tick <= world.tick:
            apply(recorded[i])
            rec.replayed += 1
            i += 1
        if world.tick >= target:
            continue                                  # only commands left, all applied above
        rec.events.extend(advance(world))
        rec.ticks += 1
        check()
    for cmd in pending:                              # first tick after the replayed range, in seq order
        apply(cmd)
        rec.unrecorded += 1
        rec.unrecorded_applied.append(rec.applied[-1])
    return rec
