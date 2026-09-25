"""Versioned, DB-owned typed pages (design/33).

Bindings use a closed, versioned operation registry. Unknown operations cannot
be published, so a page definition cannot enlarge its own authority.
"""
from __future__ import annotations

import re
from datetime import date
from math import isfinite
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from agentplatform.api.auth import READ_ROLES, authenticate, require_admin, role_allows
from agentplatform.db import AppCollection, LiveView, LiveViewVersion, utcnow

router = APIRouter()
_SLUG = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


class TypedBlock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["heading", "paragraph", "metric", "action"]
    text: str = Field(default="", max_length=4000)
    label: str = Field(default="", max_length=128)
    value: str = Field(default="", max_length=256)
    source: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")
    field: Literal["total_km", "runs", "activities", "latest_day"] | None = None
    action_alias: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")


class ReadBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: Literal["running.summary.read@1"]


class ActionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    alias: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    operation: Literal["tickets.create@1"]
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
        aliases = [binding.alias for binding in self.reads]
        if len(set(aliases)) != len(aliases):
            raise ValueError("read aliases must be unique")
        action_aliases = [binding.alias for binding in self.actions]
        if len(set(action_aliases)) != len(action_aliases):
            raise ValueError("action aliases must be unique")
        for block in self.blocks:
            if (block.source is None) != (block.field is None):
                raise ValueError("dynamic metric needs both source and field")
            if block.source is not None and (block.kind != "metric" or block.source not in aliases):
                raise ValueError("dynamic metric must reference a declared read")
            if block.kind == "action" and block.action_alias not in action_aliases:
                raise ValueError("action block must reference a declared action")
            if block.kind != "action" and block.action_alias is not None:
                raise ValueError("only an action block may name an action")
        return self


def _check_app_bindings(app_name: str, definition: TypedDefinition) -> None:
    if definition.reads and app_name != "running":
        raise HTTPException(422, "running reads belong to the Running App")


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
            definition = TypedDefinition.model_validate(published.definition).model_dump()
        except ValidationError:
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
        if view.app_name != "running" or binding is None or binding.operation != "running.summary.read@1":
            raise HTTPException(404, "unknown read")

    upstream = getattr(request.app.state, "app_proxy_base", None) or \
        "http://agent-platform-app-running:8000"
    try:
        async with httpx.AsyncClient(base_url=upstream, timeout=8,
                                     follow_redirects=False) as client:
            response = await client.get("/apps/running/api/summary", headers={
                "X-AP-User": ident[0], "X-AP-Role": "reader"})
        response.raise_for_status()
        if len(response.content) > 262144:
            raise ValueError("oversized summary")
        raw = response.json()
        totals = raw["totals"]
        total_km = float(totals["total_km"])
        runs = int(totals["runs"])
        activities = int(totals["activities"])
        latest_day = raw.get("latest_day")
        if not isfinite(total_km) or total_km < 0 or runs < 0 or activities < 0:
            raise ValueError("invalid totals")
        if latest_day is not None:
            date.fromisoformat(latest_day)
        result = {"total_km": total_km,
                  "runs": runs, "activities": activities,
                  "latest_day": latest_day}
    except (httpx.HTTPError, ValueError, KeyError, TypeError, OverflowError):
        raise HTTPException(502, "Running summary unavailable")
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
            TypedDefinition.model_validate(previous.definition)
        except ValidationError:
            raise HTTPException(409, "version no longer matches typed/v1")
        view.published_version = version
        view.updated_at = utcnow()
        await session.commit()
        return {"id": view_id, "published_version": version}
