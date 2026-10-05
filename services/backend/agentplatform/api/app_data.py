"""State Apps over HTTP (docs/design/39).

Two doors, never the same caller:

- **Browser routes** (`GET /api/app-data/apps…`) answer Kyle's session and
  the named QA reader login. QA sees only approved shares and fields, no
  builder details or actions. Proposal decisions and quota routes still
  answer only Kyle's admin session: never an API key or agent. Response
  shapes are the contract in services/web/src/lib/appData.ts; errors are
  FastAPI's `{"detail": "<text>"}`.
- **Agent routes** (`POST /api/app-data/agent/…`) are what the `apps` and
  `app_data` broker tools call. They answer only an agent run whose grant set
  (the run token's frozen tools when it has them) holds the tool: `apps` for
  the builder actions, `app_data` for records. No run, no App access. A
  builder acts only on Apps it owns (the lifecycle checks); a records call
  runs through the records engine as the agent, so the App's facts decide.
  Errors carry `{"detail": {"code", "message", "detail"}}` so the tools can
  act on the stable code.

  A **tool-call credential** (an agent acting through a tool, presented by the
  executor) reaches the records routes only: it holds no grant, so it is
  judged by the credential's `app_scope` (`require_scope`) before the engine,
  and its writes are stamped `author = agent:<name>, via = tool:<name>`. The
  builder routes and Kyle's routes refuse it.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from agentplatform.api.auth import authenticate
from agentplatform.appdata import credentials as tc
from agentplatform.appdata import lifecycle as L
from agentplatform.appdata import proposals as P
from agentplatform.appdata import quotas
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import TextBlock, ToolViewDef, json_schemas
from agentplatform.appdata.lifecycle import Actor
from agentplatform.appdata.models import AppDataProposal
from agentplatform.appdata.records import (get_record, get_record_version, load_app,
                                           plan_delete, record_history)
from agentplatform.appdata.views import check_view_access, run_view
from agentplatform.db import AgentDef

router = APIRouter()

TOOL_APPS = "mcp__platform__apps"
TOOL_APP_DATA = "mcp__platform__app_data"
KYLE = Actor("kyle")
# Reserved query names on a view read; every other query parameter is a view
# parameter.
_PAGING = ("limit", "cursor")


# --- auth -------------------------------------------------------------------------------

async def kyle_session(request: Request) -> Actor:
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    _, role = ident
    if getattr(request.state, "auth_kind", None) != "session" or role != "admin":
        raise HTTPException(403, "State Apps are read here only from Kyle's browser session")
    return KYLE


async def app_reader_session(request: Request) -> Actor | Caller:
    """A browser read is Kyle's, or the one QA login under its App facts.

    A reader API key and an arbitrary reader account are not a QA login.
    Write routes keep kyle_session and never accept this dependency.
    """
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    name, role = ident
    if getattr(request.state, "auth_kind", None) != "session":
        raise HTTPException(403)
    if role == "admin":
        return KYLE
    if name == "qa" and role == "reader":
        return Caller("login:qa")
    raise HTTPException(403)


def _reader_caller(actor: Actor | Caller) -> Caller:
    return actor.caller if isinstance(actor, Actor) else actor


async def _agent(request: Request, tool: str) -> Actor:
    if await authenticate(request) is None:
        raise HTTPException(401)
    return await _run_agent(request, tool)


async def _run_agent(request: Request, tool: str) -> Actor:
    """The checks after authentication: an agent run holding `tool`."""
    if getattr(request.state, "auth_kind", None) == tc.KIND_TOOL_CALL:
        raise HTTPException(403, "a tool call reaches only the app_data record routes")
    agent = getattr(request.state, "api_key_agent", None)
    run_id = getattr(request.state, "api_key_run_id", None)
    if not agent:
        raise HTTPException(403, "these routes serve agent runs through the apps and "
                                 "app_data tools")
    if not run_id:
        raise HTTPException(403, "no run, no App access: a run-scoped agent identity "
                                 "is required")
    frozen = getattr(request.state, "frozen_tools", None)
    if frozen is not None:
        granted = frozen
    else:
        async with request.app.state.session_factory() as s:
            row = await s.get(AgentDef, agent)
            granted = row.platform_tools if row else []
    if tool not in (granted or []):
        raise HTTPException(403, f"{tool} is not granted to this run")
    try:
        return Actor(f"agent:{agent}", run_id=run_id)
    except ValueError:
        raise HTTPException(403, "not an agent principal") from None


async def builder(request: Request) -> Actor:
    return await _agent(request, TOOL_APPS)


async def records_caller(request: Request) -> Actor:
    """An agent run holding `app_data`, or a tool-call credential: the agent
    acting through the tool, which each route keeps to the credential's
    scope with `_scoped`."""
    if await authenticate(request) is None:
        raise HTTPException(401)
    caller = tc.tool_call_caller(request)
    if caller is None:
        return await _run_agent(request, TOOL_APP_DATA)
    try:
        return Actor(caller.principal, run_id=request.state.api_key_run_id,
                     via_tool=caller.via_tool)
    except ValueError:
        raise HTTPException(403, "not an agent principal") from None


async def _scoped(request: Request, session, app_ref: str, collection: str, verb: str):
    """The App a records call names, after the tool-call ceiling: a no-op for
    every other caller."""
    app = await L._app(session, app_ref)
    tc.require_scope(request, app.id, collection, verb)
    return app


def _scope_for(request: Request, app_id: str) -> set[str] | None:
    """The collections a tool-call credential reaches in an App, or None for
    a caller with no credential."""
    if getattr(request.state, "auth_kind", None) != tc.KIND_TOOL_CALL:
        return None
    return {c for entry in request.state.tool_call.get("app_scope") or []
            if entry["app_id"] == app_id for c in entry["collections"]}


def _view_in_scope(ctx, view: dict, scope: set[str]) -> bool:
    """A tool view names source roles, while a collection view names one collection."""
    if "collection" in view:
        return view["collection"] in scope
    fact = ctx.bundle.app_tools.get(view.get("tool"))
    return fact is not None and all(
        role in fact.roles and fact.roles[role].collection in scope
        for role in view.get("sources", []))


def _for_web(exc: RecordError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _for_agent(exc: RecordError) -> HTTPException:
    return HTTPException(exc.status, exc.as_dict())


# --- Kyle's read routes -----------------------------------------------------------------

@router.get("/api/app-data/apps")
async def state_apps_list(request: Request,
                          actor: Actor | Caller = Depends(app_reader_session)):
    async with request.app.state.session_factory() as s:
        return await L.list_apps(s, actor)


@router.get("/api/app-data/apps/{app_id}")
async def state_app_get(request: Request, app_id: str,
                        actor: Actor | Caller = Depends(app_reader_session)):
    async with request.app.state.session_factory() as s:
        try:
            if isinstance(actor, Caller):
                return await L.get_readable_app(s, actor, app_id)
            return await L.get_app(s, actor, app_id)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.get("/api/app-data/proposals")
async def state_proposals_list(request: Request, app: str | None = None,
                               state: str | None = None,
                               actor: Actor = Depends(kyle_session)):
    async with request.app.state.session_factory() as s:
        try:
            if app:
                return await P.list_proposals(s, actor, app, state=state)
            stmt = select(AppDataProposal)
            if state is not None:
                stmt = stmt.where(AppDataProposal.state == state)
            rows = (await s.execute(stmt.order_by(AppDataProposal.created_at.desc(),
                                                  AppDataProposal.id).limit(100)))
            return [P.view(p) for p in rows.scalars()]
        except RecordError as exc:
            raise _for_web(exc) from None


@router.get("/api/app-data/proposals/{proposal_id}")
async def state_proposal_get(request: Request, proposal_id: str,
                             actor: Actor = Depends(kyle_session)):
    async with request.app.state.session_factory() as s:
        try:
            return await P.review(s, actor, proposal_id)
        except RecordError as exc:
            raise _for_web(exc) from None


class ProposalDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str
    digest: str | None = None
    reason: str = ""


@router.post("/api/app-data/proposals/{proposal_id}/approve")
async def state_proposal_approve(request: Request, proposal_id: str,
                                 body: ProposalDecisionIn,
                                 actor: Actor = Depends(kyle_session)):
    if not body.digest:
        raise HTTPException(422, "approve requires the proposal digest shown in review")
    async with request.app.state.session_factory() as s:
        try:
            result = await P.approve(s, actor, proposal_id, request_id=body.request_id,
                                     digest=body.digest)
        except RecordError as exc:
            raise _for_web(exc) from None
    await P.notify(request.app.state.session_factory, request.app.state.producer,
                   proposal_id)
    return result


@router.post("/api/app-data/proposals/{proposal_id}/decline")
async def state_proposal_decline(request: Request, proposal_id: str,
                                 body: ProposalDecisionIn,
                                 actor: Actor = Depends(kyle_session)):
    async with request.app.state.session_factory() as s:
        try:
            result = await P.decline(s, actor, proposal_id, request_id=body.request_id,
                                     reason=body.reason)
        except RecordError as exc:
            raise _for_web(exc) from None
    await P.notify(request.app.state.session_factory, request.app.state.producer,
                   proposal_id)
    return result


@router.get("/api/app-data/apps/{app_id}/pages/{page}")
async def state_app_page(request: Request, app_id: str, page: str,
                   actor: Actor | Caller = Depends(app_reader_session)):
    async with request.app.state.session_factory() as s:
        try:
            return await published_page(s, _reader_caller(actor), app_id, page)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.get("/api/app-data/apps/{app_id}/records/{collection}/{record_id}/history")
async def state_app_record_history(request: Request, app_id: str, collection: str,
                                   record_id: str, limit: int = Query(default=20, ge=1, le=100),
                                   before_version: int | None = Query(default=None, ge=1),
                                   actor: Actor | Caller = Depends(app_reader_session)):
    async with request.app.state.session_factory() as s:
        try:
            app, ctx = await _loaded(s, app_id)
            if app.status != "active":
                raise RecordError("AD-APP-RETIRED", "this App is retired", 409)
            return await record_history(s, ctx, _reader_caller(actor), collection,
                                        record_id, limit=limit,
                                        before_version=before_version)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.get("/api/app-data/apps/{app_id}/views/{view}")
async def state_app_view(request: Request, app_id: str, view: str,
                    actor: Actor | Caller = Depends(app_reader_session)):
    query = request.query_params
    params = {k: v for k, v in query.items() if k not in _PAGING}
    limit = query.get("limit")
    if limit is not None:
        try:
            limit = int(limit)
        except ValueError:
            raise HTTPException(422, "limit is a number from 1 to 200") from None
    async with request.app.state.session_factory() as s:
        try:
            return await published_view(s, _reader_caller(actor), app_id, view, params, limit=limit,
                                        cursor=query.get("cursor"), app_state=request.app.state)
        except RecordError as exc:
            raise _for_web(exc) from None


# --- Kyle's quota routes ------------------------------------------------------------------
# Limits are platform-owned: only Kyle reads or moves them, and only here.

class QuotaIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A limit's name -> a whole number, or null to return it to the default.
    limits: dict[str, Any]


def _quota_scope(scope_kind: str, scope_id: str) -> None:
    if not 1 <= len(scope_id) <= 160:
        raise HTTPException(422, "scope_id is 1 to 160 characters")


@router.get("/api/app-data/quotas/{scope_kind}/{scope_id}")
async def app_data_quota_get(request: Request, scope_kind: str, scope_id: str,
                             actor: Actor = Depends(kyle_session)):
    _quota_scope(scope_kind, scope_id)
    async with request.app.state.session_factory() as s:
        try:
            return await quotas.describe(s, scope_kind, scope_id)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.put("/api/app-data/quotas/{scope_kind}/{scope_id}")
async def app_data_quota_set(request: Request, scope_kind: str, scope_id: str,
                             body: QuotaIn, actor: Actor = Depends(kyle_session)):
    _quota_scope(scope_kind, scope_id)
    async with request.app.state.session_factory() as s:
        try:
            # kyle_session is the proof set_quota asks for.
            return await quotas.set_quota(s, scope_kind, scope_id, body.limits,
                                          set_by=actor.principal, is_kyle=True)
        except RecordError as exc:
            raise _for_web(exc) from None


async def _loaded(session, app_ref: str):
    app = await L._app(session, app_ref)
    ctx = await load_app(session, app.id)
    return app, ctx


async def published_page(session, caller: Caller, app_ref: str, name: str) -> dict:
    app, ctx = await _loaded(session, app_ref)
    page = ctx.bundle.pages.get(name)
    if page is None:
        raise RecordError("AD-NO-PAGE", f"no published page {name}", 404)
    if app.status != "active":
        raise RecordError("AD-APP-RETIRED", f"App {app.name} is retired", 409)
    if not L.can_read_page(ctx, caller, page):
        raise RecordError("AD-FORBIDDEN", f"{caller.principal} may not read page {name}", 403)
    definition = L.page_for_web(page, ctx.bundle,
                                with_actions=caller.principal == "kyle")
    if caller.principal == "login:qa":
        visible = []
        for block, component in zip(page.blocks, definition["components"]):
            if isinstance(block, TextBlock):
                visible.append(component)
                continue
            view = ctx.bundle.views[block.view]
            try:
                if isinstance(view, ToolViewDef):
                    if not L._can_read_view(ctx, caller, view):
                        continue
                else:
                    check_view_access(ctx, caller, view)
            except RecordError:
                continue
            if not isinstance(view, ToolViewDef):
                access = ctx.access(ctx.collection(view.collection), caller)
                key = "columns" if component["kind"] == "table" else (
                    "fields" if component["kind"] == "detail" else None)
                if key:
                    component[key] = [f for f in component[key] if access.can_read(f["field"])]
                    if not component[key]:
                        continue
                if component["kind"] in ("chart", "calendar"):
                    axes = ("x", "y") if component["kind"] == "chart" else ("day", "value")
                    if any(not access.can_read(component[axis]) for axis in axes):
                        continue
                if component["kind"] == "stat_row":
                    component["columns"] = [f for f in component["columns"]
                                            if access.can_read(f["field"])]
                    if not component["columns"]:
                        continue
            visible.append(component)
        if any(not isinstance(b, TextBlock) for b in page.blocks) and not any(
                c["kind"] != "text" for c in visible):
            raise RecordError("AD-FORBIDDEN", "no readable blocks on this page", 403)
        definition["components"] = visible
    return {"app_id": app.id, "app_name": app.name, "page": name,
            "version": L.page_version(await L._rows(session, app.id), app, name),
            "definition": definition}


async def published_view(session, caller: Caller, app_ref: str, name: str, params: dict,
                         *, limit=None, cursor=None, app_state=None) -> dict:
    """A retired App's views are stopped; its records stay."""
    app, ctx = await _loaded(session, app_ref)
    if app.status != "active":
        raise RecordError("AD-APP-RETIRED", f"App {app.name} is retired; its views are "
                          "stopped", 409)
    return await run_view(session, ctx, caller, name, params, limit=limit, cursor=cursor,
                          app_state=app_state)


