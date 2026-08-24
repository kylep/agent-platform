"""Proposing a change to the repo from the API (the platform-code self-edit
flow, docs/design/02 + docs/building-blocks/changes.md).

CAPABILITY is code: skills, custom tools and secret declarations live in git
and are changed the way code is changed — a deterministic branch, a pull
request, review. This module is the shared plumbing those endpoints use: pick
the git credential (a GitHub App installation token first, else a PAT), clone,
write the edit set, and hand it to `EditService` to commit or open the PR.

It used to live in `api/agents.py`, because agent DEFINITIONS were files too.
They are rows now (docs/design/15) and edit directly, so the helpers moved here
— to a module named for what they actually do — and their remaining callers
(`api/skills.py`, `api/secrets.py`, `api/tools.py`, `api/pulls.py`) import them
from one obvious place.
"""
import asyncio
import tempfile
from pathlib import Path

from fastapi import HTTPException, Request

from agentplatform.github import GitHubClient
from agentplatform.githubapp import GitHubApp
from agentplatform.gitservice import EditService, GitWriter


def _push_url(url: str, token: str | None) -> str:
    """Set the `x-access-token` username on an https GitHub URL (no secret in
    the URL — the token is supplied to git via GIT_ASKPASS). Other URLs (e.g. a
    local bare repo in tests) pass through unchanged."""
    if token and url.startswith("https://github.com/"):
        return url.replace("https://", "https://x-access-token@", 1)
    return url


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


def _build_writer(settings, token_creds: dict | None):
    """Build the GitWriter (+ optional PR client) from an https token. Returns
    (writer, pr_client) or None if no usable credential is configured."""
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
    """Write an edit set into a fresh clone and open it as a pending change.
    Picks the git credential the same way for every structured edit: a GitHub
    App token first, else a PAT."""
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
            built = _build_writer(settings, await st.secret_store.get("github-token"))
            if built is None:
                raise HTTPException(409, "no git credential configured "
                                         "(github-app or github-token)")
            writer, pr_client = built
        svc = EditService(writer, pr_client=pr_client)
        return svc.apply(tmpp / "ws", files, message=message, branch=branch,
                         pr_title=pr_title, pr_body=pr_body)
