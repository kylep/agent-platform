import asyncio
import urllib.error

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.agents import _github_app_token
from agentplatform.api.auth import require_admin
from agentplatform.github import GitHubClient

# Platform-authored PRs live on coder/* branches.
CODER_BRANCH_PREFIX = "coder/"

router = APIRouter(dependencies=[Depends(require_admin)])


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


@router.get("/api/pull-requests")
async def list_pull_requests(request: Request):
    """Open pull requests the platform authored (coder/* branches) — the
    Pending Changes view."""
    gh = await _client(request)
    prs = await asyncio.to_thread(gh.list_pull_requests)
    return [_view(p) for p in prs if p["head"]["ref"].startswith(CODER_BRANCH_PREFIX)]


@router.post("/api/pull-requests/{number}/merge")
async def merge_pull_request(request: Request, number: int):
    gh = await _client(request)
    try:
        return await asyncio.to_thread(gh.merge_pull_request, number)
    except urllib.error.HTTPError as e:
        # e.g. 405 not mergeable / 409 conflict — surface GitHub's reason.
        raise HTTPException(e.code, e.read().decode()[:300])


@router.post("/api/pull-requests/{number}/close")
async def close_pull_request(request: Request, number: int):
    gh = await _client(request)
    try:
        return await asyncio.to_thread(gh.close_pull_request, number)
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode()[:300])
