"""Versioned, DB-owned typed pages (design/33).

Bindings use a closed, versioned operation registry. Unknown operations cannot
be published, so a page definition cannot enlarge its own authority.
"""
from __future__ import annotations

import json
import re
from datetime import date
from math import isfinite
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from agentplatform import operation_catalog
from agentplatform.api.auth import READ_ROLES, authenticate, require_admin, role_allows
from agentplatform.db import (
    AppCollection,
    Conversation,
    LiveView,
    LiveViewVersion,
    RelayMessage,
    RelayParticipant,
    utcnow,
)
from agentplatform.relay import is_member, participant_of

router = APIRouter()
_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
READ_FIELDS = {
    "running.summary.read@1": {"total_km", "runs", "activities", "latest_day"},
    "running.activities.read@1": set(),
    "news.summary.read@1": {"today", "week", "total", "topics", "latest_day"},
    "news.items.read@1": set(),
    "stockmarket.summary.read@1": {
        "indexes", "watchlist", "latest_day", "latest_brief_day"},
    "stockmarket.watchlist.read@1": set(),
    "tcms.overview.read@1": {
        "failing", "flaky", "unlinked", "prune_candidates", "coverage_pct"},
    "tcms.runs.read@1": set(),
    "relay.channel.read@1": set(),
}
READ_APP = {operation: operation.split(".", 1)[0] for operation in READ_FIELDS}
TABLE_FIELDS = {
    "running.activities.read@1": ("day", "name", "type", "distance_km", "pace"),
    "news.items.read@1": ("day", "title", "source", "topic"),
    "stockmarket.watchlist.read@1": ("symbol", "label", "status", "latest_close", "change_pct"),
    "tcms.runs.read@1": ("started_at", "branch", "agent", "n", "verify_ok"),
    "relay.channel.read@1": ("created_at", "author", "body"),
}


class TypedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["heading", "paragraph", "metric", "table", "chat", "action", "link"]
    text: str = Field(default="", max_length=4000)
    label: str = Field(default="", max_length=128)
    value: str = Field(default="", max_length=256)
    source: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    field: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    columns: list[Annotated[str, Field(max_length=40, pattern=r"^[a-z][a-z0-9_]*$")]] = Field(
        default_factory=list, max_length=8)
    action_alias: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    href: str | None = Field(default=None, max_length=128,
                             pattern=r"^/apps/[a-z][a-z0-9-]{0,63}/$")


class ReadBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: str = Field(min_length=1, max_length=128)
    channel_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class ActionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: str = Field(min_length=1, max_length=128)
    channel: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9-]+$")


class TypedDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    renderer: Literal["typed/v1"] = "typed/v1"
    title: str = Field(min_length=1, max_length=128)
    blocks: list[TypedBlock] = Field(default_factory=list, max_length=50)
    reads: list[ReadBinding] = Field(default_factory=list, max_length=10)
    actions: list[ActionBinding] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _references_known_reads(self):
        if any(binding.operation not in READ_FIELDS
               or not operation_catalog.admitted(binding.operation)
               for binding in self.reads):
            raise ValueError("read operation has no admitted page adapter")
        if any((binding.operation == "relay.channel.read@1") != (binding.channel_id is not None)
               for binding in self.reads):
            raise ValueError("Relay read needs a fixed channel; App reads cannot name one")
        if any(binding.operation not in ("tickets.create@1", "relay.channel.post@1")
               or not operation_catalog.admitted(binding.operation)
               for binding in self.actions):
            raise ValueError("action operation has no admitted page adapter")
        aliases = [binding.alias for binding in self.reads]
        if len(set(aliases)) != len(aliases):
            raise ValueError("read aliases must be unique")
        action_aliases = [binding.alias for binding in self.actions]
        if len(set(action_aliases)) != len(action_aliases):
            raise ValueError("action aliases must be unique")
        for block in self.blocks:
            if (block.source is not None
                    and (block.kind not in ("metric", "table", "chat")
                         or block.source not in aliases)):
                raise ValueError("data block must reference a declared read")
            if block.kind == "metric" and block.source is not None:
                if block.field is None:
                    raise ValueError("dynamic metric needs a field")
                operation = next(b.operation for b in self.reads if b.alias == block.source)
                if block.field not in READ_FIELDS[operation]:
                    raise ValueError("metric field is not available from its read")
            elif block.field is not None:
                raise ValueError("only a dynamic metric may name a field")
            if block.kind == "table":
                if block.source is None:
                    raise ValueError("table needs a read source")
                operation = next(b.operation for b in self.reads if b.alias == block.source)
                if operation not in TABLE_FIELDS:
                    raise ValueError("read does not provide a table")
                if (len(set(block.columns)) != len(block.columns)
                        or any(column not in TABLE_FIELDS[operation]
                               for column in block.columns)):
                    raise ValueError("table columns must be unique approved fields")
            elif block.columns:
                raise ValueError("only a table may name columns")
            if block.kind == "chat":
                if block.source is None:
                    raise ValueError("chat needs a Relay read source")
                operation = next(b.operation for b in self.reads if b.alias == block.source)
                if operation != "relay.channel.read@1":
                    raise ValueError("chat can only render a Relay channel read")
            if block.kind == "action" and block.action_alias not in action_aliases:
                raise ValueError("action block must reference a declared action")
            if block.kind != "action" and block.action_alias is not None:
                raise ValueError("only an action block may name an action")
            if (block.kind == "link") != (block.href is not None):
                raise ValueError("link block needs a destination; other blocks cannot have one")
        return self


