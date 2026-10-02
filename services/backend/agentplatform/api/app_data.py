"""State Apps over HTTP (docs/design/39).

Two doors, never the same caller:

- **Kyle's read routes** (`GET /api/app-data/apps…`) serve the console. They
  answer only Kyle's browser session (`auth_kind == "session"`, role
  `admin`): not an admin API key, not another login, not an agent. Response
  shapes are the contract at the top of services/web/src/lib/appData.ts, and
  errors are FastAPI's `{"detail": "<text>"}`.
- **Agent routes** (`POST /api/app-data/agent/…`) are what the `apps` and
  `app_data` broker tools call. They answer only an agent run whose grant set
  (the run token's frozen tools when it has them) holds the tool: `apps` for
  the builder actions, `app_data` for records. No run, no App access. A
  builder acts only on Apps it owns (the lifecycle checks); a records call
  runs through the records engine as the agent, so the App's facts decide.
  Errors carry `{"detail": {"code", "message", "detail"}}` so the tools can
  act on the stable code.
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentplatform.api.auth import authenticate
from agentplatform.appdata import lifecycle as L
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import json_schemas
from agentplatform.appdata.lifecycle import Actor
from agentplatform.appdata.records import get_record, load_app, plan_delete
from agentplatform.appdata.views import run_view
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


async def _agent(request: Request, tool: str) -> Actor:
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
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
    return await _agent(request, TOOL_APP_DATA)


def _for_web(exc: RecordError) -> HTTPException:
    return HTTPException(exc.status, exc.message)


def _for_agent(exc: RecordError) -> HTTPException:
    return HTTPException(exc.status, exc.as_dict())


# --- Kyle's read routes -----------------------------------------------------------------

@router.get("/api/app-data/apps")
async def state_apps_list(request: Request, actor: Actor = Depends(kyle_session)):
    async with request.app.state.session_factory() as s:
        return await L.list_apps(s, actor)


@router.get("/api/app-data/apps/{app_id}")
async def state_app_get(request: Request, app_id: str,
                        actor: Actor = Depends(kyle_session)):
    async with request.app.state.session_factory() as s:
        try:
            return await L.get_app(s, actor, app_id)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.get("/api/app-data/apps/{app_id}/pages/{page}")
async def state_app_page(request: Request, app_id: str, page: str,
                   actor: Actor = Depends(kyle_session)):
    async with request.app.state.session_factory() as s:
        try:
            return await published_page(s, actor.caller, app_id, page)
        except RecordError as exc:
            raise _for_web(exc) from None


@router.get("/api/app-data/apps/{app_id}/views/{view}")
async def state_app_view(request: Request, app_id: str, view: str,
                    actor: Actor = Depends(kyle_session)):
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
            return await published_view(s, actor.caller, app_id, view, params, limit=limit,
                                        cursor=query.get("cursor"))
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
    return {"app_id": app.id, "app_name": app.name, "page": name,
            "version": L.page_version(await L._rows(session, app.id), app, name),
            "definition": L.page_for_web(page)}


async def published_view(session, caller: Caller, app_ref: str, name: str, params: dict,
                         *, limit=None, cursor=None) -> dict:
    """A retired App's views are stopped; its records stay."""
    app, ctx = await _loaded(session, app_ref)
    if app.status != "active":
        raise RecordError("AD-APP-RETIRED", f"App {app.name} is retired; its views are "
                          "stopped", 409)
    return await run_view(session, ctx, caller, name, params, limit=limit, cursor=cursor)


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
    kind: Literal["collection", "view", "page"]
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
    return await _call(request, lambda s: L.get_app(s, actor, body.app))


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
        as_=body.as_, samples=body.samples, limit=body.limit, cursor=body.cursor))


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
    return await _call(request, lambda s: L.health(s, actor, body.app))


# --- agent routes: `app_data` -----------------------------------------------------------

class QueryIn(AppRef):
    view: str
    params: dict[str, Any] = Field(default_factory=dict)
    limit: int | None = None
    cursor: str | None = None


class RecordRef(AppRef):
    collection: str
    id: str


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


class DeletePreviewIn(AppRef):
    collection: str
    ids: list[str] = Field(min_length=1, max_length=100)


@router.post("/api/app-data/agent/records/describe")
async def records_describe(request: Request, body: AppRef,
                           actor: Actor = Depends(records_caller)):
    return await _call(request, lambda s: L.describe_records(s, actor.caller, body.app))


@router.post("/api/app-data/agent/records/query")
async def records_query(request: Request, body: QueryIn,
                        actor: Actor = Depends(records_caller)):
    return await _call(request, lambda s: published_view(
        s, actor.caller, body.app, body.view, body.params, limit=body.limit,
        cursor=body.cursor))


@router.post("/api/app-data/agent/records/get")
async def records_get(request: Request, body: RecordRef,
                      actor: Actor = Depends(records_caller)):
    async def fn(s):
        _, ctx = await _loaded(s, body.app)
        return await get_record(s, ctx, actor.caller, body.collection, body.id)
    return await _call(request, fn)


@router.post("/api/app-data/agent/records/create")
async def records_create(request: Request, body: RecordCreateIn,
                         actor: Actor = Depends(records_caller)):
    return await _call(request, lambda s: L.record_create(
        s, actor, body.app, request_id=body.request_id, collection=body.collection,
        values=body.values))


@router.post("/api/app-data/agent/records/update")
async def records_update(request: Request, body: RecordUpdateIn,
                         actor: Actor = Depends(records_caller)):
    return await _call(request, lambda s: L.record_update(
        s, actor, body.app, request_id=body.request_id, collection=body.collection,
        record_id=body.id, values=body.values, expected_version=body.expected_version))


@router.post("/api/app-data/agent/records/delete")
async def records_delete(request: Request, body: RecordDeleteIn,
                         actor: Actor = Depends(records_caller)):
    return await _call(request, lambda s: L.record_delete(
        s, actor, body.app, request_id=body.request_id, collection=body.collection,
        record_id=body.id, expected_version=body.expected_version))


@router.post("/api/app-data/agent/records/delete_preview")
async def records_delete_preview(request: Request, body: DeletePreviewIn,
                                 actor: Actor = Depends(records_caller)):
    async def fn(s):
        _, ctx = await _loaded(s, body.app)
        return await plan_delete(s, ctx, actor.caller, body.collection, body.ids)
    return await _call(request, fn)
