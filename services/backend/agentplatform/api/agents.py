import tempfile
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agentplatform.api.auth import require_admin
from agentplatform.github import GitHubClient
from agentplatform.gitservice import EditService, GitWriter

router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/api/agents")
async def list_agents(request: Request):
    request.app.state.agent_store.reload()
    return [{"name": a.name, "description": a.manifest.description if a.manifest else "",
             "quarantined": a.error is not None, "error": a.error}
            for a in request.app.state.agent_store.list()]

@router.get("/api/agents/{name}")
async def get_agent(request: Request, name: str):
    a = request.app.state.agent_store.get(name)
    if a is None:
        raise HTTPException(404)
    return a.model_dump()


class QuickEditIn(BaseModel):
    field: str          # "prompt" (agent.md body); more fields land with M3
    value: str


def _authed_remote(url: str, token: str | None) -> str:
    """Inject a token into an https GitHub URL for push auth; pass other URLs
    (e.g. a local bare repo in tests) through unchanged."""
    if token and url.startswith("https://github.com/"):
        return url.replace("https://", f"https://x-access-token:{token}@", 1)
    return url


@router.post("/api/agents/{name}/quick-edit")
async def quick_edit(request: Request, name: str, body: QuickEditIn,
                     principal: str = Depends(require_admin)):
    """Deterministic edit that skips the agent: writes the change into a fresh
    clone and lets the tiered git path commit it (tier 1) or open a PR."""
    st = request.app.state
    settings = st.settings
    if not settings.git_remote_url:
        raise HTTPException(409, "self-edit is not configured (git_remote_url unset)")
    if st.agent_store.get(name) is None:
        raise HTTPException(404, "unknown agent")
    if body.field != "prompt":
        raise HTTPException(422, "unsupported field")
    files = {f"agents/{name}/agent.md": body.value}

    token = None
    creds = await st.secret_store.get("github-token")
    if creds:
        token = creds.get("token")
    writer = GitWriter(_authed_remote(settings.git_remote_url, token),
                       default_branch=settings.default_branch)
    pr_client = (GitHubClient(token, settings.github_repo)
                 if token and settings.github_repo else None)
    svc = EditService(writer, pr_client=pr_client)
    with tempfile.TemporaryDirectory() as tmp:
        return svc.apply(Path(tmp) / "ws", files,
                         message=f"{principal}: quick-edit {name}/{body.field}",
                         branch=f"coder/{name}-{body.field}-{uuid.uuid4().hex[:8]}",
                         pr_title=f"Edit {name}: {body.field}")
