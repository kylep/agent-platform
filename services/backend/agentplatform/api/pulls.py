import asyncio
import urllib.error
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.agents import _github_app_token
from agentplatform.api.auth import require_admin
from agentplatform.github import GitHubClient

# Platform-authored PRs live on coder/* branches — one branch per building
# block: coder/agent-<name>, coder/skill-<name>, coder/secret-<name>.
CODER_BRANCH_PREFIX = "coder/"

from agentplatform.api import schemas as S
router = APIRouter(dependencies=[Depends(require_admin)])


def synced_head(checkout: Path) -> str | None:
    """The commit sha the synced git checkout is at, resolved by reading the
    .git files directly (no git binary in the backend image). This is what the
    cluster is actually running — the UI polls it after an accept to show
    Deploying… → Live."""
    git = checkout / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
    except OSError:
        return None
    if not head.startswith("ref: "):
        return head or None          # detached HEAD is already a sha
    ref = head[5:].strip()
    loose = git / ref
    if loose.is_file():
        return loose.read_text().strip() or None
    packed = git / "packed-refs"
    if packed.is_file():
        for line in packed.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and not line.startswith(("#", "^")) and parts[1] == ref:
                return parts[0]
    return None


@router.get("/api/sync-status", response_model=S.SyncStatus)
async def sync_status(request: Request):
    """Where the live checkout is. The agents/skills/secrets the platform runs
    come from this sha; after accepting a change, it becomes visible here
    within one agents-sync interval."""
    root = Path(request.app.state.settings.agents_root).parent
    return {"sha": synced_head(root)}


async def _client(request: Request) -> GitHubClient:
    token = await _github_app_token(request)
    repo = request.app.state.settings.github_repo
    if not token or not repo:
        raise HTTPException(409, "github app is not configured")
    return GitHubClient(token, repo)


def _view(pr: dict) -> dict:
    return {"number": pr["number"], "title": pr["title"], "url": pr["html_url"],
            "branch": pr["head"]["ref"], "author": pr["user"]["login"],
            "created_at": pr["created_at"]}


@router.get("/api/pull-requests", response_model=list[S.PullRequest])
async def list_pull_requests(request: Request):
    """Open pull requests the platform authored (coder/* branches) — the
    Pending Changes view."""
    gh = await _client(request)
    prs = await asyncio.to_thread(gh.list_pull_requests)
    return [_view(p) for p in prs if p["head"]["ref"].startswith(CODER_BRANCH_PREFIX)]


@router.get("/api/pull-requests/{number}/files", response_model=list[S.PullRequestFile])
async def pull_request_files(request: Request, number: int):
    """Changed files + unified diff for the Pending Changes detail view."""
    gh = await _client(request)
    files = await asyncio.to_thread(gh.pull_request_files, number)
    return [{"filename": f["filename"], "status": f["status"],
             "additions": f["additions"], "deletions": f["deletions"],
             "patch": f.get("patch", "")} for f in files]


@router.post("/api/pull-requests/{number}/merge", response_model=S.MergeResult)
async def merge_pull_request(request: Request, number: int):
    """Accept a change. Returns the merge commit sha so the UI can track it
    through /api/sync-status until the cluster is running it (Live)."""
    gh = await _client(request)
    try:
        r = await asyncio.to_thread(gh.merge_pull_request, number)
    except urllib.error.HTTPError as e:
        # e.g. 405 not mergeable / 409 conflict — surface GitHub's reason.
        raise HTTPException(e.code, e.read().decode()[:300])
    return {"merged": bool(r.get("merged")), "sha": r.get("sha")}


@router.post("/api/pull-requests/{number}/close")
async def close_pull_request(request: Request, number: int):
    gh = await _client(request)
    try:
        return await asyncio.to_thread(gh.close_pull_request, number)
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode()[:300])
