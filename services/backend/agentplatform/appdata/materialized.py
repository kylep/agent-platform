"""Scheduled, read-scoped results for reviewed heavy App tool views."""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from agentplatform import maintenance_mode
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import ToolViewDef
from agentplatform.appdata.models import (
    AppDataApp,
    AppDataMaterialization,
    AppDataMaterializeRequest,
)
from agentplatform.appdata.records import field_expr, load_app, scope
from agentplatform.appdata.views import resolve_params
from agentplatform.db import utcnow

log = logging.getLogger("appdata.materialized")
DOMAIN_MAX = 1_000
PER_TICK_MAX = 10


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def result_key(app_id: str, view: str, params: dict) -> str:
    raw = json.dumps([app_id, view, params], sort_keys=True, separators=(",", ":"),
                     default=str).encode()
    return hashlib.sha256(raw).hexdigest()


def _definition_digest(view: ToolViewDef) -> str:
    return hashlib.sha256(json.dumps(view.model_dump(mode="json"), sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


async def read(session, ctx, caller: Caller, view: ToolViewDef, params: dict | None,
               sources: dict[str, str]) -> dict:
    arguments = resolve_params(view, params)
    row = await session.get(AppDataMaterialization,
                            result_key(ctx.app_id, view.view, arguments))
    if row is None:
        raise RecordError("AD-TOOL-VIEW-NOT-READY", "this view has not refreshed yet", 503)
    if row.sources != sources or row.definition_digest != _definition_digest(view):
        raise RecordError("AD-TOOL-VIEW-NOT-READY", "this view changed and needs a refresh", 503)
    for collection in sources.values():
        ctx.access(ctx.collection(collection), caller).require_rows()
    # The refresh ran as system:materializer, which can inspect all fields of
    # its approved sources. A read earns only the intersection of the source
    # fields the run actually touched, evaluated against *current* facts.
    for used in row.read_fields:
        role, sep, field = used.partition(".")
        collection = sources.get(role) if sep else None
        if collection is None:
            raise RecordError("AD-TOOL-VIEW-DISABLED", "materialized source changed", 503)
        ctx.access(ctx.collection(collection), caller).require_readable(field, "read materialized")
    result = dict(row.result)
    period = timedelta(minutes=int(view.materialize.every[:-1]))
    result["stale"] = utcnow() - _aware(row.refreshed_at) > period
    return result


async def request_refresh(session, ctx, view: ToolViewDef, *, now: datetime | None = None) -> bool:
    """Coalesce batch-writer requests to at most one pending wake per minute."""
    if view.materialize is None:
        raise RecordError("AD-NOT-MATERIALIZED", "this view has no refresh schedule", 422)
    now = now or utcnow()
    if session.bind.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    table = AppDataMaterializeRequest.__table__
    stmt = insert(table).values(app_id=ctx.app_id, view=view.view,
                                requested_at=now, consumed_at=None)
    result = await session.execute(stmt.on_conflict_do_update(
        index_elements=[table.c.app_id, table.c.view],
        set_={"requested_at": now, "consumed_at": None},
        where=table.c.requested_at <= now - timedelta(minutes=1)))
    await session.commit()
    return bool(result.rowcount)


async def _domain(session, ctx, view: ToolViewDef) -> list[dict]:
    field = view.materialize.domain
    fact = ctx.bundle.app_tools[view.tool]
    collection = next((fact.roles[role].collection for role in view.sources
                       if role in fact.roles and field in ctx.collection(
                           fact.roles[role].collection).indexed), None)
    if collection is None:
        raise RecordError("AD-TOOL-VIEW-DISABLED", "materialization domain is unavailable", 503)
    c = ctx.collection(collection)
    expr, _ = field_expr(c, field)
    values = (await session.execute(select(expr).where(scope(ctx, collection),
                                                  expr.is_not(None)).distinct()
                                    .order_by(expr).limit(DOMAIN_MAX + 1))).scalars().all()
    if len(values) > DOMAIN_MAX:
        raise RecordError("AD-TOOL-VIEW-DOMAIN", "materialization domain exceeds 1000 values", 413)
    return [{field: value.isoformat() if hasattr(value, "isoformat") else value}
            for value in values]


async def refresh_due(sf, app_state, *, now: datetime | None = None) -> int:
    """One dispatcher tick, bounded so a large restore cannot monopolize it."""
    now = now or utcnow()
    app_state.tool_registry.reload()
    async with sf() as s:
        if not await maintenance_mode.materialization_allowed(s):
            return 0
        app_ids = (await s.execute(select(AppDataApp.id).where(
            AppDataApp.status == "active").order_by(AppDataApp.id))).scalars().all()
    refreshed = 0
    for app_id in app_ids:
        async with sf() as s:
            try:
                ctx = await load_app(s, app_id)
            except RecordError as exc:
                log.warning("skip App %s materializations: %s", app_id, exc)
                continue
            for view in sorted(ctx.bundle.views.values(), key=lambda v: v.view):
                if not isinstance(view, ToolViewDef) or view.materialize is None:
                    continue
                try:
                    from agentplatform.appdata.toolviews import _binding
                    _, _, sources = _binding(ctx, view, app_state)
                    parameter_sets = await _domain(s, ctx, view)
                except RecordError as exc:
                    log.warning("skip %s.%s materialization: %s", app_id, view.view, exc)
                    continue
                request = await s.get(AppDataMaterializeRequest, (app_id, view.view))
                definition_digest = _definition_digest(view)
                for params in parameter_sets:
                    key = result_key(app_id, view.view, params)
                    previous = await s.get(AppDataMaterialization, key)
                    interval = timedelta(minutes=int(view.materialize.every[:-1]))
                    due = (previous is None or previous.sources != sources
                           or previous.definition_digest != definition_digest
                           or now - _aware(previous.refreshed_at) >= interval)
                    if request is not None and (previous is None or
                            _aware(request.requested_at) > _aware(previous.refreshed_at)):
                        due = True
                    if not due:
                        continue
                    if refreshed >= PER_TICK_MAX:
                        return refreshed
                    # A separate session is needed: the executor's scan sees
                    # its credential only after mint commits it.
                    async with sf() as execution:
                        try:
                            from agentplatform.appdata.toolviews import execute
                            used: list[str] = []
                            result = await execute(
                                execution, ctx, Caller("system:materializer", system=True),
                                view, params, app_state, use_cache=False,
                                materializing=True, capture_fields=used)
                        except RecordError as exc:
                            log.warning("materialization %s.%s failed: %s", app_id,
                                        view.view, exc)
                            continue
                    row = await s.get(AppDataMaterialization, key)
                    if row is None:
                        row = AppDataMaterialization(key=key, app_id=app_id,
                                                     view=view.view, params=params,
                                                     sources=sources,
                                                     definition_digest=definition_digest)
                        s.add(row)
                    row.result, row.read_fields, row.refreshed_at = result, sorted(set(used)), now
                    row.sources, row.definition_digest = sources, definition_digest
                    await s.commit()
                    refreshed += 1
                if request is not None:
                    request.consumed_at = now
                    await s.commit()
    return refreshed
