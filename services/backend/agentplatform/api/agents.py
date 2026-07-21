import asyncio
import logging
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agentplatform.agentspec import (AVAILABLE_TOOLS, mutate_agent_md,
                                     mutate_manifest_yaml, render_agent_md,
                                     render_manifest, validate_agent_name)
from agentplatform.api.auth import READ_ROLES, require_admin, require_role
from agentplatform.db import Run
from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.github import GitHubClient
from agentplatform.githubapp import GitHubApp
from agentplatform.gitservice import EditService, GitWriter

log = logging.getLogger("agents-api")

# Reads are open to any authenticated role (reader+); mutating routes below
# guard themselves with require_admin. This lets an operator/reader key list and
# inspect agents (SDK / platform skill) without granting edit rights.
router = APIRouter()


@router.get("/api/agents", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_agents(request: Request):
    request.app.state.agent_store.reload()
    return [{"name": a.name, "description": a.manifest.description if a.manifest else "",
             "quarantined": a.error is not None, "error": a.error,
             "system": bool(a.manifest and a.manifest.system),
             "schedule": a.manifest.schedule if a.manifest else ""}
            for a in request.app.state.agent_store.list()]

@router.get("/api/agents/{name}", dependencies=[Depends(require_role(*READ_ROLES))])
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


async def _apply_files(request: Request, files: dict[str, str | None], *,
                       message: str, branch: str, pr_title: str,
                       pr_body: str = "") -> dict:
    """Write an edit set into a fresh clone and let the tiered git path commit
    it (tier 1) or open a PR (tier 2). Picks the git credential the same way for
    every structured edit: a GitHub App token first, else a deploy key / PAT."""
    st = request.app.state
    settings = st.settings
    if not (settings.git_remote_url or settings.github_repo):
        raise HTTPException(409, "self-edit is not configured")
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
        return svc.apply(tmpp / "ws", files, message=message, branch=branch,
                         pr_title=pr_title, pr_body=pr_body)


@router.post("/api/agents/{name}/quick-edit")
async def quick_edit(request: Request, name: str, body: QuickEditIn,
                     principal: str = Depends(require_admin)):
    """Deterministic edit that skips the agent: writes the change into a fresh
    clone and lets the tiered git path commit it (tier 1) or open a PR."""
    if request.app.state.agent_store.get(name) is None:
        raise HTTPException(404, "unknown agent")
    if body.field != "prompt":
        raise HTTPException(422, "unsupported field")
    return await _apply_files(
        request, {f"agents/{name}/agent.md": body.value},
        message=f"{principal}: quick-edit {name}/{body.field}",
        branch=f"coder/agent-{name}", pr_title=f"Edit {name}: {body.field}")


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
                                  {"type": "run", "run_id": run.id}, type="run.request")
    except Exception:
        log.warning("publish failed for self-edit run %s; sweep will drain it", run.id)
    return {"id": run.id, "state": run.state, "target_agent": name}


# --- structured create / config edit (New-Agent wizard + checkbox editor) ----

@router.get("/api/agent-tools", dependencies=[Depends(require_role(*READ_ROLES))])
async def agent_tools():
    """The canonical tool list the UI renders as checkboxes (one source of truth
    with the validation below)."""
    return {"tools": AVAILABLE_TOOLS}


def _known_skill_names(request: Request) -> set[str]:
    request.app.state.skill_store.reload()
    return {s.name for s in request.app.state.skill_store.list()}


def _validate_selection(request: Request, skills: list[str], tools: list[str]) -> None:
    """Reject unknown skills or tools before writing anything (422)."""
    known = _known_skill_names(request)
    bad_skills = [s for s in skills if s not in known]
    if bad_skills:
        raise HTTPException(422, f"unknown skill(s): {', '.join(bad_skills)}")
    bad_tools = [t for t in tools if t not in AVAILABLE_TOOLS]
    if bad_tools:
        raise HTTPException(422, f"unknown tool(s): {', '.join(bad_tools)}")


class CreateAgentIn(BaseModel):
    name: str
    description: str = ""
    role: str = "operator"
    model: str = ""
    concurrency: int = 1
    timeout_seconds: int = 1800
    skills: list[str] = []
    tools: list[str] = AVAILABLE_TOOLS  # default: unrestricted (all tools)
    prompt: str = ""


@router.post("/api/agents", status_code=201)
async def create_agent(request: Request, body: CreateAgentIn,
                       principal: str = Depends(require_admin)):
    """Create a new agent from the wizard: render its manifest.yaml + agent.md
    and open a pull request (new files are always Tier 2 / review-gated)."""
    st = request.app.state
    try:
        name = validate_agent_name(body.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    st.agent_store.reload()
    if st.agent_store.get(name) is not None:
        raise HTTPException(409, "an agent with that name already exists")
    _validate_selection(request, body.skills, body.tools)

    manifest = render_manifest({
        "description": body.description,
        "role": body.role if body.role != "operator" else None,
        "model": body.model,
        "concurrency": body.concurrency if body.concurrency != 1 else None,
        "timeout_seconds": body.timeout_seconds if body.timeout_seconds != 1800 else None,
        "skills": body.skills,
    })
    agent_md = render_agent_md(name, body.description, body.tools,
                               body.prompt or f"You are {name}.")
    files = {f"agents/{name}/manifest.yaml": manifest,
             f"agents/{name}/agent.md": agent_md}
    return await _apply_files(
        request, files, message=f"{principal}: create agent {name}",
        branch=f"coder/agent-{name}", pr_title=f"Create agent {name}",
        pr_body=f"New agent `{name}` created via the New-Agent wizard.")


class ConfigEditIn(BaseModel):
    skills: list[str] | None = None
    tools: list[str] | None = None
    description: str | None = None


@router.patch("/api/agents/{name}/config")
async def edit_agent_config(request: Request, name: str, body: ConfigEditIn,
                            principal: str = Depends(require_admin)):
    """Apply a structured config edit (skills / tools / description) to an
    existing agent and open a PR. A no-op edit returns tier 0."""
    st = request.app.state
    st.agent_store.reload()
    info = st.agent_store.get(name)
    if info is None:
        raise HTTPException(404, "unknown agent")
    _validate_selection(request, body.skills or [], body.tools or [])

    root = Path(st.agent_store.root) / name
    files: dict[str, str | None] = {}
    if body.skills is not None or body.description is not None:
        manifest_text = (root / "manifest.yaml").read_text()
        files[f"agents/{name}/manifest.yaml"] = mutate_manifest_yaml(
            manifest_text, skills=body.skills, description=body.description)
    if body.tools is not None or body.description is not None:
        files[f"agents/{name}/agent.md"] = mutate_agent_md(
            info.agent_md, tools=body.tools, description=body.description)
    if not files:
        raise HTTPException(422, "nothing to change")
    return await _apply_files(
        request, files, message=f"{principal}: edit {name} config",
        branch=f"coder/agent-{name}", pr_title=f"Edit {name}: skills & tools",
        pr_body=f"Config edit for `{name}` via the agent editor.")
