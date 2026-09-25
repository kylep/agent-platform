"""The app registry surface (docs/design/11): what apps are declared
(apps/<name>/app.yaml in the synced checkout), what each needs, and whether
its Deployment is live. Apps serve their own UI/API behind /apps/<name>/ —
this router also provides the nginx auth_request endpoint that session-guards
those routes without the app ever seeing credentials, and a read-only proxy
so shell-less agents can query app APIs through the MCP broker."""
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, authenticate, require_admin, require_role
from agentplatform.app_collections import APP_NAME
from agentplatform.db import AppCollection, utcnow

router = APIRouter()


def _deployment_ready(request: Request, name: str) -> tuple[bool | None, int]:
    """(ready, ready_replicas) for Deployment ap-app-<name>; ready None when
    the k8s API isn't reachable (local dev) or the deployment doesn't exist."""
    apps_v1 = getattr(request.app.state, "k8s_apps_v1", None)
    if apps_v1 is None:
        try:
            from kubernetes import client, config
            config.load_incluster_config()
            apps_v1 = request.app.state.k8s_apps_v1 = client.AppsV1Api()
        except Exception:
            request.app.state.k8s_apps_v1 = None
            return None, 0
    try:
        d = apps_v1.read_namespaced_deployment(
            f"ap-app-{name}", request.app.state.settings.k8s_namespace)
        ready = d.status.ready_replicas or 0
        return ready > 0, ready
    except Exception:
        return None, 0


@router.get("/api/apps", response_model=list[S.AppView],
            dependencies=[Depends(require_role(*READ_ROLES))])
async def list_apps(request: Request):
    reg = request.app.state.app_registry
    reg.reload()
    async with request.app.state.session_factory() as session:
        collections = (await session.execute(
            select(AppCollection).order_by(AppCollection.name)
        )).scalars().all()
    out = []
    for collection in collections:
        info = reg.get(collection.source_app) if collection.source_app else None
        sp = info.spec if info else None
        ready, replicas = (_deployment_ready(request, info.name)
                           if info else (None, 0))
        out.append({
            "name": collection.name,
            "display_name": collection.display_name,
            "description": collection.description,
            "icon": collection.icon,
            "source_app": collection.source_app,
            "ui": sp.ui if sp else False,
            "api": sp.api if sp else False,
            "postgres": sp.needs.postgres if sp else False,
            "kafka_topics": sp.needs.kafka_topics if sp else [],
            "redis": sp.needs.redis if sp else False,
            "agent_key_role": sp.agent_key.role if sp and sp.agent_key else None,
            "error": info.error if info else None,
            "ready": ready,
            "ready_replicas": replicas,
        })
    # A newly synced or malformed service manifest still appears before the
    # next API restart/import. In particular, do not hide its validation error.
    known = {collection.name for collection in collections}
    for info in reg.list():
        if info.name in known:
            continue
        sp = info.spec
        ready, replicas = _deployment_ready(request, info.name)
        out.append({
            "name": info.name,
            "display_name": sp.display_name if sp else "",
            "description": sp.description if sp else "",
            "icon": sp.icon if sp else "",
            "source_app": info.name,
            "ui": sp.ui if sp else False,
            "api": sp.api if sp else False,
            "postgres": sp.needs.postgres if sp else False,
            "kafka_topics": sp.needs.kafka_topics if sp else [],
            "redis": sp.needs.redis if sp else False,
            "agent_key_role": sp.agent_key.role if sp and sp.agent_key else None,
            "error": info.error,
            "ready": ready,
            "ready_replicas": replicas,
        })
    out.sort(key=lambda app: app["name"])
    return out


class AppCollectionIn(BaseModel):
    name: str
    display_name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=4000)
    icon: str = Field(default="", max_length=32)


class AppCollectionPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=4000)
    icon: str | None = Field(default=None, max_length=32)


