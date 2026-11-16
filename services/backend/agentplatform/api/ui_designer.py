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
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy import select

from agentplatform import artifact_store
from agentplatform.agentspec import TOOL_ARTIFACTS, TOOL_UI_DESIGNER
from agentplatform.api import agents as agents_api
from agentplatform.api import relay as relay_api
from agentplatform.api import schemas as S
from agentplatform.api.artifacts import _authorize_read, _bounded_body, _may_modify
from agentplatform.api.auth import require_browser_session
from agentplatform.api.relay import READ, WRITE, Caller, require_relay_access
from agentplatform.api.tickets import _run_of
from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.db import Conversation, Run, UiDesignerAsset, utcnow
from agentplatform.relay import is_open_channel, room_home
from agentplatform.ui_designer import errors as ud_errors
from agentplatform.ui_designer import render_client, store, views
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
                bound = request.app.state.settings.ui_designer_max_body_bytes
                try:
                    request = await _bounded_body(request, bound)
                except HTTPException as e:
                    if e.status_code != 413:
                        raise
                    raise ud_http(UiDesignerError(
                        ud_errors.UD_BODY_TOO_LARGE, f"a request body is at most {bound} bytes",
                        detail={"limit": bound})) from e
            return await inner(request)
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
        if caller.agent is None:
            return caller
        granted = await agents_api._caller_platform_tools(request, caller.agent)
        if caller.agent not in relay_api._agent_set(request):
            raise HTTPException(403, "unknown or disabled agent")
        if TOOL_UI_DESIGNER not in granted:
            raise HTTPException(403, NOT_GRANTED)
        if write:
            async with request.app.state.session_factory() as s:
                await _run_of(s, request, caller, writing=True)
        return caller

    return dep


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
            out = await store.place_asset(s, slug, data=data, mime=art.mime,
                                          placed_by=caller.participant,
                                          placed_from=f"artifact:{art.id}",
                                          width=art.width, height=art.height, caps=caps(request))
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    return out


# --- documents ------------------------------------------------------------------

def _write_out(out: dict, slug: str) -> S.UiDocWriteOut:
    return S.UiDocWriteOut(slug=out.get("slug", slug), version=out["version"],
                           text=out.get("text", ""), warnings=out.get("warnings", []),
                           replayed=bool(out.get("replayed")), folded=bool(out.get("folded")))


@router.get(PREFIX + "/docs", response_model=S.UiDocList)
async def list_ui_documents(request: Request, q: str | None = Query(None, max_length=120),
                            limit: int = Query(50, ge=1, le=200), before: str | None = None,
                            caller: Caller = Depends(require_ui_designer_access(*READ))):
    async with request.app.state.session_factory() as s:
        try:
            rows = await store.list_documents(s, limit=limit + 1, q=q, before=before)
        except UiDesignerError as e:
            raise ud_http(e)
    more = len(rows) > limit
    rows = rows[:limit]
    return S.UiDocList(docs=rows, next_before=rows[-1]["slug"] if more and rows else None)