# --- agent routes: `apps` ---------------------------------------------------------------

class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppRef(_Body):
    app: str = Field(min_length=1, max_length=64, description="App id or name")


class Empty(_Body):
    pass


class CreateIn(_Body):
    request_id: str
    name: str
    timezone: str = "UTC"
    description: str = ""


class DefRef(_Body):
    kind: Literal["collection", "view", "page", "tool"]
    name: str


class DraftIn(AppRef):
    request_id: str
    kind: str
    definition: dict[str, Any] | None = None
    name: str | None = None
    expected_revision: int | None = None
    remove: bool = False
    discard: bool = False
    reason: str = ""


class NotesIn(AppRef):
    """Without `text`, a read; with it, a write under `expected_revision`."""
    request_id: str | None = None
    text: str | None = None
    expected_revision: int | None = None


class ValidateIn(AppRef):
    expected_approved_version: int | None = None
    only: list[DefRef] | None = None


class PreviewIn(AppRef):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    kind: str
    name: str
    params: dict[str, Any] = Field(default_factory=dict)
    as_: str | None = Field(default=None, alias="as")
    samples: dict[str, list[dict[str, Any]]] | None = None
    limit: int | None = None
    cursor: str | None = None


class PublishIn(AppRef):
    request_id: str
    # Required, and null only before the first publish: the compare-and-swap
    # is the point, so it is never defaulted.
    expected_approved_version: int | None
    only: list[DefRef] | None = None
    reason: str = ""


