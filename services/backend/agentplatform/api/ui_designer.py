"""The UI Designer's REST surface (docs/design/46 §6, §9).

Every route takes `require_ui_designer_access` — that is the whole rule for who
reaches a document; the admin-only reclaim routes add a browser-session check.
Phase 0 routes: list, create, get, outline, edit, place, history, restore,
render/svg/resolved, assets, delete (soft and hard), prune, asset GC. Comments,
export, events and imports are later phases. What this module owns:

- WHO is acting: `caller.participant`, resolved from the token by
  `require_relay_access`; there is no author on the wire. An agent writes only
  from its own Run (`write=True` on the dependency), so a run-less agent key
  reads and writes nothing.
- WHO may reach a document at all: Kyle's session and human keys by role, an
  agent only when its frozen grants hold `ui_designer` — for reads too, so the
  messenger grant does not open the designer (the wiki's fence, for the wiki's
  reason).
- HOW an image gets in: `place`, the one door (§9). Copy-on-place: the bytes
  are authorized once here and from then on the document is self-contained.
- WHERE pictures go: `snapshot` on `edit` is refused for any caller without a
  run (`UD-NO-IMAGES-HERE`). The facade's admin bearer and API keys get text;
  images go to agent runs, people use the web UI.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy import func, select, text
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from agentplatform import artifact_store
from agentplatform.agentspec import TOOL_ARTIFACTS, TOOL_UI_DESIGNER
from agentplatform.api import agents as agents_api
from agentplatform.api import relay as relay_api
from agentplatform.api import schemas as S
from agentplatform.api.artifacts import _authorize_read, _bounded_body, _may_modify
from agentplatform.api.auth import require_admin, require_browser_session
from agentplatform.api.relay import READ, WRITE, Caller, require_relay_access
from agentplatform.api.tickets import _run_of
from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.db import Conversation, Run, UiDesignerAsset, utcnow
from agentplatform.events import TOPIC_UI_DESIGNER_EVENTS
from agentplatform.relay import SYSTEM_AUTHOR, is_open_channel, room_home
from agentplatform.relay_feed import OVERFLOW, TopicFeed
from agentplatform.relay_store import (channel_by_name, outbound_for_message,
                                       post_relay_message, publish_relay_message)
from agentplatform.tickets import one_line, participant_label
from agentplatform.api.auth import still_signed_in, stream_cookie
from agentplatform.ui_designer import errors as ud_errors
from agentplatform.ui_designer import render_client, store, views, access
from agentplatform.ui_designer.errors import UiDesignerError, invalid, issue
from agentplatform.ui_designer.layout import iter_nodes
from agentplatform.ui_designer.render_client import RenderClient
from agentplatform.ui_designer.schema import image_assets, lint
from agentplatform.ui_designer import svg as ud_svg



class _BoundedRoute(APIRoute):
    """Every write body is read under `ui_designer_max_body_bytes` before the
    JSON parser sees it (create/edit would otherwise buffer anything sent)."""

    def get_route_handler(self):
        inner = super().get_route_handler()

        async def handler(request: Request):
            if request.method in ("POST", "PUT", "PATCH"):
                bound = ((2 << 10) if "/presence/{client_session}" in self.path else
                         (2 << 20) if request.url.path.endswith("/fonts") else
                         request.app.state.settings.ui_designer_max_body_bytes)
                try:
                    original_request = request
                    request = await _bounded_body(request, bound)
                    if request.url.path.endswith(("/motion/preview", "/motion/export")):
                        # The bounded-body replay ends with a synthetic disconnect;
                        # motion cancellation must watch the real ASGI connection.
                        request.state.ui_motion_connection = original_request
                except HTTPException as e:
                    if e.status_code != 413:
                        raise
                    raise ud_http(UiDesignerError(
                        ud_errors.UD_BODY_TOO_LARGE, f"a request body is at most {bound} bytes",
                        detail={"limit": bound})) from e
            try:
                response = await inner(request)
            except UiDesignerError as error:
                raise ud_http(error)
            caller = getattr(request.state, "ui_acl_caller", None)
            slug = request.path_params.get("slug")
            if caller is not None and slug and request.method != "DELETE":
                # A slow renderer cannot return private bytes after revocation.
                writing = getattr(request.state, "ui_acl_write", False)
                fresh = await require_ui_designer_access(*(WRITE if writing else READ), write=writing)(request)
                if fresh.participant != caller.participant:
                    raise HTTPException(403, "caller identity changed")
            asset_document = getattr(request.state, "ui_asset_document_id", None)
            if caller is not None and asset_document is not None:
                who = await access.subject(request, caller)
                async with request.app.state.session_factory() as session:
                    await access.require_read_by_uuid(session, asset_document, who, allow_deleted_owner=False)
            if caller is not None and not request.url.path.endswith("/events"):
                response.headers["Cache-Control"] = "private, no-store"
            return response
        return handler


router = APIRouter(route_class=_BoundedRoute)

PREFIX = "/api/ui-designer"
NOT_GRANTED = "this agent is not granted the ui_designer tool"

UD_NO_IMAGES_HERE = ud_errors.UD_NO_IMAGES_HERE

# UiDesignerError code -> HTTP status. Anything unlisted is a 422: the caller
# sent something the document rules refuse.
_STATUS = {
    store.UD_NOT_FOUND: 404,
    store.UD_SLUG_TAKEN: 409,
    store.UD_REQUEST_REUSED: 409,
    store.UD_BUDGET_STORAGE: 507,
    ud_errors.UD_VERSION_CONFLICT: 409,
    ud_errors.UD_RENDER_FAILED: 502,
    ud_errors.UD_IMAGE_TOO_LARGE: 413,
    ud_errors.UD_BODY_TOO_LARGE: 413,
    ud_errors.UD_BUDGET_WRITES: 429,
    ud_errors.UD_BUDGET_PIXELS: 429,
    ud_errors.UD_FORBIDDEN: 403,
    UD_NO_IMAGES_HERE: 403,
}


def ud_http(e: UiDesignerError) -> HTTPException:
    """A store refusal as the HTTP answer: the stable code, message and fix in
    the body, which is what an agent acts on. A budget refusal also carries
    `Retry-After`."""
    headers = None
    if e.detail.get("retry_after") is not None:
        headers = {"Retry-After": str(int(e.detail["retry_after"]))}
    return HTTPException(_STATUS.get(e.code, 422), e.as_dict(), headers=headers)


# --- settings, renderer, budgets ------------------------------------------------

def caps(request: Request) -> store.Caps:
    cfg = request.app.state.settings
    return store.Caps(max_total_bytes=cfg.ui_designer_max_total_bytes,
                      max_version_bytes_per_doc=cfg.ui_designer_max_version_bytes_per_doc)


def _text_cap(request: Request) -> int:
    return request.app.state.settings.ui_designer_text_cap_bytes


def _renderer(request: Request) -> RenderClient:
    """The renderer client, built on first use from `ui_designer_renderer_url`
    (a test sets `app.state.ui_designer_renderer`)."""
    st = request.app.state
    r = getattr(st, "ui_designer_renderer", None)
    if r is None:
        cfg = st.settings
        if not cfg.ui_designer_renderer_url:
            raise ud_http(UiDesignerError(ud_errors.UD_RENDER_FAILED, "no renderer is configured"))
        r = RenderClient(cfg.ui_designer_renderer_url, timeout_s=cfg.ui_designer_renderer_timeout_s,
                         cache_max_bytes=cfg.ui_designer_render_cache_mb << 20)
        st.ui_designer_renderer = r
    return r


def _scope(caller: Caller, run_id: str | None) -> str:
    """The receipt scope: the run for an agent, the participant for a human."""
    return run_id or caller.participant


def _session_admin(request: Request, caller: Caller) -> None:
    """Kyle's reclaim paths (hard delete, prune, asset GC): the admin signed in
    through the browser, never an agent and never a key."""
    if caller.agent is not None:
        raise ud_http(UiDesignerError(ud_errors.UD_FORBIDDEN, "this action is for the administrator's session"))
    require_browser_session(request)


async def _check_write_budget(request: Request, caller: Caller) -> None:
    """Agents only: `ui_designer_writes_per_hour` accepted writes per rolling
    hour, counted from the stored chunks (folded rows included)."""
    limit = request.app.state.settings.ui_designer_writes_per_hour
    if caller.agent is None or limit <= 0:
        return
    now = datetime.now(timezone.utc)
    async with request.app.state.session_factory() as s:
        times = await store.write_times(s, caller.participant, now - timedelta(hours=1))
    if len(times) >= limit:
        retry = max(1, int((times[len(times) - limit] + timedelta(hours=1) - now).total_seconds()) + 1)
        raise ud_http(UiDesignerError(
            ud_errors.UD_BUDGET_WRITES, f"{len(times)} writes in the last hour; the limit is {limit}",
            detail={"retry_after": retry, "limit": limit}))


def _pixel_window(request: Request, agent: str) -> deque:
    st = request.app.state
    if getattr(st, "ui_designer_pixels", None) is None:
        st.ui_designer_pixels = {}
    return st.ui_designer_pixels.setdefault(agent, deque())


def _check_card_budget(request: Request, caller: Caller) -> None:
    """Agents only: label, resolve and export (the actions that announce
    themselves in `#design` or flip shared state without a version) per rolling
    hour, in memory like the pixel window."""
    limit = request.app.state.settings.ui_designer_cards_per_hour
    if caller.agent is None or limit <= 0:
        return
    st = request.app.state
    if getattr(st, "ui_designer_cards", None) is None:
        st.ui_designer_cards = {}
    win = st.ui_designer_cards.setdefault(caller.agent, deque())
    now = time.monotonic()
    while win and now - win[0] > 3600:
        win.popleft()
    if len(win) >= limit:
        raise ud_http(UiDesignerError(
            ud_errors.UD_BUDGET_WRITES, f"{len(win)} labels, resolves and exports in the last hour; "
            f"the limit is {limit}",
            detail={"retry_after": max(1, int(win[0] + 3600 - now) + 1), "limit": limit}))
    win.append(now)


def _card_text(text: str, limit: int) -> str:
    """Somebody's words inside a system-voiced `#design` card: one line, and no
    chip or mention syntax that would render as the platform's own."""
    out = re.sub(r"\[(?=\[)", "[\u200b", one_line(text, limit))
    return re.sub(r"\](?=\])", "]\u200b", out).replace("@", "@\u200b")


def _check_pixel_budget(request: Request, caller: Caller) -> None:
    """Agents only: rendered megapixels per rolling hour. Kept in memory (one
    API replica); a restart forgives a loop, which the write budget still caps."""
    budget = request.app.state.settings.ui_designer_pixels_per_hour_mp
    if caller.agent is None or budget <= 0:
        return
    win = _pixel_window(request, caller.agent)
    now = time.monotonic()
    while win and now - win[0][0] > 3600:
        win.popleft()
    used = sum(mp for _t, mp in win)
    if used >= budget:
        retry = max(1, int(win[0][0] + 3600 - now) + 1)
        raise ud_http(UiDesignerError(
            ud_errors.UD_BUDGET_PIXELS, f"{used:.0f} megapixels rendered in the last hour; the "
            f"limit is {budget:g}", detail={"retry_after": retry, "limit_mp": budget}))


def _charge_pixels(request: Request, caller: Caller, images) -> float:
    mp = sum(i.width * i.height for i in images) / 1e6
    if caller.agent is not None:
        _pixel_window(request, caller.agent).append((time.monotonic(), mp))
    return mp


def _check_scale(scale: float) -> None:
    if scale not in (0.5, 1, 2, 3):
        raise ud_http(invalid([issue("UD-VALUE", "$.scale", "scale is one of 0.5, 1, 2, 3", scale)]))


def _snapshot_nodes(request: Request, snapshot: Any, scale: float) -> list[str | None]:
    if not snapshot:
        return []
    nodes: list[str | None] = [None] if snapshot is True else list(snapshot)
    cfg = request.app.state.settings
    if len(nodes) > cfg.ui_designer_max_images_per_request:
        raise ud_http(invalid([issue("UD-LIMIT", "$.snapshot", "at most "
                                     f"{cfg.ui_designer_max_images_per_request} images per request")]))
    _check_scale(scale)
    return nodes


def _check_megapixels(request: Request, images) -> None:
    cap = request.app.state.settings.ui_designer_max_megapixels_per_request
    mp = sum(i.width * i.height for i in images) / 1e6
    if mp > cap:
        raise UiDesignerError(ud_errors.UD_IMAGE_TOO_LARGE,
                              f"the images total {mp:.1f} megapixels; the limit is {cap:g}",
                              detail={"megapixels": round(mp, 2)})


# --- the seam ------------------------------------------------------------------

