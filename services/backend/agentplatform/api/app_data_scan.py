"""Executor-only, read-only source scans for reviewed App tool views."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from agentplatform.api.auth import authenticate
from agentplatform.appdata import credentials, quotas
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import ViewDef, _check_view, _check_view_alone
from agentplatform.appdata.models import AppDataApp, AppDataToolCall
from agentplatform.appdata.records import load_app
from agentplatform.appdata.views import _Query, _sort_keys, scan_page

router = APIRouter()


class ScanIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(min_length=1, max_length=64)
    fields: list[str] = Field(min_length=1, max_length=64)
    filter: list[dict[str, Any]] = Field(default_factory=list, max_length=10)
    sort: list[dict[str, Any]] = Field(default_factory=list, max_length=3)
    limit: int = Field(default=1000, ge=1, le=1000)
    cursor: str | None = None


async def _view_executor(request: Request) -> dict:
    if await authenticate(request) is None:
        raise HTTPException(401)
    if getattr(request.state, "auth_kind", None) != credentials.KIND_VIEW_EXEC:
        raise HTTPException(403, "a view-execution credential is required")
    return request.state.view_exec


def _web_error(exc: RecordError) -> HTTPException:
    return HTTPException(exc.status, exc.as_dict())


@router.post("/api/app-data/scan")
async def app_data_scan(request: Request, body: ScanIn) -> dict:
    """Read a bounded page through one approved source role, as its viewer.

    Neither a run JWT nor an ordinary tool-call credential can enter. The
    source, current App fact, viewer facts and per-execution allowance all
    remain server-side, so the tool cannot enlarge its own read surface.
    """
    claim = await _view_executor(request)
    collection = claim["sources"].get(body.role)
    if collection is None:
        raise HTTPException(403, {"code": "AD-OUT-OF-SCOPE",
                                  "message": "source role is outside this execution"})
    async with request.app.state.session_factory() as s:
        try:
            app = await s.get(AppDataApp, claim["app_id"])
            if app is None or app.status != "active":
                raise RecordError("AD-APP-UNAVAILABLE", "App is unavailable", 409)
            ctx = await load_app(s, app.id)
            fact = ctx.bundle.app_tools.get(claim["tool"])
            role = fact.roles.get(body.role) if fact else None
            if role is None or role.collection != collection or "read" not in role.verbs:
                raise RecordError("AD-OUT-OF-SCOPE", "source is no longer approved", 403)
            c = ctx.collection(collection)
            digest = hashlib.sha256(json.dumps({
                "fields": body.fields, "filter": body.filter, "sort": body.sort,
            }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:20]
            try:
                view = ViewDef.model_validate({
                    "view": f"scan_{digest}", "collection": collection,
                    "fields": body.fields, "filter": body.filter, "sort": body.sort,
                    "paging": True,
                })
            except ValidationError as exc:
                raise RecordError("AD-SCAN-SPEC", "invalid scan specification", 422,
                                  exc.errors(include_context=False)) from None
            issues = [*_check_view_alone(view, "$.scan"),
                      *_check_view(view, c, "$.scan")]
            if issues:
                raise RecordError("AD-SCAN-SPEC", "invalid scan specification", 422,
                                  [i.as_dict() for i in issues])
            caller = Caller(claim["principal"],
                            system=claim["principal"] == "system:materializer")
            query = _Query(ctx, caller, view, {}, datetime.now(timezone.utc))
            query.check_access()
            for field in body.fields:
                query.access.require_readable(field, "scan")
            used = set(body.fields)
            used.update(item["field"] for item in body.filter)
            used.update(name for name, _ in _sort_keys(view))
            for item in body.filter:
                if item["op"] == "within_last" and item.get("anchor", "now").startswith("max("):
                    used.add(item["anchor"][4:-1])
            # Reserve before scanning. The row lock serializes pages of one
            # credential, including concurrent proxy requests. Reservation is
            # intentionally not refunded after an error: failure is closed.
            row = (await s.execute(select(AppDataToolCall).where(
                AppDataToolCall.jti == claim["jti"]).with_for_update())).scalar_one_or_none()
            now = datetime.now(timezone.utc)
            started = row.created_at if row is not None else now
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if row is None or row.revoked_at is not None or now >= row.expires_at.replace(
                    tzinfo=row.expires_at.tzinfo or timezone.utc):
                raise RecordError("AD-SCAN-EXPIRED", "scan execution has ended", 403)
            if (now - started).total_seconds() >= request.app.state.settings.app_data_scan_max_seconds:
                raise RecordError("AD-QUOTA-SCAN-TIME", "scan execution exceeded its time limit", 413)
            max_rows = request.app.state.settings.app_data_scan_max_rows
            # The execution ceiling counts rows returned to the tool. The
            # query's one-row lookahead is charged to the App's scan lease,
            # but reserving it here would make an exact 1M-row scan stop one
            # page early at the configured 1M-row ceiling.
            reserve = body.limit
            if (row.scan_rows or 0) + reserve > max_rows:
                raise RecordError("AD-QUOTA-SCAN-EXECUTION", "scan execution row limit reached", 413,
                                  {"max": max_rows, "used": row.scan_rows or 0})
            row.scan_rows = (row.scan_rows or 0) + reserve
            row.scan_fields = sorted(set(row.scan_fields or []).union(
                f"{body.role}.{field}" for field in used))
            await s.commit()
            async with quotas.scan_budget(s, ctx) as budget:
                return await scan_page(s, ctx, caller, view, limit=body.limit,
                                       cursor=body.cursor, budget=budget)
        except RecordError as exc:
            raise _web_error(exc) from None