class RollbackIn(AppRef):
    request_id: str
    to_version: int
    expected_approved_version: int | None
    reason: str = ""


class RetireIn(AppRef):
    request_id: str
    reason: str = ""


class ProposeIn(AppRef):
    request_id: str
    only: list[DefRef] | None = None
    rollback_to: int | None = None
    transfer_to: str | None = None
    reason: str = ""


class ProposalIn(_Body):
    proposal_id: str
    action: Literal["get", "withdraw"]
    request_id: str | None = None


def _only(only: list[DefRef] | None):
    return [o.model_dump() for o in only] if only is not None else None


async def _call(request: Request, fn):
    async with request.app.state.session_factory() as s:
        try:
            return await fn(s)
        except RecordError as exc:
            raise _for_agent(exc) from None


@router.post("/api/app-data/agent/apps/schema")
async def apps_schema(body: Empty, actor: Actor = Depends(builder)):
    return json_schemas()


@router.post("/api/app-data/agent/apps/list")
async def apps_list(request: Request, body: Empty, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.list_apps(s, actor))


@router.post("/api/app-data/agent/apps/create")
async def apps_create(request: Request, body: CreateIn, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.create(
        s, actor, request_id=body.request_id, name=body.name, timezone=body.timezone,
        description=body.description))


@router.post("/api/app-data/agent/apps/get")
async def apps_get(request: Request, body: AppRef, actor: Actor = Depends(builder)):
    async def get_with_proposals(s):
        result = await L.get_app(s, actor, body.app)
        result["open_proposals"] = await P.list_proposals(s, actor, body.app, state="open")
        return result
    return await _call(request, get_with_proposals)