@router.post(PREFIX + "/docs", response_model=S.UiDocWriteOut)
async def create_ui_document(body: S.UiDocCreateIn, request: Request,
                             caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    run_id = await caller_run_id(request, caller)
    await _check_write_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            out = await store.create_document(
                s, slug=body.slug, title=body.title, owner=caller.participant, author=caller.participant,
                run_id=run_id, doc=body.doc, description=body.description, reason=body.reason,
                caps=caps(request), request_id=body.request_id, scope=_scope(caller, run_id))
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    out.setdefault("text", "created")
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
            tree = await _renderer(request).resolved(doc=view["doc"], mode=mode)
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
        tree = await _renderer(request).resolved(doc=doc, mode=mode)
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
    await _check_write_budget(request, caller)
    if nodes:
        _check_pixel_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            res = await store.edit_document(
                s, slug, ops=body.ops, expected_version=body.expected_version,
                author=caller.participant, run_id=run_id, reason=body.reason,
                request_id=body.request_id, scope=_scope(caller, run_id),
                wants_snapshot=bool(nodes), caps=caps(request))
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    out = _write_out(res, slug)
    if nodes and not res.get("replayed"):
        await _attach_snapshots(request, caller, out, slug, res["doc"], nodes, body)
    return out


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
            res = await store.restore_version(
                s, slug, body.version, expected_version=body.expected_version,
                author=caller.participant, run_id=run_id, request_id=body.request_id,
                scope=_scope(caller, run_id), caps=caps(request))
        except UiDesignerError as e:
            raise ud_http(e)
        await s.commit()
    return _write_out(res, slug)


# --- delete and reclaim (Kyle's session) ------------------------------------------

@router.delete(PREFIX + "/docs/{slug}", response_model=S.UiDocDeleteOut)
async def delete_ui_document(slug: str, request: Request, hard: bool = False,
                             caller: Caller = Depends(require_ui_designer_access(*WRITE, write=True))):
    """Soft delete: the owner, an `agents_edit` holder or the admin. `hard=true`
    removes the document, its versions, comments and placements now and runs
    asset GC: the administrator's browser session only."""
    async with request.app.state.session_factory() as s:
        try:
            if hard:
                _session_admin(request, caller)
                out = await store.hard_delete(s, slug)
                await s.commit()
                return S.UiDocDeleteOut(hard=True, assets_removed=out["assets_removed"],
                                        bytes_freed=out["bytes_freed"])
            view = await store.get_document(s, slug)
            await _may_modify(request, caller, view["owner"])
            await store.soft_delete(s, slug)
            await s.commit()
        except UiDesignerError as e:
            raise ud_http(e)
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
    cc = "private, max-age=31536000, immutable" if state == "closed" else "no-cache"
    return view, sha, cc


def _etag_hit(request: Request, etag: str) -> bool:
    return request.headers.get("if-none-match") == etag


@router.get(PREFIX + "/docs/{slug}/render", response_class=Response)
async def render_ui_document(slug: str, request: Request, node: str | None = None,
                             mode: str | None = None, scale: float = 1, annotate: bool = False,
                             version: int | None = Query(None, ge=1),
                             caller: Caller = Depends(require_ui_designer_access(*READ))):
    _check_scale(scale)
    view, sha, cc = await _doc_at(request, caller, slug, version)
    # An agent's picture goes into a model's context: the byte ladder applies.
    max_bytes = request.app.state.settings.ui_designer_snapshot_max_bytes if caller.agent is not None else None
    etag = '"' + render_client.cache_key(sha, node, mode, scale, annotate, max_bytes=max_bytes) + '"'
    headers = {"Cache-Control": cc, "ETag": etag, "X-Content-Type-Options": "nosniff"}
    if _etag_hit(request, etag):
        return Response(status_code=304, headers=headers)
    _check_pixel_budget(request, caller)
    async with request.app.state.session_factory() as s:
        try:
            images = await _renderer(request).render(
                s, document_id=view["id"], doc=view["doc"], doc_sha256=sha, mode=mode, nodes=[node],
                scale=scale, annotate=annotate, max_bytes=max_bytes)
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
               "Content-Security-Policy": "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"}
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
        out = await _renderer(request).svg(doc=view["doc"], mode=mode, node=node, annotate=annotate,
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
        tree = await _renderer(request).resolved(doc=view["doc"], mode=mode)
    except UiDesignerError as e:
        raise ud_http(e)
    return JSONResponse(tree, headers=headers)


@router.get(PREFIX + "/assets/{sha256}", response_class=Response)
async def get_ui_asset(request: Request, sha256: str = Path(pattern=r"^[0-9a-f]{64}$"),
                       caller: Caller = Depends(require_ui_designer_access(*READ))):
    """Placed image bytes. Content-addressed, so `immutable`; inline only for
    the four rasters, everything else is an attachment."""
    async with request.app.state.session_factory() as s:
        row = await store.get_asset(s, sha256)
    if row is None:
        raise HTTPException(404, "unknown asset")
    inline = row.mime in artifact_store.RASTERS
    return Response(content=row.data, media_type=row.mime if inline else "application/octet-stream",
                    headers={"X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, max-age=31536000, immutable",
                             "Content-Security-Policy": "default-src 'none'; sandbox",
                             "Content-Disposition": f'{"inline" if inline else "attachment"}; '
                                                    f'filename="{sha256[:16]}"'})
