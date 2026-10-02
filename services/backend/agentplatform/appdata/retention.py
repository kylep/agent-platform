"""Retention pruning (design 39, "Collections" → "Retention").

A collection may keep records for `max_age` (`90d`, `12w`, by `created_at`)
or keep only its newest `max_records`. A daily platform job calls
`prune_collection` for each collection that declares one. Pruning deletes
through the same server-computed plan as any delete, so `unlink` refs are
cleared and a record still held by a `restrict` ref is kept, not forced out:
retention never breaks a reference the App said must hold. A kept record is
reconsidered on the next run, once whatever referenced it has gone.

Each chunk is its own transaction, so a large first prune doesn't hold one
long transaction over the whole collection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agentplatform.appdata.access import SYSTEM_RETENTION
from agentplatform.appdata.definitions import retention_days
from agentplatform.appdata.records import (R, AppContext, compute_plan, execute_plan,
                                           scope)
from agentplatform.db import utcnow

CHUNK = 1_000


@dataclass
class PruneResult:
    collection: str
    deleted: int = 0
    unlinked: int = 0
    # Records past retention that a `restrict` ref still holds.
    kept: int = 0

    def as_dict(self) -> dict:
        return {"collection": self.collection, "deleted": self.deleted,
                "unlinked": self.unlinked, "kept": self.kept}


async def _expired_ids(session, ctx: AppContext, collection: str, now: datetime,
                       after: tuple | None) -> list[tuple]:
    """The next chunk of (created_at, id) past retention, oldest first."""
    c = ctx.collection(collection)
    stmt = select(R.created_at, R.id).where(scope(ctx, collection))
    if c.retention.max_age is not None:
        stmt = stmt.where(R.created_at < now - timedelta(days=retention_days(c.retention)))
    else:
        # Everything older than the newest max_records: the cut is the
        # (created_at, id) of the max_records-th newest record.
        cut = (await session.execute(
            select(R.created_at, R.id).where(scope(ctx, collection))
            .order_by(R.created_at.desc(), R.id.desc())
            .offset(c.retention.max_records - 1).limit(1))).first()
        if cut is None:
            return []
        stmt = stmt.where((R.created_at < cut[0])
                          | ((R.created_at == cut[0]) & (R.id < cut[1])))
    if after is not None:
        stmt = stmt.where((R.created_at > after[0])
                          | ((R.created_at == after[0]) & (R.id > after[1])))
    return list((await session.execute(
        stmt.order_by(R.created_at, R.id).limit(CHUNK))).all())


async def prune_collection(session, ctx: AppContext, collection: str, *,
                           now: datetime | None = None) -> PruneResult:
    c = ctx.collection(collection)
    result = PruneResult(collection)
    if c.retention is None:
        return result
    now = (now or utcnow()).astimezone(timezone.utc)
    after = None
    while True:
        rows = await _expired_ids(session, ctx, collection, now, after)
        if not rows:
            break
        after = tuple(rows[-1])
        targets = [(collection, rid) for _, rid in rows]
        plan = await compute_plan(session, ctx, targets)
        # Drop what a restrict ref holds. Dropping a record can't block
        # another (blocks come only from records outside the delete set, and
        # cascade, which could chain, is Release 2), so one pass settles it.
        held = {(target, rid) for target, rid, *_ in plan.blocked}
        if held:
            kept = [t for t in targets if t not in held]
            result.kept += len(targets) - len(kept)
            plan = await compute_plan(session, ctx, kept) if kept else None
        if plan is not None and plan.deletes:
            await execute_plan(session, ctx, SYSTEM_RETENTION, plan)
            result.deleted += len(plan.deletes)
            result.unlinked += len(plan.unlinks)
        await session.commit()
    return result


async def prune_app(session, ctx: AppContext, *, now: datetime | None = None) -> list[dict]:
    return [(await prune_collection(session, ctx, name, now=now)).as_dict()
            for name, c in ctx.bundle.collections.items() if c.retention is not None]