def _check_app_bindings(app_name: str, definition: TypedDefinition) -> None:
    if any(READ_APP[b.operation] not in (app_name, "relay") for b in definition.reads):
        raise HTTPException(422, "a read must belong to its App")
    if any(block.href != f"/apps/{app_name}/" for block in definition.blocks
           if block.kind == "link"):
        raise HTTPException(422, "a link must open this App's reviewed interface")


class CreateView(BaseModel):
    app_name: str
    slug: str
    definition: TypedDefinition


class ReplaceDraft(BaseModel):
    expected_revision: int = Field(ge=1)
    definition: TypedDefinition


async def _reader(request: Request) -> tuple[str, str]:
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    if getattr(request.state, "api_key_agent", None) is not None:
        raise HTTPException(403, "Live Apps require a human principal")
    if not role_allows(ident[1], READ_ROLES):
        raise HTTPException(403)
    return ident


async def _accessible_app(session, name: str, ident: tuple[str, str]) -> AppCollection:
    app = await session.get(AppCollection, name)
    if app is None or (ident[1] != "admin" and app.owner_id != ident[0]):
        raise HTTPException(404, "unknown app")
    return app


def _view_summary(view: LiveView) -> dict:
    return {"id": view.id, "app_name": view.app_name, "slug": view.slug,
            "published_version": view.published_version}


@router.get("/api/live-operations")
async def list_live_operations(eligible_only: bool = False,
                               actor: str = Depends(require_admin)):
    """Show authors every callable branch and which reviewed adapters may be bound."""
    return operation_catalog.listed(eligible_only=eligible_only)


def _count(value) -> int:
    number = int(value)
    if number < 0:
        raise ValueError("negative count")
    return number


def _day(value) -> str | None:
    if value is not None:
        date.fromisoformat(value)
    return value