def require_ui_designer_access(*roles: str, write: bool = False):
    """`require_relay_access`, plus the grant this door is behind and, on a
    write, the Run an agent must be speaking from.

    Humans are judged by role (Kyle's session, the admin bearer, human keys).
    An agent is judged by its grant: `_caller_platform_tools` and not the row,
    because a run JWT froze its grants at launch and a grant added mid-run must
    not widen the run already using its token. The `relay` role alone opens
    nothing here. Disabled and quarantined agents are refused. A run-less agent
    key (or one whose run has gone) may read and may not write: every version
    is stamped with the run that wrote it."""
    inner = require_relay_access(*roles)

    async def dep(request: Request) -> Caller:
        caller = await inner(request)
        if caller.agent is not None:
            granted = await agents_api._caller_platform_tools(request, caller.agent)
            if caller.agent not in relay_api._agent_set(request):
                raise HTTPException(403, "unknown or disabled agent")
            if TOOL_UI_DESIGNER not in granted:
                raise HTTPException(403, NOT_GRANTED)
            if write:
                async with request.app.state.session_factory() as s:
                    await _run_of(s, request, caller, writing=True)
        request.state.ui_acl_caller = caller
        request.state.ui_acl_write = write
        slug = request.path_params.get("slug")
        if slug and not (request.method == "DELETE" and caller.agent is None and
                         getattr(request.state, "resolved_role", None) == "admin"):
            await _acl_check(request, caller, slug, write=write)
        return caller

    return dep


async def _acl_check(request: Request, caller: Caller, slug: str, *, write=False):
    async with request.app.state.session_factory() as session:
        who = await access.subject_for(session, caller.participant,
            is_admin=getattr(request.state, "resolved_role", None) == "admin" and caller.agent is None)
        try:
            row = await store._live(session, slug)
        except UiDesignerError as error:
            raise ud_http(error)
        if write:
            await access.require_write_by_uuid(session, row.id, who)
        else:
            await access.require_read_by_uuid(session, row.id, who, allow_deleted_owner=False)
    pinned = getattr(request.state, "ui_acl_document_id", None)
    if pinned is not None and pinned != row.id:
        raise HTTPException(404, "design document identity changed")
    request.state.ui_acl_document_id = row.id
    return who


async def _acl_lock(request: Request, caller: Caller, session, slug: str, *, write=True):
    fresh = await require_ui_designer_access(*(WRITE if write else READ), write=write)(request)
    if fresh.participant != caller.participant:
        raise HTTPException(403, "caller identity changed")
    try:
        row = await store._live(session, slug, lock=True)
    except UiDesignerError as error:
        raise ud_http(error)
    who = await access.subject_for(session, caller.participant,
        is_admin=getattr(request.state, "resolved_role", None) == "admin" and caller.agent is None)
    if write:
        await access.require_write_by_uuid(session, row.id, who)
    else:
        await access.require_read_by_uuid(session, row.id, who, allow_deleted_owner=False)
    return row


async def caller_run_id(request: Request, caller: Caller) -> str | None:
    """The run an agent caller acts from (None for a human, or a run-less
    agent key on a read). Routes stamp it on versions, receipts, placements."""
    if caller.agent is None:
        return None
    async with request.app.state.session_factory() as s:
        run = await _run_of(s, request, caller, writing=False)
        return run.id if run is not None else None


async def refuse_runless_snapshot(request: Request, caller: Caller, snapshot: Any) -> None:
    """§6: `snapshot` cannot be excluded from the facade by path, so the API
    refuses it for any caller without a run — Kyle's session, the facade's
    admin bearer, any API key. The `edit` route calls this before anything
    else touches the document."""
    if not snapshot:
        return
    if await caller_run_id(request, caller) is None:
        raise ud_http(UiDesignerError(UD_NO_IMAGES_HERE,
                                      "images go to agent runs; use the web UI"))


# --- place (copy on place, §9) -------------------------------------------------

async def _run_is_restricted(s, run: Run) -> bool:
    """Whether reading this run, or any run in its `parent_run_id` chain, is
    restricted. A child spawned from a restricted run carries none of its
    parent's restriction fields but may hold what the parent saw, so the whole
    chain is judged. The walk is bounded by `max_run_chain_depth`; a missing
    ancestor, a cycle, or a chain past the bound counts as restricted."""
    from agentplatform.config import get_settings
    bound = get_settings().max_run_chain_depth
    seen: set[str] = set()
    current: Run | None = run
    for _ in range(bound + 1):
        if current.id in seen:
            return True
        seen.add(current.id)
        if await _run_itself_restricted(s, current):
            return True
        if not current.parent_run_id:
            return False
        current = await s.get(Run, current.parent_run_id)
        if current is None:
            return True
    return True


async def _run_itself_restricted(s, run: Run) -> bool:
    """Whether reading this one run is restricted for ANYONE: the cases
    `authority.assert_readable_run` checks — a startup-pilot run, a local
    persona session, a run in a room that is not an open Relay channel. A
    document is readable by every holder of the tool, so an artifact from such
    a run would widen its readership; the caller's own access does not matter.
    A room that has gone counts as restricted (we cannot say who it was for)."""
    if getattr(run, "startup_memory_enabled", False):
        return True
    if getattr(run, "local_persona_session_id", None):
        return True
    if run.conversation_id:
        room = await s.get(Conversation, run.conversation_id)
        if room is None or room_home(room) != "relay" or not is_open_channel(room):
            return True
    return False