@router.post("/api/app-data/agent/apps/propose")
async def apps_propose(request: Request, body: ProposeIn,
                       actor: Actor = Depends(builder)):
    result = await _call(request, lambda s: P.propose(
        s, actor, body.app, request_id=body.request_id, only=_only(body.only),
        rollback_to=body.rollback_to, transfer_to=body.transfer_to,
        reason=body.reason))
    await P.notify(request.app.state.session_factory, request.app.state.producer,
                   result["id"])
    return result


@router.post("/api/app-data/agent/apps/proposal")
async def apps_proposal(request: Request, body: ProposalIn,
                        actor: Actor = Depends(builder)):
    if body.action == "withdraw":
        if not body.request_id:
            raise HTTPException(422, {"code": "AL-ARGS", "message":
                                      "withdraw requires request_id"})
        result = await _call(request, lambda s: P.withdraw(
            s, actor, body.proposal_id, request_id=body.request_id))
        await P.notify(request.app.state.session_factory, request.app.state.producer,
                       body.proposal_id)
        return result
    return await _call(request, lambda s: P.get(s, actor, body.proposal_id))


@router.post("/api/app-data/agent/apps/draft")
async def apps_draft(request: Request, body: DraftIn, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.draft(
        s, actor, body.app, request_id=body.request_id, kind=body.kind,
        definition=body.definition, name=body.name, expected_revision=body.expected_revision,
        remove=body.remove, discard=body.discard, reason=body.reason))