def _normalize_read(operation: str, raw: dict) -> dict:
    """Return only reviewed scalar fields; domain responses never flow through."""
    if operation == "running.summary.read@1":
        totals = raw["totals"]
        total_km = float(totals["total_km"])
        if not isfinite(total_km) or total_km < 0:
            raise ValueError("invalid distance")
        return {"total_km": total_km, "runs": _count(totals["runs"]),
                "activities": _count(totals["activities"]),
                "latest_day": _day(raw.get("latest_day"))}
    if operation == "running.activities.read@1":
        if not isinstance(raw, list) or len(raw) > operation_catalog.OPERATIONS[operation]["limits"]["max_rows"]:
            raise ValueError("invalid activity list")
        rows = []
        for item in raw:
            distance = float(item["distance_km"])
            if not isfinite(distance) or distance < 0:
                raise ValueError("invalid activity distance")
            rows.append({"day": _day(item["day"]),
                         "name": str(item["name"])[:160],
                         "type": str(item["type"])[:40],
                         "distance_km": distance,
                         "pace": str(item["pace"])[:30] if item.get("pace") else None})
        return {"rows": rows}
    if operation == "news.items.read@1":
        if not isinstance(raw, list) or len(raw) > operation_catalog.OPERATIONS[operation]["limits"]["max_rows"]:
            raise ValueError("invalid news item list")
        return {"rows": [{"day": _day(item["day"]),
                          "title": str(item["title"])[:240],
                          "source": str(item["source"])[:80],
                          "topic": str(item["topic_label"])[:80]}
                         for item in raw]}
    if operation == "news.summary.read@1":
        return {key: _count(raw[key]) for key in ("today", "week", "total", "topics")} | {
            "latest_day": _day(raw.get("latest_day"))}
    if operation == "stockmarket.summary.read@1":
        if not isinstance(raw["indexes"], list) or not isinstance(raw["watchlist"], list):
            raise ValueError("invalid market lists")
        return {"indexes": len(raw["indexes"]), "watchlist": len(raw["watchlist"]),
                "latest_day": _day(raw.get("latest_day")),
                "latest_brief_day": _day(raw.get("latest_brief_day"))}
    if operation == "stockmarket.watchlist.read@1":
        items = raw["watchlist"]
        if not isinstance(items, list) or len(items) > operation_catalog.OPERATIONS[operation]["limits"]["max_rows"]:
            raise ValueError("invalid watchlist")
        rows = []
        for item in items:
            close = item.get("latest_close")
            change = item.get("change_pct")
            if ((close is not None and not isfinite(float(close)))
                    or (change is not None and not isfinite(float(change)))):
                raise ValueError("invalid market price")
            rows.append({"symbol": str(item["symbol"])[:20],
                         "label": str(item["label"])[:80],
                         "status": str(item["status"])[:32],
                         "latest_close": float(close) if close is not None else None,
                         "change_pct": float(change) if change is not None else None})
        return {"rows": rows}
    if operation == "tcms.overview.read@1":
        attention = raw["attention"]
        pct = float(raw["coverage"]["pct"])
        if not isfinite(pct) or not 0 <= pct <= 100:
            raise ValueError("invalid coverage")
        return {key: _count(attention[key]) for key in (
            "failing", "flaky", "unlinked", "prune_candidates")} | {
            "coverage_pct": pct}
    if operation == "tcms.runs.read@1":
        if not isinstance(raw, list) or len(raw) > operation_catalog.OPERATIONS[operation]["limits"]["max_rows"]:
            raise ValueError("invalid test run list")
        if any(item.get("verify_ok") is not None
               and not isinstance(item.get("verify_ok"), bool) for item in raw):
            raise ValueError("invalid verification result")
        return {"rows": [{"started_at": str(item["started_at"])[:32],
                          "branch": str(item["branch"])[:80],
                          "agent": str(item["agent"])[:80] if item.get("agent") else "",
                          "n": _count(item["n"]),
                          "verify_ok": item["verify_ok"]} for item in raw]}
    raise ValueError("unknown read operation")


async def _read_relay_channel(request: Request, channel_id: str,
                              ident: tuple[str, str]) -> JSONResponse:
    """Read only current member-visible text from one immutable room target.

    This is deliberately stricter than the operator's general Relay view:
    a Live App cannot turn a closed room into a shareable dashboard merely
    because its owner has an admin key. Snapshots are disallowed separately.
    """
    limits = operation_catalog.OPERATIONS["relay.channel.read@1"]["limits"]
    async with request.app.state.session_factory() as session:
        channel = await session.get(Conversation, channel_id)
        if channel is None or channel.archived_at is not None or channel.kind == "dm":
            raise HTTPException(404, "unknown Relay room")
        participant = participant_of(principal=ident[0])
        explicit = set((await session.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == channel.id))).scalars())
        if not is_member(channel, participant, set(), explicit):
            raise HTTPException(404, "unknown Relay room")
        rows = (await session.execute(select(RelayMessage).where(
            RelayMessage.channel_id == channel.id,
            RelayMessage.deleted_at.is_(None), RelayMessage.kind == "text",
        ).order_by(RelayMessage.created_at.desc(), RelayMessage.id.desc())
            .limit(limits["max_rows"]))).scalars().all()
    result = {"rows": [{"created_at": row.created_at.isoformat(),
                        "author": row.author, "body": row.body[:1000]} for row in rows]}
    if len(json.dumps(result, ensure_ascii=False).encode()) > limits["max_output_bytes"]:
        raise HTTPException(502, "Relay response exceeded its page limit")
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