@router.post(PREFIX + "/docs/{slug}/place", response_model=S.UiDocPlaceOut)
async def place_ui_asset(slug: str, body: S.UiDocPlaceIn, request: Request,
                caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    """Copy an image artifact into the document's assets and record the
    placement. The node then names the sha256; `insert`/`replace`/`set_doc`
    accept only placed assets (UD-ASSET-UNKNOWN)."""
    if caller.agent is not None:
        # `_authorize_read` alone is not enough: `artifacts_granted` defaults to
        # True when `require_artifacts_access` did not run, which it did not.
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if TOOL_ARTIFACTS not in granted:
            raise HTTPException(403, "placing an image needs the artifacts tool")
    request.state.artifacts_granted = True
    async with request.app.state.session_factory() as s:
        art = await artifact_store.get(s, body.artifact_id)
        # App-owned: refused outright and indistinguishable from missing (the
        # avatar rule) — its readership is its App's, never a document's.
        if art is None or await app_artifacts.ownership(s, art.id) is not None:
            raise HTTPException(404, "unknown artifact")
        await _authorize_read(s, request, art)
        if art.kind != "image" or art.mime not in artifact_store.RASTERS:
            raise HTTPException(422, "only a raster image artifact can be placed")
        if art.run_id:
            run = await s.get(Run, art.run_id)
            if run is None or await _run_is_restricted(s, run):
                raise HTTPException(403, "this artifact comes from a run whose history is "
                                         "restricted; it cannot be placed in a shared document")
        elif caller.agent is not None or art.owner != caller.participant:
            # A run-less artifact is a human's upload: only its uploader places
            # it. This is what bounds the admin — an admin reads everything, and
            # a document must not become a way to show the rest to every holder.
            raise HTTPException(403, "an artifact without a run can be placed only by "
                                     "the person who uploaded it")
        data = await artifact_store.content(s, art.id)
        if data is None:
            raise HTTPException(404, "unknown artifact")
        try:
            await _acl_lock(request, caller, s, slug)
            out = await store.place_asset(s, slug, data=data, mime=art.mime,
                                          placed_by=caller.participant,
                                          placed_from=f"artifact:{art.id}",
                                          width=art.width, height=art.height, caps=caps(request))
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    return out



# --- events and the #design room ---------------------------------------------------

log = logging.getLogger("ui_designer")
STREAM = "ui_designer"
STREAM_RECHECK_SECONDS = 60.0   # module global: a test turns it down
DESIGN_CHANNEL = "design"


class _DesignerFeed(TopicFeed):
    async def _on_message(self, msg, data: dict) -> None:
        await super()._on_message(msg, data)
        if (msg.topic == TOPIC_UI_DESIGNER_EVENTS and data.get("type") in ("access", "deleted")
                and isinstance(data.get("document_id"), str)):
            self.publish("presence:" + data["document_id"], "design", data)


def ui_designer_feed(session_factory=None) -> TopicFeed:
    """Presence has a per-UUID fan-out; chip subscribers never see cursors."""
    def frame(data):
        if data.get("type") == "presence":
            identifier = data.get("document_id")
            return ("presence:" + identifier, data) if isinstance(identifier, str) else None
        return (STREAM, data) if data.get("slug") else None
    return _DesignerFeed(TOPIC_UI_DESIGNER_EVENTS, event="design", frame_of=frame,
                         session_factory=session_factory)


async def _emit(request: Request, caller: Caller, type_: str, slug: str, version: int,
                run_id: str | None, **extra: Any) -> None:
    """Best-effort, after the commit: the row is the record, a broker that is down
    costs the live view an update and never the write."""
    producer = getattr(request.app.state, "producer", None)
    if producer is None:
        return
    try:
        await producer.publish(TOPIC_UI_DESIGNER_EVENTS, slug,
                               {"type": type_, "slug": slug, "version": version,
                                "author": caller.participant, "run_id": run_id,
                                "document_id": getattr(request.state, "ui_acl_document_id", None), **extra},
                               type="ui_designer.event")
    except Exception:
        log.warning("ui_designer.events publish failed for %s %s", slug, type_, exc_info=True)


async def _design_card(request: Request, *, body: str, card: dict) -> bool:
    """A card in `#design`. An EVENT row, so a frame titled "@all look" summons
    nobody (the router reads mentions only from text rows). A missing room costs
    the audience only, never the response."""
    try:
        async with request.app.state.session_factory() as s:
            from agentplatform.db import UiDesignerDocument
            doc = (await s.execute(store._for_update(s, select(UiDesignerDocument).where(
                UiDesignerDocument.id == card.get("document_id"),
                UiDesignerDocument.slug == card.get("slug"), UiDesignerDocument.deleted_at.is_(None))))).scalars().first()
            if doc is None or not await access.is_workspace(s, doc.id):
                return False
            conv = await channel_by_name(s, DESIGN_CHANNEL)
            if conv is None:
                log.warning("no live #%s channel; no card", DESIGN_CHANNEL)
                return False
            msg = await post_relay_message(s, conv, author=SYSTEM_AUTHOR, kind="event",
                                           mentions=[], body=body, card=card)
            await s.commit()
            outbound = await outbound_for_message(s, conv, msg)
        await publish_relay_message(getattr(request.app.state, "producer", None), conv, msg,
                                    outbound=outbound)
        return True
    except Exception:
        log.warning("no #%s card", DESIGN_CHANNEL, exc_info=True)
        return False


async def _visible_design_event(request: Request, caller: Caller, data: dict):
    if not isinstance(data, dict):
        return None
    who = await access.subject(request, caller)
    if data.get("type") in ("access", "deleted"):
        if who.is_admin or data.get("workspace") is True or set(who.principals) & set(data.get("audience", [])):
            return {key: data[key] for key in ("type", "slug", "document_id", "access_version") if key in data}
        return None
    identifier = data.get("document_id")
    if not isinstance(identifier, str):
        return None
    try:
        async with request.app.state.session_factory() as session:
            row = await access.require_read_by_uuid(session, identifier, who, allow_deleted_owner=False)
            if row.slug != data.get("slug"):
                return None
    except HTTPException:
        return None
    return {key: value for key, value in data.items() if key not in ("audience", "workspace")}


@router.get(PREFIX + "/events", response_class=StreamingResponse)
async def ui_designer_stream(request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*READ))):
    """The designer, live: a `design` frame per write, a heartbeat, and an
    `overflow` marker for a reader that fell too far behind (re-list to catch up).
    Unfiltered: a document is not room-scoped. Kafka-fed only."""
    feed = request.app.state.ui_designer_feed
    queue = feed.subscribe(STREAM)
    cookie = stream_cookie(request)
    recheck = require_ui_designer_access(*READ)
    last_check = time.monotonic()

    async def stream():
        nonlocal last_check
        try:
            while True:
                if cookie and not await still_signed_in(request.app, cookie):
                    return
                if time.monotonic() - last_check >= STREAM_RECHECK_SECONDS:
                    # The same door the GET routes use: a revoked key, a disabled or
                    # quarantined agent or a lost grant ends the stream.
                    last_check = time.monotonic()
                    try:
                        await recheck(request)
                    except HTTPException:
                        return
                try:
                    event, data = await asyncio.wait_for(queue.get(), relay_api.HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event == OVERFLOW:
                    yield relay_api._frame(OVERFLOW, {})
                    continue
                visible = await _visible_design_event(request, caller, data)
                if visible is not None:
                    yield relay_api._frame(event, visible)
        finally:
            feed.unsubscribe(STREAM, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --- documents ------------------------------------------------------------------

def _write_out(out: dict, slug: str) -> S.UiDocWriteOut:
    return S.UiDocWriteOut(slug=out.get("slug", slug), version=out["version"],
                           text=out.get("text", ""), warnings=out.get("warnings", []),
                           replayed=bool(out.get("replayed")), folded=bool(out.get("folded")),
                           undo_ops=out.get("undo_ops"))


@router.get(PREFIX + "/docs", response_model=S.UiDocList)
async def list_ui_documents(request: Request, q: str | None = Query(None, max_length=120),
                            limit: int = Query(50, ge=1, le=200), before: str | None = None,
                            caller: Caller = Depends(require_ui_designer_access(*READ))):
    async with request.app.state.session_factory() as s:
        try:
            rows = await store.list_documents(s, limit=limit + 1, q=q, before=before,
                readable=access.readable_clause(await access.subject(request, caller)))
        except UiDesignerError as e:
            raise ud_http(e)
    more = len(rows) > limit
    rows = rows[:limit]
    return S.UiDocList(docs=rows, next_before=rows[-1]["slug"] if more and rows else None)


async def _access_view(session, request: Request, caller: Caller, row) -> dict:
    from agentplatform.api.auth import role_allows
    who = await access.subject_for(session, caller.participant,
        is_admin=getattr(request.state, "resolved_role", None) == "admin" and caller.agent is None)
    view = await access.capabilities(session, row, who)
    global_write = role_allows(getattr(request.state, "resolved_role", ""), WRITE)
    if caller.agent is not None:
        try:
            global_write = await _run_of(session, request, caller, writing=True) is not None
        except HTTPException:
            global_write = False
    view["can_write"] = view["can_write"] and global_write
    view["can_manage_sharing"] = view["can_manage_sharing"] and global_write
    return view, who


def _presence_credential(request: Request) -> dict | None:
    from agentplatform.api.auth import hash_token
    if getattr(request.state, "auth_kind", None) == "session":
        identifier = getattr(request.state, "session_id", None)
        return {"kind": "session", "id": hash_token(identifier)} if identifier else None
    identifier = getattr(request.state, "api_key_id", None)
    return {"kind": "key", "id": identifier} if identifier else None


def _presence_view(data: dict) -> S.UiPresenceOut:
    view = S.UiPresenceOut.model_validate(data)
    if len(view.model_dump_json().encode()) > 32 << 10:
        raise HTTPException(413, "presence roster exceeds 32KiB")
    return view


async def _publish_presence(request: Request, slug: str, document_id: str, signal: dict | None):
    producer = getattr(request.app.state, "producer", None)
    if producer is None or signal is None:
        return
    data = {"type": "presence", "slug": slug, "document_id": document_id,
            **{key: signal[key] for key in ("participant", "client_session", "sequence")}}
    try:
        await producer.publish(TOPIC_UI_DESIGNER_EVENTS, "presence:" + document_id,
                               data, type="ui_designer.event")
    except Exception:
        log.warning("presence signal publish failed for %s", document_id, exc_info=True)


@router.get(PREFIX + "/docs/{slug}/presence", response_model=S.UiPresenceOut)
async def ui_presence_roster(slug: str, request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer import presence
    who = await access.subject(request, caller)
    async with request.app.state.session_factory() as session:
        row = await store._live(session, slug)
        return _presence_view(await presence.roster(session, row.id, who))


@router.put(PREFIX + "/docs/{slug}/presence/{client_session}", response_model=S.UiPresenceOut)
async def refresh_ui_presence(slug: str, body: S.UiPresenceIn, request: Request,
        client_session: str = Path(pattern=r"^[0-9a-f]{32}$"),
        caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer import presence
    if body.state == "editing":
        # This advertises an editing cursor, not a document mutation. Keep the
        # global write role/tool AND the service's current write ACL, without
        # requiring a Run for ordinary browser collaboration.
        await require_ui_designer_access(*WRITE, write=caller.agent is not None)(request)
    who = await access.subject(request, caller)
    async with request.app.state.session_factory() as session:
        row = await store._live(session, slug)
        result = _presence_view(await presence.refresh(session, row.id, who, client_session,
            body.model_dump(), credential=_presence_credential(request)))
        await session.commit()
    own = next((item.model_dump() for item in result.sessions
                if item.participant == who.participant and item.client_session == client_session), None)
    await _publish_presence(request, slug, row.id, own)
    return result


@router.delete(PREFIX + "/docs/{slug}/presence/{client_session}", status_code=204)
async def leave_ui_presence(slug: str, request: Request,
        client_session: str = Path(pattern=r"^[0-9a-f]{32}$"),
        document_id: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
        caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer import presence
    who = await access.subject(request, caller)
    async with request.app.state.session_factory() as session:
        row = await store._live(session, slug)
        if document_id is not None and document_id != row.id:
            raise HTTPException(409, "document identity changed")
        signal = await presence.leave(session, row.id, who, client_session)
        await session.commit()
    await _publish_presence(request, slug, row.id, signal)
    return Response(status_code=204)


@router.get(PREFIX + "/docs/{slug}/presence/events", response_class=StreamingResponse)
async def ui_presence_events(slug: str, request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer import presence
    identifier = request.state.ui_acl_document_id
    feed = request.app.state.ui_designer_feed
    stream_key = "presence:" + identifier
    cookie = stream_cookie(request)

    async def snapshot(signal=None):
        fresh = await require_ui_designer_access(*READ)(request)
        who = await access.subject(request, fresh)
        async with request.app.state.session_factory() as session:
            await access.require_read_by_uuid(session, identifier, who, allow_deleted_owner=False)
            if signal is not None and signal.get("type") == "presence":
                if signal.get("document_id") != identifier:
                    return None
                current = await presence.event_current(session, identifier, who,
                    signal.get("participant"), signal.get("client_session"), signal.get("sequence"))
                if current is None:
                    return None
            return _presence_view(await presence.roster(session, identifier, who)).model_dump()

    async def stream():
        queue = feed.subscribe(stream_key)
        try:
            initial = await snapshot()
            yield relay_api._frame("presence", initial)
            while True:
                if cookie and not await still_signed_in(request.app, cookie):
                    return
                try:
                    event, data = await asyncio.wait_for(queue.get(), relay_api.HEARTBEAT_SECONDS)
                except TimeoutError:
                    # Full snapshots on heartbeats remove TTL-expired peers even
                    # when Kafka is down and no explicit leave signal arrives.
                    yield relay_api._frame("presence", await snapshot())
                    continue
                if event == OVERFLOW:
                    yield relay_api._frame(OVERFLOW, {})
                    yield relay_api._frame("presence", await snapshot())
                    continue
                view = await snapshot(data)
                if view is not None:
                    yield relay_api._frame("presence", view)
        except HTTPException:
            return
        finally:
            feed.unsubscribe(stream_key, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
        headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})


@router.get(PREFIX + "/docs/{slug}/access", response_model=S.UiAccessOut)
async def get_ui_access(slug: str, request: Request,
                        caller: Caller = Depends(require_ui_designer_access(*READ))):
    async with request.app.state.session_factory() as session:
        row = await store._live(session, slug)
        view, _ = await _access_view(session, request, caller, row)
        return view


@router.put(PREFIX + "/docs/{slug}/access", response_model=S.UiAccessOut)
async def set_ui_access(slug: str, body: S.UiAccessIn, request: Request,
                        caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    from agentplatform.db import UiDesignerAccess, UiDesignerAccessEntry
    from sqlalchemy import delete
    import json
    for principals in (body.readers, body.editors):
        if len(set(principals)) != len(principals):
            raise HTTPException(422, "sharing principals must be distinct within each list")
        if any(not re.fullmatch(r"(?:user|agent|team):[^:\x00-\x20\x7f]{1,128}", principal)
               or len(principal) > 160 for principal in principals):
            raise HTTPException(422, "sharing principals must be user:name, agent:name or team:id")
    run_id = await caller_run_id(request, caller)
    scope = _scope(caller, run_id)
    fingerprint = store.args_hash(op="access", slug=slug, **body.model_dump(exclude={"request_id"}))
    async with request.app.state.session_factory() as session:
        row = await _acl_lock(request, caller, session, slug)
        previous, who = await _access_view(session, request, caller, row)
        if not previous["can_manage_sharing"]:
            raise HTTPException(403, "only the document owner or a human administrator manages sharing")
        prior = await store._receipt_lookup(session, scope, body.request_id, "access", fingerprint)
        if prior is not None:
            if prior["document_id"] != row.id:
                raise HTTPException(404, "design document identity changed")
            return {**prior["access"], "replayed": True}
        if previous["access_version"] != body.expected_access_version:
            raise HTTPException(409, detail={"code": "UD-ACCESS-VERSION-CONFLICT",
                "message": "sharing changed; reload access before applying your changes",
                "current_access_version": previous["access_version"]})
        if body.visibility == "restricted":
            from agentplatform.ui_designer.artifact_access import backfill_design_artifacts
            await backfill_design_artifacts(session)
        policy = await session.get(UiDesignerAccess, row.id)
        if policy is None:
            policy = UiDesignerAccess(document_id=row.id)
            session.add(policy)
        policy.visibility = body.visibility
        policy.readers = sorted(body.readers)
        policy.editors = sorted(body.editors)
        policy.access_version = previous["access_version"] + 1
        await session.execute(delete(UiDesignerAccessEntry).where(UiDesignerAccessEntry.document_id == row.id))
        for role, principals in (("reader", policy.readers), ("editor", policy.editors)):
            for principal in principals:
                session.add(UiDesignerAccessEntry(document_id=row.id, principal=principal, role=role))
        await session.flush()
        view = await access.capabilities(session, row, who)
        receipt = {"document_id": row.id, "access": view}
        if len(json.dumps(receipt, ensure_ascii=False).encode()) > 16 << 10:
            raise HTTPException(422, "sharing receipt is limited to 16KiB; use fewer or shorter principal names")
        await store._receipt_write(session, scope, body.request_id, row.id, "access", fingerprint, receipt, utcnow())
        await session.commit()
    audience = sorted(set(previous["readers"] + previous["editors"] + view["readers"] + view["editors"] + [view["owner"]]))
    await _emit(request, caller, "access", slug, row.version, run_id, document_id=row.id,
        access_version=view["access_version"], audience=audience,
        workspace=previous["visibility"] == "workspace" or view["visibility"] == "workspace")
    return view


@router.get(PREFIX + "/sharing-choices", response_model=S.UiSharingChoices)
async def ui_sharing_choices(request: Request, slug: str = Query(min_length=1, max_length=80),
                              caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.db import AgentDef, Principal, ApiKey, Team
    async with request.app.state.session_factory() as session:
        row = await store._live(session, slug)
        view, _ = await _access_view(session, request, caller, row)
        if not view["can_read"]:
            raise HTTPException(404, "design document is unavailable")
        if not view["can_manage_sharing"]:
            raise HTTPException(403, "only an owner or administrator may browse sharing choices")
        users = (await session.execute(select(Principal.name).where(Principal.role != "user").order_by(Principal.name).limit(201))).scalars().all()
        keys = (await session.execute(select(ApiKey.name).where(ApiKey.agent.is_(None),
            ApiKey.revoked_at.is_(None), ApiKey.role != "user").order_by(ApiKey.name).limit(201))).scalars().all()
        agents = (await session.execute(select(AgentDef.name).where(AgentDef.enabled.is_(True)).order_by(AgentDef.name).limit(201))).scalars().all()
        teams = (await session.execute(select(Team).where(Team.archived_at.is_(None)).order_by(Team.name, Team.id).limit(101))).scalars().all()
    from agentplatform.relay import participant_of
    names = sorted({participant_of(principal=name) for name in [*users, *keys]} | {view["owner"]})
    principals = [{"principal": name, "label": name[5:] if name.startswith("user:") else name,
                   "kind": "user"} for name in names]
    principals += [{"principal": "agent:" + name, "label": name, "kind": "agent"} for name in agents]
    return {"principals": principals[:200], "teams": [{"principal": "team:" + team.id,
        "id": team.id, "name": team.name, "slug": team.slug} for team in teams[:100]],
        "truncated": len(principals) > 200 or len(teams) > 100 or len(users) > 200 or len(keys) > 200}


@router.get(PREFIX + "/icons", response_model=S.UiIconsOut)
async def list_ui_icons(request: Request, q: str = Query("", max_length=100),
                        limit: int = Query(40, ge=1, le=100),
                        cursor: str | None = Query(None, max_length=256),
                        caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer.icons import search_icons
    try:
        return search_icons(q=q, limit=limit, cursor=cursor)
    except UiDesignerError as error:
        raise ud_http(error)


@router.get(PREFIX + "/icons/{icon_id}", response_model=S.UiIconOut)
async def get_ui_icon(icon_id: str, request: Request, revision: int = Query(..., ge=1),
                      caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer.icons import get_icon
    try:
        return get_icon(icon_id, revision=revision)
    except UiDesignerError as error:
        raise ud_http(error)


@router.get(PREFIX + "/templates", response_model=S.UiTemplatesOut)
async def list_ui_templates(request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Immutable, asset-free starter documents; creating one is an ordinary write."""
    from agentplatform.ui_designer.templates import list_templates
    return S.UiTemplatesOut(templates=list_templates())


@router.post(PREFIX + "/docs", response_model=S.UiDocWriteOut)
async def create_ui_document(body: S.UiDocCreateIn, request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    run_id = await caller_run_id(request, caller)
    template = ({"id": body.template_id, "revision": body.template_revision}
                if body.template_id is not None else None)
    if template is None:
        await _check_write_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            doc = body.doc
            if template is not None:
                # Bind original inputs before asking today's catalogue for bytes.
                # A successful retry remains valid after a template is retired.
                fingerprint = store.create_fingerprint(slug=body.slug, title=body.title,
                    description=body.description, doc=None, template=template)
                prior = await store._receipt_lookup(s, _scope(caller, run_id), body.request_id,
                                                     "create", fingerprint)
                if prior is not None:
                    return _write_out(prior, body.slug)
                await _check_write_budget(request, caller)
                from agentplatform.ui_designer.templates import get_template
                doc = get_template(body.template_id, body.template_revision)
                if body.title:
                    doc["title"] = body.title
            out = await store.create_document(
                s, slug=body.slug, title=body.title, owner=caller.participant, author=caller.participant,
                run_id=run_id, doc=doc, description=body.description, reason=body.reason,
                caps=caps(request), request_id=body.request_id, scope=_scope(caller, run_id),
                _template=template)
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    out.setdefault("text", "created")
    if not out.get("replayed"):
        await _emit(request, caller, "version", body.slug, out["version"], run_id, document_id=out["id"])
    return _write_out(out, body.slug)


async def _doc_answer(request: Request, caller: Caller, slug: str, *, version: int | None,
                      node: str | None, depth: int, after: str | None, resolve: bool,
                      mode: str | None) -> S.UiDocGetOut:
    async with request.app.state.session_factory() as s:
        try:
            view = await store.get_document(s, slug, reader=caller.participant, version=version)
            state = await store.version_state(s, slug, version) if version is not None else None
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()           # records the reader (closes an open row for others)
    try:
        cut = views.cut_document(view["doc"], node=node, depth=depth, after=after,
                                 budget=max(1024, _text_cap(request) - 2048))
    except UiDesignerError as e:
        raise ud_http(e)
    resolved = None
    if resolve:
        try:
            tree = await _resolved_document(request, view["doc"], mode)
            resolved = ud_svg.boxes(ud_svg.find_node(tree, node)[0])
        except UiDesignerError as e:
            raise ud_http(e)
    meta = {k: v for k, v in view.items() if k != "doc"}
    return S.UiDocGetOut(**meta, state=state, resolved=resolved, **cut)


@router.get(PREFIX + "/docs/{slug}", response_model=S.UiDocGetOut)
async def get_ui_document(slug: str, request: Request, node: str | None = None,
                          depth: int = Query(3, ge=0, le=12), after: str | None = None,
                          mode: str | None = None, resolve: bool = False,
                          version: int | None = Query(None, ge=1),
                          caller: Caller = Depends(require_ui_designer_access(*READ))):
    return await _doc_answer(request, caller, slug, version=version, node=node, depth=depth,
                             after=after, resolve=resolve, mode=mode)


@router.get(PREFIX + "/docs/{slug}/outline", response_model=S.UiDocOutline)
async def ui_document_outline(slug: str, request: Request, page: str | None = None,
                              node: str | None = None, after: str | None = None,
                              mode: str | None = None,
                              caller: Caller = Depends(require_ui_designer_access(*READ))):
    async with request.app.state.session_factory() as s:
        try:
            view = await store.get_document(s, slug, reader=caller.participant)
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    doc = view["doc"]
    warnings = lint(doc)
    boxes: dict[str, tuple[float, float]] = {}
    layout = "authored"
    try:
        tree = await _resolved_document(request, doc, mode)
        boxes = {n["id"]: (n["w"], n["h"]) for n in iter_nodes(tree)}
        warnings = warnings + [w for w in tree.get("warnings") or []]
        layout = "resolved"
    except (UiDesignerError, HTTPException):
        pass                       # the cheap map still works without sizes
    try:
        lines = views.outline_lines(doc, page=page, node=node, boxes=boxes, warnings=warnings)
        text, nxt = views.cut_lines(lines, after=after, cap=_text_cap(request))
    except UiDesignerError as e:
        raise ud_http(e)
    if after is None:
        text = f"# {slug} v{view['version']} {view['title']} ({view['node_count']} nodes, layout {layout})\n" + text
    return S.UiDocOutline(slug=slug, version=view["version"], text=text, next_after=nxt, layout=layout)


@router.post(PREFIX + "/docs/{slug}/edit", response_model=S.UiDocWriteOut)
async def edit_ui_document(slug: str, body: S.UiDocEditIn, request: Request,
                           caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    await refuse_runless_snapshot(request, caller, body.snapshot)
    nodes = _snapshot_nodes(request, body.snapshot, body.scale)
    run_id = await caller_run_id(request, caller)
    if any(op.get("op") in ("boolean", "icon") for op in body.ops):
        if len(body.ops) != 1:
            raise ud_http(invalid([issue("UD-LIMIT", "$.ops", "boolean or icon must be the only op in its edit batch")]))
        if body.ops[0]["op"] == "icon" and not body.request_id:
            raise ud_http(invalid([issue("UD-MISSING", "$.request_id", "icon insertion requires request_id")]))
        res = await _edit_prepared(slug, body, request, caller, run_id, bool(nodes))
    else:
        await _check_write_budget(request, caller)
        if nodes:
            _check_pixel_budget(request, caller)
        async with request.app.state.session_factory() as s:
            try:
                await _acl_lock(request, caller, s, slug)
                res = await store.edit_document(
                    s, slug, ops=body.ops, expected_version=body.expected_version,
                    author=caller.participant, run_id=run_id, reason=body.reason,
                    request_id=body.request_id, scope=_scope(caller, run_id),
                    wants_snapshot=bool(nodes), caps=caps(request))
            except UiDesignerError as e:
                raise ud_http(e)
            await s.commit()
    out = _write_out(res, slug)
    if not res.get("replayed"):
        await _emit(request, caller, "version", slug, res["version"], run_id)
    if nodes and not res.get("replayed"):
        await _attach_snapshots(request, caller, out, slug, res["doc"], nodes, body)
    return out


async def _edit_prepared(slug: str, body: S.UiDocEditIn, request: Request, caller: Caller,
                         run_id: str | None, wants_snapshot: bool) -> dict:
    from agentplatform.ui_designer.ops import apply_ops, prepare_boolean, boolean_literal_ops
    operation = body.ops[0]["op"]
    op = "edit-" + operation
    stable_args = {"ops": body.ops}
    scope = _scope(caller, run_id)
    fingerprint = store.edit_fingerprint(op=op, slug=slug, ops=None,
        expected_version=body.expected_version, reason=body.reason, stable_args=stable_args)
    async with request.app.state.session_factory() as s:
        try:
            # Original recipe receipts are resolved before loading operands or native work.
            if body.request_id:
                prior = await store._receipt_lookup(s, scope, body.request_id, op, fingerprint)
                if prior is not None:
                    await store.get_document(s, slug)  # still require a live authorized destination
                    return prior
            await _check_write_budget(request, caller)
            if wants_snapshot:
                _check_pixel_budget(request, caller)
            view = await store.get_document(s, slug)
            if view["version"] != body.expected_version:
                apply_ops(view["doc"], [], version=view["version"], expected_version=body.expected_version,
                          ops_since=await store.chunks_after(s, view["id"], body.expected_version))
            if operation == "icon":
                from agentplatform.ui_designer.icons import plan_icon
                prepared = plan_icon(view["doc"], body.ops[0])
                literal = prepared["ops"]
                warnings = prepared.get("warnings", [])
            else:
                prepared = prepare_boolean(view["doc"], body.ops[0])
                result = await _renderer(request).boolean_geometry(body.ops[0]["kind"], prepared["operands"])
                literal = boolean_literal_ops(prepared, result["geometry"])
                warnings = [{"code": "UD-WARN-BOOLEAN", "message": warning}
                            for warning in result.get("warnings", [])]
            await _acl_lock(request, caller, s, slug)
            res = await store.edit_document(s, slug, ops=literal, expected_version=body.expected_version,
                author=caller.participant, run_id=run_id, reason=body.reason, request_id=body.request_id,
                scope=scope, wants_snapshot=wants_snapshot, caps=caps(request), op=op,
                _stable_args=stable_args, _undo_ops=prepared["undo_ops"],
                _extra_warnings=warnings)
            await s.commit()
            return res
        except UiDesignerError as error:
            raise ud_http(error)


async def _attach_snapshots(request: Request, caller: Caller, out: S.UiDocWriteOut, slug: str,
                            doc: dict, nodes: list[str | None], body: S.UiDocEditIn) -> None:
    """Render after the commit, under the snapshot deadline. Whatever goes
    wrong here leaves the committed version intact and says so in `text`."""
    deadline = request.app.state.settings.ui_designer_snapshot_deadline_s
    try:
        renderer = _renderer(request)
        async with request.app.state.session_factory() as s:
            view = await store.get_document(s, slug)
            images = await asyncio.wait_for(renderer.render(
                s, document_id=view["id"], doc=doc, doc_sha256=store.doc_sha256(doc), mode=body.mode,
                nodes=nodes, scale=body.scale, annotate=body.annotate, timeout_s=deadline,
                max_bytes=request.app.state.settings.ui_designer_snapshot_max_bytes), deadline)
            await s.commit()
        _check_megapixels(request, images)
    except asyncio.TimeoutError:
        out.text += "\nsnapshot timed out; call snapshot"
        return
    except HTTPException as e:
        out.text += f"\nsnapshot failed: {e.detail.get('code') if isinstance(e.detail, dict) else e.detail}"
        return
    except UiDesignerError as e:
        out.text += f"\nsnapshot failed: {e.code} {e.message}"
        return
    _charge_pixels(request, caller, images)
    out.snapshots = [S.UiDocSnapshot(node=i.node, format=i.format, mime=i.mime, width=i.width,
                                     height=i.height, data=base64.b64encode(i.png).decode(),
                                     cached=i.cached, boxes=i.boxes) for i in images]


# --- history --------------------------------------------------------------------

@router.get(PREFIX + "/docs/{slug}/versions", response_model=S.UiVersionList)
async def ui_document_versions(slug: str, request: Request, limit: int = Query(50, ge=1, le=100),
                               before: int | None = Query(None, ge=1),
                               caller: Caller = Depends(require_ui_designer_access(*READ))):
    async with request.app.state.session_factory() as s:
        try:
            rows = await store.history(s, slug, limit=limit + 1, before=before)
            current = (await store.get_document(s, slug))["version"]
        except UiDesignerError as e:
            raise ud_http(e)
    cap = _text_cap(request)
    keep: list[dict] = []
    used = 0
    for r in rows[:limit]:
        r["reasons"] = [x[:120] for x in r["reasons"][:20]]
        used += views.size(r)
        if used > cap - 512 and keep:
            break
        keep.append(r)
    more = len(rows) > len(keep)
    return S.UiVersionList(slug=slug, version=current, versions=keep,
                           next_before=keep[-1]["first_version"] if more and keep else None)


@router.get(PREFIX + "/docs/{slug}/versions/{n}", response_model=S.UiDocGetOut)
async def get_ui_document_version(slug: str, n: int, request: Request, node: str | None = None,
                                  depth: int = Query(3, ge=0, le=12), after: str | None = None,
                                  mode: str | None = None, resolve: bool = False,
                                  caller: Caller = Depends(require_ui_designer_access(*READ))):
    return await _doc_answer(request, caller, slug, version=n, node=node, depth=depth, after=after,
                             resolve=resolve, mode=mode)


@router.post(PREFIX + "/docs/{slug}/restore", response_model=S.UiDocWriteOut)
async def restore_ui_document(slug: str, body: S.UiDocRestoreIn, request: Request,
                              caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    run_id = await caller_run_id(request, caller)
    await _check_write_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            await _acl_lock(request, caller, s, slug)
            res = await store.restore_version(
                s, slug, body.version, expected_version=body.expected_version,
                author=caller.participant, run_id=run_id, request_id=body.request_id,
                scope=_scope(caller, run_id), caps=caps(request))
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    if not res.get("replayed"):
        await _emit(request, caller, "version", slug, res["version"], run_id, restored=body.version)
    return _write_out(res, slug)


# --- inspect, diff, comments, label, export (Phase 1) --------------------------------

@router.get(PREFIX + "/docs/{slug}/inspect", response_model=S.UiDocInspectOut)
async def inspect_ui_document(slug: str, request: Request, node: str = Query(min_length=1, max_length=96),
                              mode: str | None = None, version: int | None = Query(None, ge=1),
                              caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Resolved geometry of the node and its children, resolved colours and
    typography per mode (every mode of the document, or just `mode`), the tokens
    its properties name, its text, the component it instances."""
    async with request.app.state.session_factory() as s:
        try:
            view = await store.get_document(s, slug, reader=caller.participant, version=version)
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    doc = view["doc"]
    doc_modes = list(doc.get("modes") or [])
    if mode is not None and doc_modes and mode not in doc_modes:
        raise ud_http(invalid([issue("UD-MODE", "$.mode", f"mode is one of {', '.join(doc_modes)}", mode)]))
    wanted: list[str | None] = [mode] if mode is not None else (doc_modes or [None])
    trees: dict[str | None, dict] = {}
    try:
        for m in wanted:
            trees[m] = await _resolved_document(request, doc, m)
        out = views.inspect_node(doc, node, trees, budget=max(1024, _text_cap(request) - 2048))
    except UiDesignerError as e:
        raise ud_http(e)
    return S.UiDocInspectOut(slug=slug, version=view["at_version"] if "at_version" in view else view["version"],
                             **out)


@router.get(PREFIX + "/docs/{slug}/diff", response_model=S.UiDocDiffOut)
async def diff_ui_document(slug: str, request: Request, from_: int = Query(alias="from", ge=0),
                           to: int = Query(ge=1),
                           caller: Caller = Depends(require_ui_designer_access(*READ))):
    """The writes between two version numbers (`from` exclusive, `to` inclusive),
    ops as applied. Every number is resolvable, folded or not (design 46 section 10)."""
    async with request.app.state.session_factory() as s:
        try:
            current = (await store.get_document(s, slug))["version"]
            if to > current:
                raise store._not_found(f"version {to} of {slug!r}")
            chunks = await store.history_diff(s, slug, from_, to)
        except UiDesignerError as e:
            raise ud_http(e)
    cap = _text_cap(request)
    text, cut = views.diff_text(chunks, cap=cap // 2)
    kept: list[dict] = []
    used = 0
    for c in chunks:
        used += views.size(c)
        if used > cap // 2 and kept:
            cut = True
            break
        kept.append(c)
    return S.UiDocDiffOut(slug=slug, version=current, from_version=from_, to_version=to, chunks=kept,
                          text=text, truncated=cut)


@router.get(PREFIX + "/docs/{slug}/comments", response_model=S.UiCommentList)
async def list_ui_comments(slug: str, request: Request, node: str | None = None,
                           resolved: bool = True, limit: int = Query(200, ge=1, le=500),
                           before: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
                           expected_document_id: str | None = Query(None, pattern=r"^[0-9a-f]{32}$"),
                           caller: Caller = Depends(require_ui_designer_access(*READ))):
    async with request.app.state.session_factory() as s:
        try:
            document = await store._live(s, slug)
            if expected_document_id and expected_document_id != document.id:
                raise HTTPException(404, "Document is unavailable")
            rows, nxt = await store.list_comments(s, slug, include_resolved=resolved, node=node,
                                                  limit=limit, before=before)
            from agentplatform.ui_designer import comment_wakes
            statuses = await comment_wakes.notifications(s, [c["id"] for c in rows])
            for c in rows:
                c["notifications"] = statuses.get(c["id"], [])
        except UiDesignerError as e:
            raise ud_http(e)
    return S.UiCommentList(slug=slug, document_id=document.id, comments=rows, next_before=nxt)


@router.post(PREFIX + "/docs/{slug}/comments", response_model=S.UiCommentOut)
async def add_ui_comment(slug: str, body: S.UiCommentIn, request: Request,
                         caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    from agentplatform.ui_designer import comment_wakes
    if comment_wakes.mentions(body.body) and not body.request_id:
        from agentplatform.ui_designer.errors import invalid, issue
        raise ud_http(invalid([issue("UD-MISSING", "$.request_id", "named mentions need a stable request_id")]))
    run_id = await caller_run_id(request, caller)
    await _check_write_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            document = await _acl_lock(request, caller, s, slug)
            if body.expected_document_id and body.expected_document_id != document.id:
                raise HTTPException(404, "Document is unavailable")
            out = await store.add_comment(
                s, slug, body=body.body, author=caller.participant, run_id=run_id,
                node_id=body.node_id, x=body.x, y=body.y, parent_id=body.parent_id,
                request_id=body.request_id, scope=_scope(caller, run_id), caps=caps(request))
            version = document.version
            if not out["replayed"]:
                from agentplatform.db import UiDesignerComment
                comment = await s.get(UiDesignerComment, out["id"])
                await comment_wakes.queue_comment(s, comment, version, request.app.state.settings,
                                                 request.app.state.agent_store)
            statuses = (await comment_wakes.notifications(s, [out["id"]])).get(out["id"], [])
            if out["comment"] is not None:
                out["comment"]["notifications"] = statuses
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    if not out["replayed"]:
        await _emit(request, caller, "comment", slug, version, run_id, comment_id=out["id"],
                    node_id=body.node_id)
    return S.UiCommentOut(slug=slug, document_id=out["document_id"], notifications=statuses,
                          id=out["id"], comment=out["comment"], replayed=out["replayed"])


@router.post(PREFIX + "/docs/{slug}/comments/{comment_id}/resolve", response_model=S.UiCommentOut)
async def resolve_ui_comment(slug: str, request: Request,
                             comment_id: str = Path(pattern=r"^[0-9a-f]{32}$"), reopen: bool = False,
                             caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    await _check_write_budget(request, caller)
    _check_card_budget(request, caller)
    run_id = await caller_run_id(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            await _acl_lock(request, caller, s, slug)
            out = await store.resolve_comment(s, slug, comment_id, by=caller.participant,
                                              resolved=not reopen, restrict=caller.agent is not None)
            version = (await store.get_document(s, slug))["version"]
            from agentplatform.ui_designer import comment_wakes
            statuses = (await comment_wakes.notifications(s, [out["id"]])).get(out["id"], [])
            out["comment"]["notifications"] = statuses
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    await _emit(request, caller, "comment", slug, version, run_id, comment_id=out["id"],
                resolved=out["resolved"])
    return S.UiCommentOut(slug=slug, document_id=out["document_id"], notifications=statuses,
                          id=out["id"], comment=out["comment"], resolved=out["resolved"])


@router.post(PREFIX + "/docs/{slug}/label", response_model=S.UiDocLabelOut)
async def label_ui_document(slug: str, body: S.UiDocLabelIn, request: Request,
                            caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    """Name a version (default: the current one). A label protects its row from
    folding and prune, and a named version is announced in `#design`."""
    await _check_write_budget(request, caller)
    _check_card_budget(request, caller)
    run_id = await caller_run_id(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            view = await store.get_document(s, slug)
            n = body.version or view["version"]
            await _acl_lock(request, caller, s, slug)
            await store.set_label(s, slug, body.label, n)
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    await _emit(request, caller, "version", slug, n, run_id, label=body.label)
    if body.label:
        await _design_card(
            request,
            body=f"{_card_text(view['title'] or slug, 64)} v{n} named \"{_card_text(body.label, 64)}\" "
                 f"by {participant_label(caller.participant)}",
            card={"type": "ui_design", "event": "labeled", "slug": slug, "document_id": view["id"], "version": n,
                  "label": one_line(body.label, 64), "author": caller.participant})
    return S.UiDocLabelOut(slug=slug, version=n, label=body.label)


@router.post(PREFIX + "/docs/{slug}/export", response_model=S.UiDocExportOut)
async def export_ui_document(slug: str, body: S.UiDocExportIn, request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    """Export one node/page to a bounded artifact and post its card in #design."""
    _check_scale(body.scale)
    if caller.agent is not None:
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if TOOL_ARTIFACTS not in granted:
            raise HTTPException(403, "exporting makes an artifact; it needs the artifacts tool")
    _check_card_budget(request, caller)
    run_id = await caller_run_id(request, caller)
    view, sha, _cc = await _doc_at(request, caller, slug, body.version)
    _check_pixel_budget(request, caller)
    warnings: list[str] = []
    async with request.app.state.session_factory() as s:
        try:
            if body.format == "png":
                images = await _renderer(request).render(
                    s, document_id=view["id"], doc=view["doc"], doc_sha256=sha, mode=body.mode,
                    nodes=[body.node], scale=body.scale, max_edge=4096)
                img = images[0]
                data, mime = img.png, "image/png"
            else:
                img = await _renderer(request).export_file(
                    s, doc=view["doc"], mode=body.mode, node=body.node, format=body.format,
                    scale=body.scale, max_bytes=min(8 << 20, request.app.state.settings.artifacts_max_bytes))
                images = [img]
                data, mime, warnings = img.data, img.mime, img.warnings
            await s.commit()
            _check_megapixels(request, images)
        except UiDesignerError as e:
            raise ud_http(e)
    _charge_pixels(request, caller, images)
    version = view.get("at_version") or view["version"]
    name = (body.name or f"{slug}-{body.node or 'page'}-v{version}").strip()
    extension = "." + body.format
    if not name.lower().endswith(extension):
        name += extension
    if body.format != "png":
        name = artifact_store.flatten(name[:-len(extension)], artifact_store.NAME_LIMIT - len(extension)) + extension
    async with request.app.state.session_factory() as s:
        try:
            current = await _acl_lock(request, caller, s, slug)
            if current.id != view["id"]:
                raise HTTPException(404, "design document identity changed")
            art = await artifact_store.create(
                s, data=data, owner=caller.participant, name=name, claimed_mime=mime,
                _trusted_mime=mime if body.format != "png" else None,
                source="tool", run_id=run_id, producer=getattr(request.app.state, "producer", None),
                settings=request.app.state.settings,
                ui_document_id=view["id"],
                meta={"ui_designer": {"document_id": view["id"], "slug": slug, "node": body.node, "version": version, "format": body.format}},
                tags=["ui-designer"])
        except artifact_store.ArtifactRuleError as e:
            raise HTTPException(e.status, str(e)) from e
    chip = f"[[artifact:{art.id}]]"
    posted = await _design_card(
        request,
        body=f"{chip}\n{_card_text(view['title'] or slug, 64)} v{version}"
             f"{' · ' + _card_text(body.node, 64) if body.node else ''} exported by {participant_label(caller.participant)}",
        card={"type": "ui_design", "event": "exported", "slug": slug, "document_id": view["id"], "node": body.node,
              "version": version, "artifact_id": art.id, "author": caller.participant})
    await _emit(request, caller, "exported", slug, version, run_id, artifact_id=art.id,
                node=body.node)
    return S.UiDocExportOut(slug=slug, version=version, artifact_id=art.id, name=art.name,
                            mime=art.mime, width=img.width, height=img.height, size=art.size,
                            url=f"/artifacts/{art.id}", card=chip, card_posted=posted, warnings=warnings)


async def _motion_disconnected(request: Request) -> bool:
    connection = getattr(request.state, "ui_motion_connection", request)
    return await connection.is_disconnected()


async def _await_motion(request: Request, operation):
    """Propagate browser cancellation through HTTP to the isolated renderer."""
    task = asyncio.create_task(operation)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=0.1)
            if await _motion_disconnected(request):
                raise HTTPException(499, "motion request disconnected")
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _motion_manifest(result: dict) -> S.UiMotionPreviewOut:
    from pydantic import ValidationError
    from agentplatform.ui_designer.motion import validate_manifest
    try:
        validate_manifest(result)
        manifest = S.UiMotionPreviewOut.model_validate({key: value for key, value in result.items() if key != "ok"})
        if len(manifest.model_dump_json(by_alias=True).encode()) > 16 << 20:
            raise ValueError("motion manifest exceeds 16MiB")
        if len(manifest.box) != 4 or manifest.box != [0, 0, manifest.width, manifest.height]:
            raise ValueError("motion canvas is invalid")
        return manifest
    except (ValidationError, ValueError, TypeError) as error:
        raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "renderer returned an invalid motion manifest") from error


def _motion_raster_pixels(manifest: S.UiMotionPreviewOut) -> int:
    import struct
    import hashlib
    pixels = 0
    seen = set()
    images = [manifest.from_image, manifest.to_image, *manifest.assets.values()]
    for index, encoded in enumerate(images):
        try:
            data = base64.b64decode(encoded, validate=True)
            if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
                raise ValueError()
            width, height = struct.unpack(">II", data[16:24])
            if not (1 <= width <= 720 and 1 <= height <= 720):
                raise ValueError()
            if index < 2 and (width, height) != (manifest.width, manifest.height):
                raise ValueError()
            digest = hashlib.sha256(data).digest()
            if index < 2 or digest not in seen:
                pixels += width * height
            seen.add(digest)
        except (ValueError, TypeError, struct.error) as error:
            raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "renderer returned invalid motion image dimensions") from error
    return pixels


def _motion_charge(request: Request, caller: Caller, pixels: int, *, cap: int) -> None:
    from types import SimpleNamespace
    if pixels > cap:
        raise UiDesignerError(ud_errors.UD_IMAGE_TOO_LARGE,
            "motion exceeds its raster work limit; lower max_edge, duration_ms or fps",
            detail={"pixels": pixels, "limit_pixels": cap})
    _check_pixel_budget(request, caller)
    hourly = request.app.state.settings.ui_designer_pixels_per_hour_mp
    if caller.agent is not None and hourly > 0:
        used = sum(mp for _, mp in _pixel_window(request, caller.agent))
        if used + pixels / 1e6 > hourly:
            raise UiDesignerError(ud_errors.UD_BUDGET_PIXELS,
                "motion would exceed the hourly pixel budget; lower max_edge or duration_ms",
                detail={"used_mp": used, "requested_mp": pixels / 1e6, "limit_mp": hourly})
    _charge_pixels(request, caller, [SimpleNamespace(width=pixels, height=1)])


def _motion_args(body: S.UiMotionIn, *, max_edge: int | None = None) -> dict:
    return {"from_node": body.from_node, "to_node": body.to_node, "mode": body.mode,
            "duration_ms": body.duration_ms, "easing": body.easing, "fps": body.fps,
            "max_edge": max_edge or body.max_edge}


@router.post(PREFIX + "/docs/{slug}/motion/preview", response_model=S.UiMotionPreviewOut)
async def preview_ui_motion(slug: str, body: S.UiMotionIn, request: Request,
                            caller: Caller = Depends(require_ui_designer_access(*READ))):
    view, _, _ = await _doc_at(request, caller, slug, body.version)
    _check_pixel_budget(request, caller)
    try:
        async with request.app.state.session_factory() as session:
            result = await _await_motion(request, _renderer(request).motion_preview(
                session, doc=view["doc"], **_motion_args(body)))
        manifest = _motion_manifest(result)
        _motion_charge(request, caller, _motion_raster_pixels(manifest), cap=64_000_000)
        return manifest
    except UiDesignerError as error:
        raise ud_http(error)


async def _motion_receipt(session, slug: str, scope: str, request_id: str, fingerprint: str):
    result = await store._receipt_lookup(session, scope, request_id, "motion-export", fingerprint)
    if result is not None:
        current = await store.get_document(session, slug)
        if current["id"] != result["document_id"]:
            raise UiDesignerError(ud_errors.UD_NOT_FOUND, "motion source document identity changed")
    return result


@router.post(PREFIX + "/docs/{slug}/motion/export", response_model=S.UiMotionExportOut)
async def export_ui_motion(slug: str, body: S.UiMotionExportIn, request: Request,
                           caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    if caller.agent is not None:
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if TOOL_ARTIFACTS not in granted:
            raise HTTPException(403, "motion export needs the artifacts tool")
    run_id = await caller_run_id(request, caller)
    scope = _scope(caller, run_id)
    fingerprint = store.args_hash(op="motion-export", slug=slug,
        **body.model_dump(exclude={"request_id"}))
    try:
        async with request.app.state.session_factory() as session:
            prior = await _motion_receipt(session, slug, scope, body.request_id, fingerprint)
            if prior is not None:
                return prior
        view, _, _ = await _doc_at(request, caller, slug, body.version)
        _check_pixel_budget(request, caller)
        edge = min(body.max_edge, 480) if body.format == "gif" else body.max_edge
        args = _motion_args(body, max_edge=edge)
        async with request.app.state.session_factory() as session:
            manifest = _motion_manifest(await _await_motion(request, _renderer(request).motion_preview(
                session, doc=view["doc"], **args)))
            _motion_charge(request, caller, _motion_raster_pixels(manifest), cap=64_000_000)
            _motion_charge(request, caller, manifest.frames * manifest.width * manifest.height,
                           cap=28_000_000 if body.format == "gif" else 64_000_000)
            result = await _await_motion(request, _renderer(request).motion_export(
                session, doc=view["doc"], **args, format=body.format,
                max_bytes=min(8 << 20, request.app.state.settings.artifacts_max_bytes)))
        expected_mime = "video/mp4" if body.format == "mp4" else "image/gif"
        if (not isinstance(result, dict) or result.get("mime") != expected_mime or
                not isinstance(result.get("data"), bytes) or not result["data"] or len(result["data"]) > 8 << 20 or
                any(result.get(key) != getattr(manifest, key) for key in ("width", "height", "frames", "duration_ms"))):
            raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "renderer returned an invalid motion export")
        warnings = result.get("warnings", manifest.warnings)
        if (not isinstance(warnings, list) or len(warnings) > 32 or
                any(not isinstance(warning, str) or len(warning.encode()) > 500 for warning in warnings)):
            raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "renderer returned invalid motion warnings")
        data = result["data"]
        if (body.format == "mp4" and (len(data) < 12 or data[4:8] != b"ftyp")) or (
                body.format == "gif" and data[:6] not in (b"GIF87a", b"GIF89a")):
            raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "renderer returned invalid motion file bytes")
        # Re-enter the same authorization door immediately before committing;
        # later document ACLs and live run/grant checks belong to this seam.
        await require_ui_designer_access(*WRITE, write=True)(request)
        async with request.app.state.session_factory() as session:
            current = await store._live(session, slug, lock=True)
            if current.id != view["id"]:
                raise UiDesignerError(ud_errors.UD_NOT_FOUND, "motion source document identity changed")
            prior = await _motion_receipt(session, slug, scope, body.request_id, fingerprint)
            if prior is not None:
                return prior
            if await _motion_disconnected(request):
                raise HTTPException(499, "motion request disconnected")
            await _acl_lock(request, caller, session, slug)
            artifact = await artifact_store.create(session, data=result["data"], owner=caller.participant,
                name=f"{slug}-v{body.version}-motion.{body.format}", claimed_mime=expected_mime,
                _trusted_mime=expected_mime if body.format == "mp4" else None,
                source="tool", run_id=run_id, settings=request.app.state.settings, commit=False,
                ui_document_id=view["id"],
                meta={"ui_designer": {"document_id": view["id"], "slug": slug,
                      "version": body.version, "format": body.format, "from_node": body.from_node,
                      "to_node": body.to_node}}, tags=["ui-designer", "motion"])
            response = S.UiMotionExportOut(slug=slug, version=body.version, document_id=view["id"],
                artifact_id=artifact.id, name=artifact.name, mime=artifact.mime,
                width=manifest.width, height=manifest.height, frames=manifest.frames,
                duration_ms=manifest.duration_ms, size=artifact.size, url=f"/artifacts/{artifact.id}",
                warnings=warnings).model_dump(exclude={"replayed"})
            try:
                await store._receipt_write(session, scope, body.request_id, view["id"],
                    "motion-export", fingerprint, response, utcnow())
            except ValueError as error:
                raise invalid([issue("UD-LIMIT", "$.warnings", "motion receipt exceeds 16KiB")]) from error
            await session.commit()
        await artifact_store.publish_artifact_event(getattr(request.app.state, "producer", None),
            event="created", artifact=artifact_store.artifact_view(artifact), agent=caller.agent)
        return response
    except UiDesignerError as error:
        raise ud_http(error)
    except artifact_store.ArtifactRuleError as error:
        raise HTTPException(error.status, str(error)) from error


@router.post(PREFIX + "/docs/{slug}/visual-diff", response_model=S.UiVisualDiffOut)
async def visual_diff_ui_document(slug: str, body: S.UiVisualDiffIn, request: Request,
                                  caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Compare two explicit retained versions in the same world-coordinate canvas."""
    from types import SimpleNamespace
    _check_scale(body.scale)
    _check_pixel_budget(request, caller)
    before, _, _ = await _doc_at(request, caller, slug, body.from_version)
    after, _, _ = await _doc_at(request, caller, slug, body.to_version)
    try:
        async with request.app.state.session_factory() as s:
            result = await _renderer(request).visual_diff(
                s, before_doc=before["doc"], after_doc=after["doc"], node=body.node,
                mode=body.mode, scale=body.scale, tolerance=body.tolerance)
        # Both source renders and both aligned output images consume pixel work.
        work = [SimpleNamespace(width=result["width"], height=result["height"])] * 2
        try:
            _check_megapixels(request, work)
        except UiDesignerError as error:
            raise UiDesignerError(error.code,
                "visual diff counts both aligned images toward the per-request megapixel limit; "
                "choose a smaller node or scale", detail=error.detail) from error
    except UiDesignerError as e:
        raise ud_http(e)
    _charge_pixels(request, caller, work)
    return S.UiVisualDiffOut(slug=slug, from_version=body.from_version,
                             to_version=body.to_version, **result)


@router.get(PREFIX + "/docs/{slug}/lint", response_model=S.UiLintOut)
async def lint_ui_document(slug: str, request: Request,
                           version: int = Query(..., ge=1),
                           node: str | None = Query(None, max_length=512),
                           mode: str | None = Query(None, max_length=16),
                           all_modes: bool = False,
                           severity: Literal["error", "warning", "info"] | None = None,
                           limit: int = Query(100, ge=1, le=100),
                           cursor: str | None = Query(None, max_length=512),
                           caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Deterministic findings for a retained version; pagination never follows latest."""
    import hashlib
    import json
    from agentplatform.ui_designer.lint import RULES_VERSION
    if mode is not None and all_modes:
        raise HTTPException(422, "choose mode or all_modes")
    view, sha, _ = await _doc_at(request, caller, slug, version)
    scope = hashlib.sha256(json.dumps([slug, version, sha, RULES_VERSION, node, mode,
                                      all_modes, severity], separators=(",", ":")).encode()).hexdigest()
    offset = 0
    if cursor:
        try:
            raw = base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True)
            decoded = json.loads(raw)
            if (not isinstance(decoded, dict) or set(decoded) != {"scope", "offset"}
                    or decoded["scope"] != scope or not isinstance(decoded["offset"], int)
                    or isinstance(decoded["offset"], bool) or not 0 <= decoded["offset"] <= 16000):
                raise ValueError()
            offset = decoded["offset"]
        except (ValueError, TypeError):
            raise HTTPException(422, "lint cursor does not match this version, rules or options")
    modes = (view["doc"].get("modes") or [None]) if all_modes else [mode]
    gate = asyncio.Semaphore(2)
    async def evaluate(selected_mode):
        async with gate:
            async with request.app.state.session_factory() as session:
                return await _renderer(request).lint(session, doc=view["doc"], node=node, mode=selected_mode)
    tasks = [asyncio.create_task(evaluate(value)) for value in modes]
    try:
        results = await asyncio.gather(*tasks)
    except BaseException as error:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if isinstance(error, UiDesignerError):
            raise ud_http(error)
        raise
    # Global findings can occur in several mode evaluations; collapse exact duplicates.
    unique = {json.dumps(finding, sort_keys=True, separators=(",", ":")): finding
              for result in results for finding in result["findings"]}
    findings = sorted(unique.values(), key=lambda f: (f["mode"] or "", f["node_id"] or "", f["code"], f["message"]))
    counts = {level: sum(f["severity"] == level for f in findings) for level in ("error", "warning", "info")}
    if severity is not None:
        findings = [f for f in findings if f["severity"] == severity]
    if offset > len(findings):
        raise HTTPException(422, "lint cursor is beyond the available findings")
    truncated = any(result["truncated"] for result in results)
    out = {"slug": slug, "version": version, "rules_version": RULES_VERSION,
           "findings": [], "total": len(findings), "by_severity": counts,
           "next_cursor": None, "truncated": truncated}
    budget = min(32 << 10, _text_cap(request))
    def next_cursor(index):
        return base64.urlsafe_b64encode(json.dumps({"scope": scope, "offset": index}, separators=(",", ":")).encode()).decode().rstrip("=")
    for finding in findings[offset:offset + limit]:
        out["findings"].append(finding)
        index = offset + len(out["findings"])
        out["next_cursor"] = next_cursor(index) if index < len(findings) else None
        if len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) > budget:
            out["findings"].pop()
            if not out["findings"]:
                raise HTTPException(413, "lint finding cannot fit the configured text budget")
            break
    index = offset + len(out["findings"])
    out["next_cursor"] = next_cursor(index) if index < len(findings) else None
    return S.UiLintOut(**out)


# Pinned libraries copy authorized data once; rendering never reads a source.
def _library_report(report: dict, *, slug: str, version: int, library_id: str,
                    source_version: int, budget: int = 32 << 10) -> dict:
    import json
    out = {"slug": slug, "version": version, "library_id": library_id,
           "source_version": source_version, "counts": report.get("counts", {}),
           "added": list(report.get("added", []))[:200],
           "updated": list(report.get("updated", []))[:200],
           "removed": list(report.get("removed", []))[:200],
           "conflicts": list(report.get("conflicts", []))[:200],
           "truncated": bool(report.get("truncated"))}
    if any(len(report.get(key, [])) > 200 for key in ("added", "updated", "removed", "conflicts")):
        out["truncated"] = True
    while len(json.dumps(out, ensure_ascii=False, separators=(",", ":")).encode()) > budget:
        key = next((key for key in ("removed", "updated", "added", "conflicts") if out[key]), None)
        if key is None:
            raise HTTPException(413, "library report exceeds the configured text budget")
        out[key].pop()
        out["truncated"] = True
    return out


async def _library_source(s, source_id: str, source_version: int, request: Request, caller: Caller) -> dict:
    from agentplatform.db import UiDesignerDocument
    row = await s.get(UiDesignerDocument, source_id)
    if row is None or row.deleted_at is not None:
        raise HTTPException(404, "library source is unavailable")
    await access.require_read_by_uuid(s, source_id, await access.subject(request, caller), allow_deleted_owner=False)
    # A pinned copy is a source read, never a cross-document write/row lock.
    view = await store.get_document(s, row.slug, version=source_version)
    if view["id"] != source_id:
        raise HTTPException(404, "library source identity changed")
    return view


async def _library_place_assets(s, destination_id: str, source: dict, shas: set[str], by: str) -> None:
    from agentplatform.db import UiDesignerDocumentAsset
    wanted = set(shas)
    referenced = {sha for _, sha in image_assets(source["doc"])}
    placed = await store.placed_assets(s, source["id"])
    if not wanted <= referenced & placed:
        raise HTTPException(403, "library assets must belong to the pinned source document")
    existing = await store.placed_assets(s, destination_id)
    ordered = sorted(wanted)
    for start in range(0, len(ordered), 500):
        batch = ordered[start:start + 500]
        locked = (await s.execute(store._for_update(s, select(UiDesignerAsset.sha256).where(
            UiDesignerAsset.sha256.in_(batch)).order_by(UiDesignerAsset.sha256)))).scalars().all()
        if set(locked) != set(batch):
            raise HTTPException(404, "a pinned library asset is unavailable")
    s.add_all([UiDesignerDocumentAsset(document_id=destination_id, sha256=sha, placed_by=by,
                 placed_from=f"library:{source['id']}:{source['at_version']}", placed_at=utcnow())
               for sha in ordered if sha not in existing])
    await s.flush()


@router.get(PREFIX + "/docs/{slug}/libraries", response_model=S.UiLibraryListOut)
async def list_ui_libraries(slug: str, request: Request,
                            version: int | None = Query(None, ge=1),
                            caller: Caller = Depends(require_ui_designer_access(*READ))):
    view, _, _ = await _doc_at(request, caller, slug, version)
    pins = [{"library_id": key, "namespace": record["namespace"],
             "source_id": record["source_id"], "source_slug": record["source_slug"],
             "source_version": record["source_version"],
             "counts": {group: len(values) for group, values in record["owned"].items()}}
            for key, record in view["doc"].get("libraries", {}).items()]
    return S.UiLibraryListOut(slug=slug, version=view["at_version"], libraries=pins)


@router.post(PREFIX + "/docs/{slug}/libraries/{library_id}/preview", response_model=S.UiLibraryPreviewOut)
async def preview_ui_library(slug: str, library_id: str, body: S.UiLibraryPreviewIn,
                              request: Request,
                              caller: Caller = Depends(require_ui_designer_access(*READ))):
    from agentplatform.ui_designer import libraries
    async with request.app.state.session_factory() as s:
        try:
            destination = await store.get_document(s, slug)
            pin = destination["doc"].get("libraries", {}).get(library_id)
            if pin is None:
                raise HTTPException(404, "library pin not found")
            source = await _library_source(s, pin["source_id"], body.source_version, request, caller)
            plan = libraries.plan_update(destination["doc"], source["doc"], destination_id=destination["id"],
                library_id=library_id, source_version=body.source_version, source_slug=source["slug"])
            await s.commit()
        except UiDesignerError as error:
            raise ud_http(error)
    return S.UiLibraryPreviewOut(**_library_report(plan["report"], slug=slug,
        version=destination["version"], library_id=library_id, source_version=body.source_version,
        budget=min(32 << 10, _text_cap(request))))


async def _mutate_ui_library(slug: str, request: Request, caller: Caller,
                              body: S.UiLibraryImportIn | S.UiLibraryApplyIn,
                              library_id: str | None = None) -> S.UiLibraryWriteOut:
    from agentplatform.ui_designer import libraries
    run_id = await caller_run_id(request, caller)
    scope = _scope(caller, run_id)
    importing = isinstance(body, S.UiLibraryImportIn)
    op = "library-import" if importing else "library-apply"
    reason = "import pinned library" if importing else "update pinned library"
    stable_args = body.model_dump(exclude={"expected_version", "request_id"})
    if not importing:
        stable_args["library_id"] = library_id
    library_id = body.library.namespace if importing else library_id
    fingerprint = store.edit_fingerprint(op=op, slug=slug, ops=None,
        expected_version=body.expected_version, reason=reason, stable_args=stable_args)
    async with request.app.state.session_factory() as s:
        try:
            # Serialize before receipt lookup: two identical retries cannot plan twice.
            destination = await _acl_lock(request, caller, s, slug)
            prior = await store._receipt_lookup(s, scope, body.request_id, op, fingerprint)
            if prior is not None:
                return S.UiLibraryWriteOut(**_write_out(prior, slug).model_dump(), library_id=library_id)
            if destination.version != body.expected_version:
                from agentplatform.ui_designer.ops import apply_ops
                apply_ops(destination.doc, [], version=destination.version,
                          expected_version=body.expected_version,
                          ops_since=await store.chunks_after(s, destination.id, body.expected_version))
            await _check_write_budget(request, caller)
            if importing:
                spec = body.library
                source = await _library_source(s, spec.source_id, spec.source_version, request, caller)
                plan = libraries.plan_import(destination.doc, source["doc"], destination_id=destination.id,
                    source_id=source["id"], source_version=spec.source_version, source_slug=source["slug"],
                    namespace=spec.namespace, components=spec.components,
                    component_sets=spec.component_sets, tokens=spec.tokens)
            else:
                pin = destination.doc.get("libraries", {}).get(library_id)
                if pin is None:
                    raise HTTPException(404, "library pin not found")
                source = await _library_source(s, pin["source_id"], body.source_version, request, caller)
                plan = libraries.plan_update(destination.doc, source["doc"], destination_id=destination.id,
                    library_id=library_id, source_version=body.source_version, source_slug=source["slug"])
            if plan["report"].get("conflicts"):
                report = _library_report(plan["report"], slug=slug, version=destination.version,
                    library_id=library_id, source_version=source["at_version"], budget=min(32 << 10, _text_cap(request)))
                raise HTTPException(409, {"code": "UD-LIBRARY-CONFLICT", "message": "library update has conflicts", **report})
            await _library_place_assets(s, destination.id, source, plan["assets"], caller.participant)
            await store.copy_document_fonts(s, source["id"], destination.id,
                plan.get("fonts", set()), placed_by=caller.participant)
            out = await store.edit_document(s, slug, ops=[{"op": "set_doc", "doc": plan["doc"]}],
                expected_version=body.expected_version, author=caller.participant, run_id=run_id,
                reason=reason, request_id=body.request_id, scope=scope, op=op, caps=caps(request),
                _stable_args=stable_args)
            await s.commit()
        except UiDesignerError as error:
            raise ud_http(error)
    if not out.get("replayed"):
        await _emit(request, caller, "version", slug, out["version"], run_id)
    return S.UiLibraryWriteOut(**_write_out(out, slug).model_dump(), library_id=library_id)


@router.post(PREFIX + "/docs/{slug}/libraries/import", response_model=S.UiLibraryWriteOut)
async def import_ui_library(slug: str, body: S.UiLibraryImportIn, request: Request,
                            caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    return await _mutate_ui_library(slug, request, caller, body)


@router.post(PREFIX + "/docs/{slug}/libraries/{library_id}/apply", response_model=S.UiLibraryWriteOut)
async def apply_ui_library(slug: str, library_id: str, body: S.UiLibraryApplyIn, request: Request,
                           caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    return await _mutate_ui_library(slug, request, caller, body, library_id)


# --- delete and reclaim (Kyle's session) ------------------------------------------

@router.delete(PREFIX + "/docs/{slug}", response_model=S.UiDocDeleteOut)
async def delete_ui_document(slug: str, request: Request, hard: bool = False,
                             caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    """Soft delete: the owner, an `agents_edit` holder or the admin. `hard=true`
    removes the document, its versions, comments and placements now and runs
    asset GC: the administrator's browser session only."""
    async with request.app.state.session_factory() as s:
        try:
            from agentplatform.db import UiDesignerDocument
            target = (await s.execute(select(UiDesignerDocument).where(
                UiDesignerDocument.slug == slug, UiDesignerDocument.deleted_at.is_(None)))).scalars().first()
            if target is None and hard:
                target = await s.get(UiDesignerDocument, slug)
            if target is None:
                raise HTTPException(404, "design document is unavailable")
            previous = await access.capabilities(s, target, await access.subject(request, caller))
            deleted_audience = sorted(set(previous["readers"] + previous["editors"] + [previous["owner"]]))
            request.state.ui_acl_document_id = target.id
            if hard:
                _session_admin(request, caller)
                out = await store.hard_delete(s, slug)
                await s.commit()
                await _emit(request, caller, "deleted", target.slug, 0, None, hard=True,
                    audience=deleted_audience, workspace=previous["visibility"] == "workspace")
                return S.UiDocDeleteOut(hard=True, assets_removed=out["assets_removed"],
                                        bytes_freed=out["bytes_freed"])
            view = await store.get_document(s, slug)
            await _may_modify(request, caller, view["owner"])
            await _acl_lock(request, caller, s, slug)
            await store.soft_delete(s, slug)
            await s.commit()
        except UiDesignerError as e:
            raise ud_http(e)
    await _emit(request, caller, "deleted", slug, view["version"], None,
                audience=deleted_audience, workspace=previous["visibility"] == "workspace")
    return S.UiDocDeleteOut()


@router.post(PREFIX + "/docs/{slug}/prune", response_model=S.UiDocPruneOut)
async def prune_ui_document(slug: str, body: S.UiDocPruneIn, request: Request,
                            caller: Caller = Depends(require_ui_designer_access("admin", write=True))):
    _session_admin(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            out = await store.prune_versions(s, slug, older_than_days=body.older_than_days,
                                             keep_last=body.keep_last)
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    return S.UiDocPruneOut(**out)


@router.post(PREFIX + "/assets/gc", response_model=S.UiAssetGcOut)
async def gc_ui_assets(request: Request,
                       caller: Caller = Depends(require_ui_designer_access("admin", write=True))):
    _session_admin(request, caller)
    async with request.app.state.session_factory() as s:
        out = await store.gc_assets(s)
        await s.commit()
    return S.UiAssetGcOut(**out)


# --- pictures for the web: render, svg, resolved, assets ----------------------------

async def _doc_at(request: Request, caller: Caller, slug: str, version: int | None):
    """(view, sha256, cache-control) for the current document or a named
    version. `immutable` only for a number in a closed row (§10)."""
    async with request.app.state.session_factory() as s:
        try:
            view = await store.get_document(s, slug, reader=caller.participant, version=version)
            state = await store.version_state(s, slug, version) if version is not None else None
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    sha = view["doc_sha256"] if version is None or version == view["version"] \
        else store.doc_sha256(view["doc"])
    cc = "private, no-store"
    return view, sha, cc


def _etag_hit(request: Request, etag: str) -> bool:
    return request.headers.get("if-none-match") == etag


@router.get(PREFIX + "/docs/{slug}/render", response_class=Response)
async def render_ui_document(slug: str, request: Request, node: str | None = None,
                             mode: str | None = None, scale: float = 1, annotate: bool = False,
                             max_edge: int = Query(1568, ge=64, le=1568),
                             version: int | None = Query(None, ge=1),
                             caller: Caller = Depends(require_ui_designer_access(*READ))):
    _check_scale(scale)
    view, sha, cc = await _doc_at(request, caller, slug, version)
    # An agent's picture goes into a model's context: the byte ladder applies.
    max_bytes = request.app.state.settings.ui_designer_snapshot_max_bytes if caller.agent is not None else None
    etag = '"' + render_client.cache_key(sha, node, mode, scale, annotate, max_edge=max_edge, max_bytes=max_bytes) + '"'
    headers = {"Cache-Control": cc, "ETag": etag, "X-Content-Type-Options": "nosniff"}
    if _etag_hit(request, etag):
        return Response(status_code=304, headers=headers)
    _check_pixel_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            images = await _renderer(request).render(
                s, document_id=view["id"], doc=view["doc"], doc_sha256=sha, mode=mode, nodes=[node],
                scale=scale, annotate=annotate, max_edge=max_edge, max_bytes=max_bytes)
            await s.commit()
            _check_megapixels(request, images)
        except UiDesignerError as e:
            raise ud_http(e)
    _charge_pixels(request, caller, images)
    return Response(content=images[0].png, media_type=images[0].mime, headers=headers)


@router.get(PREFIX + "/docs/{slug}/svg", response_class=Response)
async def ui_document_svg(slug: str, request: Request, node: str | None = None,
                          mode: str | None = None, annotate: bool = False,
                          version: int | None = Query(None, ge=1),
                          caller: Caller = Depends(require_ui_designer_access(*READ))):
    view, sha, cc = await _doc_at(request, caller, slug, version)
    etag = '"' + render_client.cache_key(sha, node, mode, 1, annotate, max_edge=0) + '"'
    headers = {"Cache-Control": cc, "ETag": etag, "X-Content-Type-Options": "nosniff",
               "Content-Security-Policy": "default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"}
    if _etag_hit(request, etag):
        return Response(status_code=304, headers=headers)
    shas = {a for _p, a in image_assets(view["doc"])}
    sizes: dict[str, dict] = {}
    if shas:
        async with request.app.state.session_factory() as s:
            for sh, w, h in (await s.execute(select(UiDesignerAsset.sha256, UiDesignerAsset.width,
                                                    UiDesignerAsset.height).where(
                    UiDesignerAsset.sha256.in_(shas)))):
                sizes[sh] = {"w": w, "h": h}
    try:
        async with request.app.state.session_factory() as s:
            out = await _renderer(request).svg(s, doc=view["doc"], mode=mode, node=node, annotate=annotate,
                                               assets=sizes)
    except UiDesignerError as e:
        raise ud_http(e)
    return Response(content=out["svg"], media_type="image/svg+xml", headers=headers)


@router.get(PREFIX + "/docs/{slug}/resolved")
async def ui_document_resolved(slug: str, request: Request, mode: str | None = None,
                               version: int | None = Query(None, ge=1),
                               caller: Caller = Depends(require_ui_designer_access(*READ))):
    """The resolved tree (boxes, no glyph data) for the web overlay."""
    view, sha, cc = await _doc_at(request, caller, slug, version)
    etag = '"' + render_client.cache_key(sha, "resolved", mode, 1, False) + '"'
    headers = {"Cache-Control": cc, "ETag": etag}
    if _etag_hit(request, etag):
        return Response(status_code=304, headers=headers)
    try:
        tree = await _resolved_document(request, view["doc"], mode)
    except UiDesignerError as e:
        raise ud_http(e)
    return JSONResponse(tree, headers=headers)


async def _resolved_document(request: Request, doc: dict, mode: str | None):
    async with request.app.state.session_factory() as session:
        return await _renderer(request).resolved(session, doc=doc, mode=mode)


@router.get(PREFIX + "/docs/{slug}/fonts", response_model=S.UiFontsOut)
async def list_ui_fonts(slug: str, request: Request, version: int | None = Query(None, ge=1),
                        caller: Caller = Depends(require_ui_designer_access(*READ))):
    view, _, _ = await _doc_at(request, caller, slug, version)
    installed = []
    async with request.app.state.session_factory() as session:
        approved = await store.placed_fonts(session, view["id"])
        for ref in view["doc"].get("fonts", []):
            if ref["sha256"] not in approved:
                raise ud_http(invalid([issue("UD-FONT", "$.fonts", "font placement is unavailable")]))
            row = await store.get_font(session, ref["sha256"])
            installed.append(row.font_meta)
    return S.UiFontsOut(slug=slug, version=view.get("at_version") or view["version"], fonts=installed)


async def _font_receipt(session, slug: str, scope: str, request_id: str, fingerprint: str):
    result = await store._receipt_lookup(session, scope, request_id, "font-upload", fingerprint)
    if result is not None:
        current = await store.get_document(session, slug)
        if current["id"] != result["document_id"]:
            raise UiDesignerError(ud_errors.UD_NOT_FOUND, "font source document identity changed")
    return result


@router.post(PREFIX + "/docs/{slug}/fonts", response_model=S.UiFontWriteOut)
async def upload_ui_font(slug: str, body: S.UiFontUploadIn, request: Request,
                         caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    import hashlib
    from pydantic import ValidationError
    from agentplatform.ui_designer.ops import apply_ops
    try:
        data = base64.b64decode(body.data_base64, validate=True)
    except ValueError:
        raise HTTPException(422, "data_base64 must be strict base64") from None
    if not data or len(data) > 1 << 20:
        raise HTTPException(413, "a font is at most 1MiB")
    sha = hashlib.sha256(data).hexdigest()
    stable = {"sha256": sha}
    run_id = await caller_run_id(request, caller)
    scope = _scope(caller, run_id)
    reason = "font upload"
    fingerprint = store.edit_fingerprint(op="font-upload", slug=slug, ops=None,
        expected_version=body.expected_version, reason=reason, stable_args=stable)
    try:
        async with request.app.state.session_factory() as session:
            prior = await _font_receipt(session, slug, scope, body.request_id, fingerprint)
            if prior is not None:
                return S.UiFontWriteOut(**_write_out(prior, slug).model_dump(), fonts=prior["fonts"])
            view = await store.get_document(session, slug)
            if view["version"] != body.expected_version:
                apply_ops(view["doc"], [], version=view["version"], expected_version=body.expected_version,
                    ops_since=await store.chunks_after(session, view["id"], body.expected_version))
        await _check_write_budget(request, caller)
        try:
            metadata = S.UiFontInfo.model_validate(await _renderer(request).validate_font(data)).model_dump()
        except ValidationError as error:
            raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "font validator returned invalid metadata") from error
        if metadata["sha256"] != sha:
            raise UiDesignerError(ud_errors.UD_RENDER_FAILED, "font validator returned a different content hash")
        await require_ui_designer_access(*WRITE, write=True)(request)
        async with request.app.state.session_factory() as session:
            current = await store._live(session, slug, lock=True)
            if current.id != view["id"]:
                raise UiDesignerError(ud_errors.UD_NOT_FOUND, "font source document identity changed")
            prior = await _font_receipt(session, slug, scope, body.request_id, fingerprint)
            if prior is not None:
                return S.UiFontWriteOut(**_write_out(prior, slug).model_dump(), fonts=prior["fonts"])
            # Fail stale CAS before storing any bytes, even though rollback would
            # also remove a failed upload's blob and placement.
            if current.version != body.expected_version:
                apply_ops(current.doc, [], version=current.version, expected_version=body.expected_version)
            await _acl_lock(request, caller, session, slug)
            await store.add_document_font(session, current.id, data=data, metadata=metadata,
                                          placed_by=caller.participant, caps=caps(request))
            doc = dict(current.doc)
            refs = list(doc.get("fonts", []))
            ref = {key: metadata[key] for key in ("family", "weight", "sha256")}
            for existing in refs:
                if (existing["family"], existing["weight"]) == (ref["family"], ref["weight"]) and existing != ref:
                    raise invalid([issue("UD-FONT", "$.fonts", "this family and weight already has a different installed face")])
            if ref not in refs:
                refs.append(ref)
            doc["fonts"] = refs
            result = await store.edit_document(session, slug, ops=[{"op": "set_doc", "doc": doc}],
                expected_version=body.expected_version, author=caller.participant, run_id=run_id,
                reason=reason, request_id=body.request_id, scope=scope, caps=caps(request), fold=False,
                op="font-upload", _stable_args=stable)
            await session.commit()
        if not result.get("replayed"):
            await _emit(request, caller, "version", slug, result["version"], run_id)
        return S.UiFontWriteOut(**_write_out(result, slug).model_dump(), fonts=result["fonts"])
    except UiDesignerError as error:
        raise ud_http(error)


@router.get(PREFIX + "/docs/{slug}/assets", response_model=S.UiPlacedAssetsOut)
async def list_ui_placed_assets(slug: str, request: Request,
                                version: int | None = Query(None, ge=1),
                                limit: int = Query(50, ge=1, le=100),
                                cursor: str | None = Query(None, pattern=r"^[0-9a-f]{64}$"),
                                caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Document placements, including unused ones, never another upload/authorization door."""
    from agentplatform.db import UiDesignerDocumentAsset
    view, _, _ = await _doc_at(request, caller, slug, version)
    async with request.app.state.session_factory() as s:
        query = select(UiDesignerAsset.sha256, UiDesignerAsset.mime, UiDesignerAsset.width, UiDesignerAsset.height).join(
            UiDesignerDocumentAsset, UiDesignerDocumentAsset.sha256 == UiDesignerAsset.sha256).where(
            UiDesignerDocumentAsset.document_id == view["id"])
        if cursor is not None:
            query = query.where(UiDesignerAsset.sha256 > cursor)
        rows = (await s.execute(query.order_by(UiDesignerAsset.sha256).limit(limit + 1))).all()
    more = len(rows) > limit
    rows = rows[:limit]
    assets = [{"sha256": row.sha256, "mime": row.mime, "width": row.width, "height": row.height} for row in rows]
    return S.UiPlacedAssetsOut(slug=slug, version=view["at_version"], assets=assets,
                              next_cursor=rows[-1].sha256 if more else None)


@router.get(PREFIX + "/assets/{sha256}", response_class=Response)
async def get_ui_asset(request: Request, sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
                       caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Placed image bytes. Content-addressed, so `immutable`; inline only for
    the four rasters, everything else is an attachment."""
    from agentplatform.db import UiDesignerDocument, UiDesignerDocumentAsset
    who = await access.subject(request, caller)
    async with request.app.state.session_factory() as s:
        allowed = await s.scalar(select(UiDesignerDocument.id).join(UiDesignerDocumentAsset,
            UiDesignerDocumentAsset.document_id == UiDesignerDocument.id).where(
            UiDesignerDocumentAsset.sha256 == sha256, UiDesignerDocument.deleted_at.is_(None),
            access.readable_clause(who)).limit(1))
        if allowed is None:
            raise HTTPException(404, "unknown asset")
        request.state.ui_asset_document_id = allowed
        row = await store.get_asset(s, sha256)
    if row is None:
        raise HTTPException(404, "unknown asset")
    inline = row.mime in artifact_store.RASTERS
    return Response(content=row.data, media_type=row.mime if inline else "application/octet-stream",
                    headers={"X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store",
                             "Content-Security-Policy": "default-src 'none'; sandbox",
                             "Content-Disposition": f'{"inline" if inline else "attachment"}; '
                                                    f'filename="{sha256[:16]}"'})

# Figma import control plane. The PAT exists only in the executor's environment.
FigmaNodeId = Annotated[str, StringConstraints(pattern=r'^[A-Za-z0-9:_-]{1,128}$')]


class FigmaImportIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    file_key: str = Field(pattern=r'^[A-Za-z0-9_-]{1,128}$')
    slug: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,63}$')
    title: str = Field(default='', max_length=120)
    pages: list[FigmaNodeId] = Field(default_factory=list, max_length=50)
    node_ids: list[FigmaNodeId] = Field(default_factory=list, max_length=8)


class FigmaImportOut(BaseModel):
    id: str
    slug: str
    file_key: str
    state: Literal['queued', 'running', 'done', 'failed']
    pages: dict[str, Any]
    report: dict[str, Any]
    error: str | None = None
    created_at: datetime
    updated_at: datetime


async def require_figma_admin(request: Request,
                              _admin: str = Depends(require_admin)) -> Caller:
    caller = await require_ui_designer_access('admin', write=True)(request)
    if caller.agent is not None:
        raise HTTPException(403, 'Figma imports require a human administrator')
    return caller


@router.post(PREFIX + '/imports', status_code=202, response_model=FigmaImportOut)
async def start_figma_import(body: FigmaImportIn, request: Request,
                            caller: Caller = Depends(require_figma_admin)):
    from agentplatform.db import UiDesignerDocument, UiDesignerImport
    from agentplatform.ui_designer.figma_import_job import job_view
    async with request.app.state.session_factory() as s:
        # Serialize admission across API replicas; no credential or network work
        # occurs in this short transaction. SQLite tests serialize writes itself.
        if s.bind.dialect.name == 'postgresql':
            await s.execute(text('SELECT pg_advisory_xact_lock(460011)'))
        active = (await s.execute(select(func.count()).select_from(UiDesignerImport).where(
            UiDesignerImport.state.in_(('queued', 'running'))))).scalar_one()
        if active >= 10:
            raise HTTPException(429, 'ten imports are already active; wait for one to finish')
        if (await s.execute(select(UiDesignerDocument.id).where(UiDesignerDocument.slug == body.slug,
            UiDesignerDocument.deleted_at.is_(None)))).first():
            raise HTTPException(409, 'a document with that slug already exists')
        job, created = await store.start_import(s, slug=body.slug, file_key=body.file_key,
                                                initiated_by=caller.participant)
        if not created:
            raise HTTPException(409, 'an import with that slug is active')
        job.report = {'options':body.model_dump(exclude={'file_key','slug'})}
        await s.commit()
        return job_view(job)


@router.get(PREFIX + '/imports/{import_id}', response_model=FigmaImportOut)
async def get_figma_import(import_id: str, request: Request,
                          caller: Caller = Depends(require_figma_admin)):
    from agentplatform.db import UiDesignerImport
    from agentplatform.ui_designer.figma_import_job import job_view
    async with request.app.state.session_factory() as s:
        job = await s.get(UiDesignerImport, import_id)
        if job is None:
            raise HTTPException(404, 'import not found')
        return job_view(job)


@router.post('/api/internal/ui-designer/figma-fetch', include_in_schema=False)
async def internal_figma_fetch(body: dict, request: Request):
    import json
    import httpx
    header = request.headers.get('authorization','')
    token = header[7:] if header.startswith('Bearer ') else ''
    validator = getattr(request.app.state, 'sa_validator', None)
    identity = await validator(token) if token.count('.') == 2 and validator else None
    settings = request.app.state.settings
    expected = f'system:serviceaccount:{settings.k8s_namespace}:{settings.dispatcher_service_account}'
    if identity != expected:
        raise HTTPException(403, 'dispatcher identity required')
    allowed = {'file_key','mode','page_ids','node_ids','image_refs','version'}
    if set(body) - allowed or body.get('mode') not in ('file_meta','tree_page','asset_batch','references'):
        raise HTTPException(422, 'invalid Figma fetch request')
    try:
        async with httpx.AsyncClient(timeout=320) as client:
            r = await client.post(request.app.state.settings.executor_url + '/run',
                                  json={'tool':'figma_import','args':body,'caller':{}})
        r.raise_for_status()
        result = r.json()
        # Errors are fixed codes; raw stderr, upstream URLs and bodies never leave this door.
        if not result.get('ok'):
            return {'ok':False,'error':'UD-IMPORT-FETCH-FAILED'}
        meta = json.loads(result.get('output') or '{}')
        safe = {k:v for k,v in meta.items() if k in ('ok','error','retry_after_seconds','frames','name','version','pages','missing')}
        return {'ok':True,'meta':safe,'files':result.get('files',[])}
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, 'Figma fetch unavailable')
