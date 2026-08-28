"""The app registry surface (docs/design/11): what apps are declared
(apps/<name>/app.yaml in the synced checkout), what each needs, and whether
its Deployment is live. Apps serve their own UI/API behind /apps/<name>/ —
this router also provides the nginx auth_request endpoint that session-guards
those routes without the app ever seeing credentials, and a read-only proxy
so shell-less agents can query app APIs through the MCP broker."""
import json

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, authenticate, require_role

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
    out = []
    for info in reg.list():
        sp = info.spec
        ready, replicas = _deployment_ready(request, info.name)
        out.append({
            "name": info.name,
            "description": sp.description if sp else "",
            "icon": sp.icon if sp else "",
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
    return out


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