@router.post("/api/app-data/agent/apps/notes")
async def apps_notes(request: Request, body: NotesIn, actor: Actor = Depends(builder)):
    if body.text is None:
        return await _call(request, lambda s: L.read_notes(s, actor, body.app))
    if body.expected_revision is None:
        raise HTTPException(422, {"code": "AL-ARGS", "message": "writing notes takes "
                                  "expected_revision (0 for the first write)"})
    return await _call(request, lambda s: L.write_notes(
        s, actor, body.app, request_id=body.request_id, text=body.text,
        expected_revision=body.expected_revision))


@router.post("/api/app-data/agent/apps/validate")
async def apps_validate(request: Request, body: ValidateIn, actor: Actor = Depends(builder)):
    kwargs = {}
    if "expected_approved_version" in body.model_fields_set:
        kwargs["expected_approved_version"] = body.expected_approved_version
    return await _call(request, lambda s: L.validate(s, actor, body.app, only=_only(body.only),
                                                     **kwargs))


@router.post("/api/app-data/agent/apps/preview")
async def apps_preview(request: Request, body: PreviewIn, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.preview(
        s, actor, body.app, kind=body.kind, name=body.name, params=body.params,
        as_=body.as_, samples=body.samples, limit=body.limit, cursor=body.cursor,
        app_state=request.app.state))