@router.get("/api/live-views")
async def list_live_views(request: Request, app_name: str,
                          ident: tuple[str, str] = Depends(_reader)):
    async with request.app.state.session_factory() as session:
        await _accessible_app(session, app_name, ident)
        rows = (await session.execute(select(LiveView).where(
            LiveView.app_name == app_name).order_by(LiveView.slug))).scalars().all()
        if ident[1] != "admin":
            rows = [row for row in rows if row.published_version is not None]
        return [_view_summary(row) for row in rows]


@router.get("/api/live-views/{view_id}")
async def get_live_view(request: Request, view_id: str,
                        ident: tuple[str, str] = Depends(_reader)):
    async with request.app.state.session_factory() as session:
        view = await session.get(LiveView, view_id)
        if view is None or view.published_version is None:
            raise HTTPException(404, "unknown view")
        await _accessible_app(session, view.app_name, ident)
        published = await session.get(LiveViewVersion, (view.id, view.published_version))
        if published is None:
            raise HTTPException(503, "published version unavailable")
        try:
            parsed = TypedDefinition.model_validate(published.definition)
            _check_app_bindings(view.app_name, parsed)
            definition = parsed.model_dump()
        except (ValidationError, HTTPException):
            raise HTTPException(503, "published definition incompatible")
        return {**_view_summary(view), "definition": definition}


@router.get("/api/live-views/{view_id}/data/{alias}")
async def read_live_view_data(request: Request, view_id: str, alias: str,
                              ident: tuple[str, str] = Depends(_reader)):
    """One bounded read operation, resolved from the published version only."""
    async with request.app.state.session_factory() as session:
        view = await session.get(LiveView, view_id)
        if view is None or view.published_version is None:
            raise HTTPException(404, "unknown view")
        await _accessible_app(session, view.app_name, ident)
        published = await session.get(LiveViewVersion, (view_id, view.published_version))
        if published is None:
            raise HTTPException(503, "published version unavailable")
        try:
            definition = TypedDefinition.model_validate(published.definition)
        except ValidationError:
            raise HTTPException(503, "published definition incompatible")
        binding = next((b for b in definition.reads if b.alias == alias), None)
        if binding is None or READ_APP[binding.operation] not in (view.app_name, "relay"):
            raise HTTPException(404, "unknown read")

    if binding.operation == "relay.channel.read@1":
        return await _read_relay_channel(request, binding.channel_id, ident)

    app_name = view.app_name
    upstream = (getattr(request.app.state, "app_proxy_base", None)
                if app_name == "running" else None) or \
        f"http://agent-platform-app-{app_name}:8000"
    limits = operation_catalog.OPERATIONS[binding.operation]["limits"]
    row_limit = limits["max_rows"]
    endpoint = (f"activities?limit={row_limit}" if binding.operation == "running.activities.read@1"
                else f"items?limit={row_limit}" if binding.operation == "news.items.read@1"
                else f"runs?limit={row_limit}" if binding.operation == "tcms.runs.read@1"
                else "overview" if app_name == "tcms" else "summary")
    try:
        async with httpx.AsyncClient(base_url=upstream, timeout=limits["timeout_seconds"],
                                     follow_redirects=False) as client:
            response = await client.get(f"/apps/{app_name}/api/{endpoint}", headers={
                "X-AP-User": ident[0], "X-AP-Role": "reader"})
        response.raise_for_status()
        if len(response.content) > limits["max_upstream_bytes"]:
            raise ValueError("oversized summary")
        result = _normalize_read(binding.operation, response.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        raise HTTPException(502, f"{app_name} summary unavailable")
    return JSONResponse(result, headers={"Cache-Control": "private, no-store"})


@router.get("/api/live-views/{view_id}/draft")
async def get_live_view_draft(request: Request, view_id: str,
                              principal: str = Depends(require_admin)):
    async with request.app.state.session_factory() as session:
        view = await session.get(LiveView, view_id)
        if view is None:
            raise HTTPException(404, "unknown view")
        return {**_view_summary(view), "draft_revision": view.draft_revision,
                "definition": view.draft}


@router.get("/api/live-views/{view_id}/versions")
async def list_live_view_versions(request: Request, view_id: str,
                                  principal: str = Depends(require_admin)):
    async with request.app.state.session_factory() as session:
        view = await session.get(LiveView, view_id)
        if view is None:
            raise HTTPException(404, "unknown view")
        versions = (await session.execute(select(LiveViewVersion).where(
            LiveViewVersion.view_id == view_id).order_by(
                LiveViewVersion.version.desc()))).scalars().all()
        return [{"version": item.version, "published_at": item.published_at,
                 "published_by": item.published_by,
                 "current": item.version == view.published_version}
                for item in versions]


@router.post("/api/live-views", status_code=201)
async def create_live_view(request: Request, body: CreateView,
                           principal: str = Depends(require_admin)):
    if not _SLUG.fullmatch(body.slug):
        raise HTTPException(422, "view slug must be lowercase letters, digits and hyphens")
    _check_app_bindings(body.app_name, body.definition)
    async with request.app.state.session_factory() as session:
        await _accessible_app(session, body.app_name, (principal, "admin"))
        view = LiveView(app_name=body.app_name, slug=body.slug,
                        draft=body.definition.model_dump(), created_by=principal)
        session.add(view)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "view slug already exists")
        return {"id": view.id, "draft_revision": view.draft_revision}


