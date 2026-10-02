"""Bounded, durable cache for viewer-scoped tool-view results."""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import func, select, text

from agentplatform.appdata.models import AppDataViewCache
from agentplatform.appdata.records import write_counter
from agentplatform.db import utcnow


async def key(session, ctx, caller, view, params: dict, sources: dict[str, str]) -> str:
    counters = {collection: await write_counter(session, ctx.app_id, collection)
                for collection in sorted(set(sources.values()))}
    payload = {
        "app": ctx.app_id, "view": view.view, "principal": caller.principal,
        "approved_version": ctx.approved_version,
        "authority_generation": ctx.authority_generation,
        # Preview bypasses this cache, but binding content belongs in the key
        # too: a restored database might reuse a version with different code.
        "definition": view.model_dump(mode="json"),
        "params": params, "source_counters": counters,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


async def get(session, digest: str) -> dict | None:
    row = await session.get(AppDataViewCache, digest)
    if row is None:
        return None
    row.accessed_at = utcnow()
    result = row.result
    await session.commit()
    return result


async def put(session, digest: str, ctx, caller, view, result: dict,
              *, max_bytes: int) -> None:
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"),
                         default=str).encode()
    if len(encoded) > max_bytes:
        return
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
        # One platform-wide cache cap, even with several API replicas.
        await session.execute(text("SELECT pg_advisory_xact_lock(4573491181320)"))
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = AppDataViewCache.__table__
    now = utcnow()
    await session.execute(insert(table).values(
        key=digest, app_id=ctx.app_id, view=view.view,
        principal=caller.principal, result=result, bytes=len(encoded),
        created_at=now, accessed_at=now).on_conflict_do_update(
            index_elements=[table.c.key],
            set_={"result": result, "bytes": len(encoded), "accessed_at": now}))
    total = int((await session.execute(select(func.coalesce(func.sum(
        AppDataViewCache.bytes), 0)))).scalar_one())
    if total > max_bytes:
        oldest = (await session.execute(select(AppDataViewCache).order_by(
            AppDataViewCache.accessed_at, AppDataViewCache.key))).scalars().all()
        for row in oldest:
            if total <= max_bytes:
                break
            total -= row.bytes
            await session.delete(row)
    await session.commit()