@router.post("/api/app-data/agent/apps/publish")
async def apps_publish(request: Request, body: PublishIn, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.publish(
        s, actor, body.app, request_id=body.request_id,
        expected_approved_version=body.expected_approved_version, only=_only(body.only),
        reason=body.reason))


@router.post("/api/app-data/agent/apps/rollback")
async def apps_rollback(request: Request, body: RollbackIn, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.rollback(
        s, actor, body.app, request_id=body.request_id, to_version=body.to_version,
        expected_approved_version=body.expected_approved_version, reason=body.reason))


@router.post("/api/app-data/agent/apps/retire")
async def apps_retire(request: Request, body: RetireIn, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.retire(s, actor, body.app,
                                                   request_id=body.request_id,
                                                   reason=body.reason))


@router.post("/api/app-data/agent/apps/authority")
async def apps_authority(request: Request, body: AppRef, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.authority(s, actor, body.app))


@router.post("/api/app-data/agent/apps/health")
async def apps_health(request: Request, body: AppRef, actor: Actor = Depends(builder)):
    return await _call(request, lambda s: L.health(
        s, actor, body.app, tool_registry=request.app.state.tool_registry))


# --- agent routes: `app_data` -----------------------------------------------------------

class QueryIn(AppRef):
    view: str
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = None
    cursor: str | None = None


class RecordRef(AppRef):
    collection: str
    id: str


class RecordHistoryIn(RecordRef):
    limit: int = Field(default=50, ge=1, le=100)
    before_version: int | None = Field(default=None, ge=1)


class RecordVersionIn(RecordRef):
    version: int = Field(ge=1)


class RecordCreateIn(AppRef):
    request_id: str
    collection: str
    values: dict[str, Any]


class RecordUpdateIn(RecordRef):
    request_id: str
    values: dict[str, Any]
    expected_version: int


class RecordDeleteIn(RecordRef):
    request_id: str
    expected_version: int | None = None


class RecordTransactionIn(AppRef):
    request_id: str
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    guards: list[dict[str, Any]] = Field(default_factory=list, max_length=100)


class DeletePreviewIn(AppRef):
    collection: str
    ids: list[str] = Field(min_length=1, max_length=100)


class RefreshIn(AppRef):
    view: str


class StagingOpenIn(AppRef):
    collections: list[str] = Field(min_length=1, max_length=16)


class StagingWriteIn(AppRef):
    set_id: str
    collection: str
    records: list[dict[str, Any]] = Field(min_length=1, max_length=5000)
    mode: Literal["insert", "upsert", "skip_existing"] = "insert"
    key: list[str] | None = None


class StagingFinishIn(AppRef):
    set_id: str


