"""The app registry surface (docs/design/11): what apps are declared
(apps/<name>/app.yaml in the synced checkout), what each needs, and whether
its Deployment is live. Apps serve their own UI/API behind /apps/<name>/ —
this router also provides the nginx auth_request endpoint that session-guards
those routes without the app ever seeing credentials."""
from fastapi import APIRouter, Depends, HTTPException, Request, Response

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