@router.post("/api/app-collections", status_code=201)
async def create_app_collection(request: Request, body: AppCollectionIn,
                                principal: str = Depends(require_admin)):
    if not APP_NAME.fullmatch(body.name):
        raise HTTPException(422, "app name must be lowercase letters, digits and hyphens")
    async with request.app.state.session_factory() as session:
        if await session.get(AppCollection, body.name):
            raise HTTPException(409, "app already exists")
        row = AppCollection(name=body.name, display_name=body.display_name,
                            description=body.description, icon=body.icon)
        session.add(row)
        await session.commit()
    return {"name": row.name}


@router.patch("/api/app-collections/{name}")
async def update_app_collection(request: Request, name: str, body: AppCollectionPatch,
                                principal: str = Depends(require_admin)):
    changes = body.model_dump(exclude_unset=True)
    if not changes or any(value is None for value in changes.values()):
        raise HTTPException(422, "provide non-null fields to update")
    async with request.app.state.session_factory() as session:
        row = await session.get(AppCollection, name)
        if row is None:
            raise HTTPException(404, "unknown app")
        for field, value in changes.items():
            setattr(row, field, value)
        row.updated_at = utcnow()
        await session.commit()
    return {"name": name}


def _path_ok(path: str) -> bool:
    return bool(path) and not (path.startswith("/") or "\\" in path
                               or ".." in path.split("/")
                               or any(ord(ch) < 0x20 for ch in path))


def _upstream_params(request: Request, params: str | None) -> dict[str, str]:
    """The app endpoint's query: every loose query param the caller sent
    (the broker's shape), plus the JSON `params` object if given (the shape an
    OpenAPI-derived client can express — a schema can name `params`, but not
    "any query key the app happens to accept")."""
    out = {k: v for k, v in request.query_params.items() if k != "params"}
    if params:
        try:
            extra = json.loads(params)
        except ValueError:
            raise HTTPException(400, "params must be a JSON object")
        if not isinstance(extra, dict) or not all(
                isinstance(v, (str, int, float, bool)) for v in extra.values()):
            raise HTTPException(400, "params must be a JSON object of scalar values")
        out.update({str(k): str(v) for k, v in extra.items()})
    return out


@router.get("/api/apps/{name}/query/{path:path}")
async def query_app(request: Request, name: str, path: str,
                    params: str | None = Query(
                        None, description="JSON object of query parameters for the "
                        "app endpoint, e.g. {\"topic\": \"security\", \"limit\": 20}"),
                    principal: str = Depends(require_role(*READ_ROLES))):
    """Read-only proxy into an app's API, for agents that (correctly) have no
    shell: the MCP broker exposes this as a tool, the caller's own token
    authenticates it, and the app receives the same trusted identity headers
    nginx would send. GETs only — mutations stay with the app's own flows."""
    # The path is caller-controlled: refuse anything that could step outside
    # /apps/<name>/api/ once normalized upstream (traversal, absolute paths,
    # backslashes, control chars).
    if not _path_ok(path):
        raise HTTPException(400, "invalid path")
    upstream_params = _upstream_params(request, params)
    reg = request.app.state.app_registry
    reg.reload()
    info = reg.get(name)
    if info is None or info.spec is None or not info.spec.api:
        raise HTTPException(404, "unknown app (or it serves no API)")
    upstream = getattr(request.app.state, "app_proxy_base", None) \
        or f"http://agent-platform-app-{name}:8000"
    role = "reader"
    agent = getattr(request.state, "api_key_agent", None)
    try:
        async with httpx.AsyncClient(base_url=upstream, timeout=20) as c:
            r = await c.get(f"/apps/{name}/api/{path}",
                            params=upstream_params,
                            headers={"X-AP-User": agent or principal, "X-AP-Role": role})
    except httpx.HTTPError as e:
        raise HTTPException(502, f"app `{name}` unreachable: {e}")
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


@router.get("/api/auth-check", include_in_schema=False)
async def auth_check(request: Request):
    """nginx auth_request backend for /apps/<name>/ routes: 204 + identity
    headers when the caller holds a valid session cookie or API key, 401
    otherwise. Apps receive X-AP-User / X-AP-Role and never see credentials."""
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    name, role = ident
    return Response(status_code=204,
                    headers={"X-AP-User": name, "X-AP-Role": role})
