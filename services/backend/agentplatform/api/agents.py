import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agentplatform.api.auth import require_admin
from agentplatform.db import Run
from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.github import GitHubClient
from agentplatform.githubapp import GitHubApp
from agentplatform.gitservice import EditService, GitWriter

log = logging.getLogger("agents-api")

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


def _push_url(url: str, token: str | None) -> str:
    """Set the `x-access-token` username on an https GitHub URL (no secret in
    the URL — the token is supplied to git via GIT_ASKPASS). Other URLs (e.g. a
    local bare repo in tests) pass through unchanged."""
    if token and url.startswith("https://github.com/"):
        return url.replace("https://", "https://x-access-token@", 1)
    return url


def _ssh_remote(repo: str) -> str:
    return f"git@github.com:{repo}.git"


async def _github_app_token(request: Request) -> str | None:
    """Mint (cached) an installation token for the configured GitHub App, or
    None if no `github-app` secret is set. The GitHubApp instance is cached on
    app.state so its ~1h token is reused across requests."""
    st = request.app.state
    app = getattr(st, "github_app", None)
    if app is None:
        c = await st.secret_store.get("github-app")
        if not (c and c.get("app_id") and c.get("install_id") and c.get("private_key")):
            return None
        app = GitHubApp(c["app_id"], c["install_id"], c["private_key"])
        st.github_app = app
    return await asyncio.to_thread(app.installation_token)


def _writer_from_token(settings, token: str):
    """GitWriter + PR client for an https token (App installation or PAT)."""
    writer = GitWriter(_push_url(settings.git_remote_url, token), token=token,
                       default_branch=settings.default_branch)
    pr_client = GitHubClient(token, settings.github_repo) if settings.github_repo else None
    return writer, pr_client


def _build_writer(settings, tmp: Path, deploy: dict | None, token_creds: dict | None):
    """Pick the git credential — a repo-scoped deploy key (ssh, preferred) or an
    https token — and build the GitWriter (+ optional PR client). Returns
    (writer, pr_client) or None if no usable credential is configured."""
    if deploy and (deploy.get("key") or "").strip() and settings.github_repo:
        key = deploy["key"]
        keyfile = tmp / "deploy_key"
        keyfile.write_text(key if key.endswith("\n") else key + "\n")
        keyfile.chmod(0o600)
        writer = GitWriter(_ssh_remote(settings.github_repo), ssh_key_path=str(keyfile),
                           default_branch=settings.default_branch)
        return writer, None  # deploy keys can't use the REST PR API
    if token_creds and (token_creds.get("token") or "").strip() and settings.git_remote_url:
        token = token_creds["token"].strip()
        writer = GitWriter(_push_url(settings.git_remote_url, token), token=token,
                           default_branch=settings.default_branch)
        pr_client = GitHubClient(token, settings.github_repo) if settings.github_repo else None
        return writer, pr_client
    if settings.git_remote_url and not settings.git_remote_url.startswith("https://"):
        # A non-https remote (a local bare repo, e.g. in tests) needs no auth.
        return GitWriter(settings.git_remote_url, default_branch=settings.default_branch), None
    return None


@router.post("/api/agents/{name}/quick-edit")
async def quick_edit(request: Request, name: str, body: QuickEditIn,
                     principal: str = Depends(require_admin)):
    """Deterministic edit that skips the agent: writes the change into a fresh
    clone and lets the tiered git path commit it (tier 1) or open a PR."""
    st = request.app.state
    settings = st.settings
    if not (settings.git_remote_url or settings.github_repo):
        raise HTTPException(409, "self-edit is not configured")
    if st.agent_store.get(name) is None:
        raise HTTPException(404, "unknown agent")
    if body.field != "prompt":
        raise HTTPException(422, "unsupported field")
    files = {f"agents/{name}/agent.md": body.value}

    app_token = await _github_app_token(request)
    with tempfile.TemporaryDirectory() as tmp:
        tmpp = Path(tmp)
        if app_token:                       # preferred: push + PR via the App
            writer, pr_client = _writer_from_token(settings, app_token)
        else:
            built = _build_writer(settings, tmpp,
                                  await st.secret_store.get("github-deploy-key"),
                                  await st.secret_store.get("github-token"))
            if built is None:
                raise HTTPException(409, "no git credential configured "
                                         "(github-app, github-deploy-key, or github-token)")
            writer, pr_client = built
        svc = EditService(writer, pr_client=pr_client)
        return svc.apply(tmpp / "ws", files,
                         message=f"{principal}: quick-edit {name}/{body.field}",
                         branch=f"coder/agent-{name}",  # one open PR per agent
                         pr_title=f"Edit {name}: {body.field}")


class FreeformEditIn(BaseModel):
    instruction: str


@router.post("/api/agents/{name}/edit", status_code=202)
async def freeform_edit(request: Request, name: str, body: FreeformEditIn,
                        principal: str = Depends(require_admin)):
    """Dispatch platform-coder to edit agent `{name}` per a freeform
    instruction; its changes land as a pull request (see the run's transcript
    for the PR link). The run flows through the normal dispatcher/runner path."""
    st = request.app.state
    if st.agent_store.get(name) is None:
        raise HTTPException(404, "unknown agent")
    coder = st.agent_store.get("platform-coder")
    if coder is None or coder.error is not None:
        raise HTTPException(409, "platform-coder agent is unavailable")
    # Instruction first so the runner derives a clean PR title from line 1.
    prompt = (f"{body.instruction}\n\nContext: edit the agent `{name}` in this "
              f"repository; only modify files under `agents/{name}/`.")
    run = Run(agent="platform-coder", trigger="self-edit", requested_by=principal, prompt=prompt)
    async with st.session_factory() as s:
        s.add(run); await s.commit()
    try:
        await st.producer.publish(TOPIC_RUN_REQUESTS, run.id,
                                  {"type": "run", "run_id": run.id})
    except Exception:
        log.warning("publish failed for self-edit run %s; sweep will drain it", run.id)
    return {"id": run.id, "state": run.state, "target_agent": name}