def _staging_call(request: Request) -> dict:
    """Staging lasts only for the executor's current, scoped tool call."""
    if getattr(request.state, "auth_kind", None) != tc.KIND_TOOL_CALL:
        raise HTTPException(403, "a scoped tool-call credential is required")
    return request.state.tool_call


@router.post("/api/app-data/agent/records/staging/open")
async def records_staging_open(request: Request, body: StagingOpenIn,
                               actor: Actor = Depends(records_caller)):
    claim = _staging_call(request)
    async def fn(s):
        from agentplatform.appdata.batch import open_staging_set
        app, ctx = await _loaded(s, body.app)
        for collection in body.collections:
            tc.require_scope(request, app.id, collection, "create")
        return await open_staging_set(s, ctx, actor.caller, body.collections,
                                      call_id=claim["call_id"], run_id=claim["run_id"])
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/staging/stage")
async def records_staging_stage(request: Request, body: StagingWriteIn,
                                actor: Actor = Depends(records_caller)):
    claim = _staging_call(request)
    async def fn(s):
        from agentplatform.appdata.batch import stage
        app, ctx = await _loaded(s, body.app)
        tc.require_scope(request, app.id, body.collection, "create")
        if body.mode == "upsert":
            tc.require_scope(request, app.id, body.collection, "update")
        return await stage(s, ctx, actor.caller, body.set_id, body.collection,
                           body.records, mode=body.mode, key=body.key,
                           call_id=claim["call_id"])
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/staging/commit")
async def records_staging_commit(request: Request, body: StagingFinishIn,
                                 actor: Actor = Depends(records_caller)):
    claim = _staging_call(request)
    async def fn(s):
        from agentplatform.appdata.batch import commit_staging_set
        from agentplatform.appdata.models import AppDataStagingSet
        app = await L._app(s, body.app)
        staging = await s.get(AppDataStagingSet, body.set_id)
        if staging is None or staging.app_id != app.id:
            raise RecordError("AD-STAGING-NOT-FOUND", "staging set not found", 404)
        for collection in staging.collection_versions:
            tc.require_scope(request, app.id, collection, "create")
        return await commit_staging_set(s, actor.caller, body.set_id,
                                        call_id=claim["call_id"])
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/staging/abandon")
async def records_staging_abandon(request: Request, body: StagingFinishIn,
                                  actor: Actor = Depends(records_caller)):
    claim = _staging_call(request)
    async def fn(s):
        from agentplatform.appdata.batch import abandon_staging_set
        app = await L._app(s, body.app)
        if _scope_for(request, app.id) is None:
            raise RecordError("AD-OUT-OF-SCOPE", "this App is outside the tool call", 403)
        return await abandon_staging_set(s, actor.caller, body.set_id,
                                         call_id=claim["call_id"])
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/request_refresh")
async def records_request_refresh(request: Request, body: RefreshIn,
                                  actor: Actor = Depends(records_caller)):
    if getattr(request.state, "auth_kind", None) != tc.KIND_TOOL_CALL:
        raise HTTPException(403, "refresh requests come from a batch writer's tool call")
    async def fn(s):
        from agentplatform.appdata.materialized import request_refresh
        app, ctx = await _loaded(s, body.app)
        view = ctx.bundle.views.get(body.view)
        if not isinstance(view, ToolViewDef) or view.materialize is None:
            raise RecordError("AD-NOT-MATERIALIZED", "no materialized view by that name", 404)
        fact = ctx.bundle.app_tools.get(view.tool)
        sources = {fact.roles[role].collection for role in view.sources} if fact else set()
        scope = request.state.tool_call.get("app_scope") or []
        if not any(entry["app_id"] == app.id
                   and set(entry["collections"]) & sources
                   and set(entry["verbs"]) & {"create", "update", "delete"}
                   for entry in scope):
            raise RecordError("AD-OUT-OF-SCOPE", "this tool call cannot request a refresh", 403)
        queued = await request_refresh(s, ctx, view)
        return {"queued": queued, "view": view.view}
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/describe")
async def records_describe(request: Request, body: AppRef,
                           actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await L._app(s, body.app)
        scope = _scope_for(request, app.id)
        if scope is not None and not scope:
            raise RecordError("AD-OUT-OF-SCOPE", f"this tool call may not use App {app.id}",
                              403)
        out = await L.describe_records(s, actor.caller, app.id)
        if scope is not None:
            ctx = await load_app(s, app.id)
            out["collections"] = [c for c in out["collections"] if c["collection"] in scope]
            out["views"] = [v for v in out["views"] if _view_in_scope(ctx, v, scope)]
        return out
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/query")
async def records_query(request: Request, body: QueryIn,
                        actor: Actor = Depends(records_caller)):
    async def fn(s):
        app, ctx = await _loaded(s, body.app)
        view = ctx.bundle.views.get(body.view)
        if isinstance(view, ToolViewDef):
            fact = ctx.bundle.app_tools.get(view.tool)
            if fact is None:
                raise RecordError("AD-TOOL-VIEW-DISABLED", "tool view binding is unavailable", 503)
            for role in view.sources:
                binding = fact.roles.get(role)
                if binding is None:
                    raise RecordError("AD-TOOL-VIEW-DISABLED", "tool view source is unavailable", 503)
                tc.require_scope(request, app.id, binding.collection, "read")
        elif view is not None:
            tc.require_scope(request, app.id, view.collection, "read")
        elif _scope_for(request, app.id) is not None:
            raise RecordError("AD-OUT-OF-SCOPE", f"this tool call may not read view "
                              f"{body.view}", 403)
        return await published_view(s, actor.caller, app.id, body.view, body.params,
                                    limit=body.limit, cursor=body.cursor,
                                    app_state=request.app.state)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/get")
async def records_get(request: Request, body: RecordRef,
                      actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "read")
        ctx = await load_app(s, app.id)
        return await get_record(s, ctx, actor.caller, body.collection, body.id)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/history")
async def records_history(request: Request, body: RecordHistoryIn,
                          actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "read")
        ctx = await load_app(s, app.id)
        return await record_history(s, ctx, actor.caller, body.collection, body.id,
                                    limit=body.limit, before_version=body.before_version)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/version")
async def records_version(request: Request, body: RecordVersionIn,
                          actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "read")
        ctx = await load_app(s, app.id)
        return await get_record_version(s, ctx, actor.caller, body.collection, body.id,
                                        body.version)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/create")
async def records_create(request: Request, body: RecordCreateIn,
                         actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "create")
        return await L.record_create(s, actor, app.id, request_id=body.request_id,
                                     collection=body.collection, values=body.values)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/update")
async def records_update(request: Request, body: RecordUpdateIn,
                         actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "update")
        return await L.record_update(
            s, actor, app.id, request_id=body.request_id, collection=body.collection,
            record_id=body.id, values=body.values, expected_version=body.expected_version)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/delete")
async def records_delete(request: Request, body: RecordDeleteIn,
                         actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "delete")
        return await L.record_delete(
            s, actor, app.id, request_id=body.request_id, collection=body.collection,
            record_id=body.id, expected_version=body.expected_version,
            check_plan=lambda plan: tc.require_plan_scope(request, app.id, plan))
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/transaction")
async def records_transaction(request: Request, body: RecordTransactionIn,
                              actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await L._app(s, body.app)
        return await L.record_transaction(
            s, actor, app.id, request_id=body.request_id,
            operations=body.operations, guards=body.guards,
            check_scope=lambda collection, verb: tc.require_scope(
                request, app.id, collection, verb),
            check_plan=lambda plan: tc.require_plan_scope(request, app.id, plan))
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/delete_preview")
async def records_delete_preview(request: Request, body: DeletePreviewIn,
                                 actor: Actor = Depends(records_caller)):
    async def fn(s):
        app = await _scoped(request, s, body.app, body.collection, "delete")
        ctx = await load_app(s, app.id)
        return await plan_delete(
            s, ctx, actor.caller, body.collection, body.ids,
            check_plan=lambda plan: tc.require_plan_scope(request, app.id, plan))
    return await _call(request, fn)