@router.put("/api/live-views/{view_id}/draft")
async def replace_live_view_draft(request: Request, view_id: str, body: ReplaceDraft,
                                  principal: str = Depends(require_admin)):
    async with request.app.state.session_factory() as session:
        view = await session.get(LiveView, view_id)
        if view is None:
            raise HTTPException(404, "unknown view")
        _check_app_bindings(view.app_name, body.definition)
        result = await session.execute(update(LiveView).where(
            LiveView.id == view_id,
            LiveView.draft_revision == body.expected_revision,
        ).values(draft=body.definition.model_dump(),
                 draft_revision=LiveView.draft_revision + 1,
                 updated_at=utcnow()))
        if result.rowcount != 1:
            if await session.get(LiveView, view_id) is None:
                raise HTTPException(404, "unknown view")
            raise HTTPException(409, "draft changed; reload before saving")
        await session.commit()
        return {"id": view_id, "draft_revision": body.expected_revision + 1}


@router.post("/api/live-views/{view_id}/publish")
async def publish_live_view(request: Request, view_id: str,
                            principal: str = Depends(require_admin)):
    async with request.app.state.session_factory() as session:
        view = (await session.execute(select(LiveView).where(
            LiveView.id == view_id).with_for_update())).scalar_one_or_none()
        if view is None:
            raise HTTPException(404, "unknown view")
        # Revalidate persisted JSON: direct DB edits and old code must not
        # smuggle active content into a published version.
        try:
            parsed = TypedDefinition.model_validate(view.draft)
        except ValidationError:
            raise HTTPException(422, "draft no longer matches typed/v1")
        _check_app_bindings(view.app_name, parsed)
        definition = parsed.model_dump()
        last = (await session.execute(select(func.max(LiveViewVersion.version)).where(
            LiveViewVersion.view_id == view_id))).scalar() or 0
        version = last + 1
        session.add(LiveViewVersion(view_id=view_id, version=version,
                                    definition=definition, published_by=principal))
        view.published_version = version
        view.updated_at = utcnow()
        await session.commit()
        return {"id": view_id, "published_version": version}


@router.post("/api/live-views/{view_id}/rollback/{version}")
async def rollback_live_view(request: Request, view_id: str, version: int,
                             principal: str = Depends(require_admin)):
    async with request.app.state.session_factory() as session:
        view = (await session.execute(select(LiveView).where(
            LiveView.id == view_id).with_for_update())).scalar_one_or_none()
        previous = await session.get(LiveViewVersion, (view_id, version))
        if view is None or previous is None:
            raise HTTPException(404, "unknown version")
        try:
            _check_app_bindings(view.app_name,
                                TypedDefinition.model_validate(previous.definition))
        except (ValidationError, HTTPException):
            raise HTTPException(409, "version no longer matches typed/v1")
        view.published_version = version
        view.updated_at = utcnow()
        await session.commit()
        return {"id": view_id, "published_version": version}
