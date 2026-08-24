import asyncio
import re
import urllib.error
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.gitedit import _github_app_token
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


# --- impact digest -----------------------------------------------------------
# Deterministic review aid: classify each changed file into its building block
# and pull out config-meaningful diff lines (triggers, secrets, strictness…).

_BLOCK_AREAS = {"agent.md": "definition", "manifest.yaml": "manifest",
                "entrypoints.yaml": "entrypoints", "SKILL.md": "SKILL.md",
                "secret.yaml": "declaration", "report.yaml": "declaration"}
# yaml keys whose add/remove a reviewer should notice at a glance
_NOTABLE = re.compile(
    r"^(cron|webhooks|kafka|secrets|skills|schedule|model|role|required|state|"
    r"severity|verify|system|can_invoke|memory|concurrency|timeout_seconds|"
    r"url|path|name|generator|cadence|retention_days)\s*:|^- ")


def classify_change_path(path: str) -> tuple[str | None, str]:
    """(building-block label, area) for a changed path; block None = platform
    code outside the blocks (worth a loud warning in review)."""
    m = re.match(r"(agents|skills|secrets|reports)/([^/]+)/(.+)$", path)
    if not m:
        return None, path
    kind = {"agents": "agent", "skills": "skill", "secrets": "secret",
            "reports": "report"}[m.group(1)]
    fname = m.group(3)
    area = _BLOCK_AREAS.get(fname, fname)
    if kind == "secret" and fname.startswith("verify_"):
        area = "verify script"
    return f"{kind}: {m.group(2)}", area


def _notable_lines(filename: str, patch: str) -> list[str]:
    """+/- lines whose yaml key matters (config files only — prose diffs in an
    agent.md are for the diff view, not the digest)."""
    if not (filename.endswith((".yaml", ".yml")) or filename.endswith("SKILL.md")):
        return []
    out = []
    for line in patch.splitlines():
        if line.startswith(("+++", "---")) or line[:1] not in "+-":
            continue
        if _NOTABLE.match(line[1:].strip()):
            out.append(line)
    return out[:10]


@router.get("/api/pull-requests/{number}/impact", response_model=S.ChangeImpact)
async def pull_request_impact(request: Request, number: int):
    """A deterministic reviewer digest: which building blocks the change
    touches, and the config-meaningful lines it adds/removes."""
    gh = await _client(request)
    files = await asyncio.to_thread(gh.pull_request_files, number)
    items, warnings = [], []
    for f in files:
        block, area = classify_change_path(f["filename"])
        if block is None:
            warnings.append(f"`{f['filename']}` is outside the building blocks "
                            "— this is platform code; review carefully.")
        if f["status"] == "removed":
            warnings.append(f"`{f['filename']}` is DELETED.")
        items.append({"file": f["filename"], "block": block, "area": area,
                      "status": f["status"], "additions": f["additions"],
                      "deletions": f["deletions"],
                      "notable": _notable_lines(f["filename"], f.get("patch") or "")})
    return {"items": items, "warnings": warnings}


# --- AI reviewer summary (automatic) -----------------------------------------
# The dispatcher's PrSummarizer loop summarizes every open coder/* PR and
# posts the result as a PR COMMENT (marker + head sha). This endpoint just
# reads that comment back for the UI.

@router.get("/api/pull-requests/{number}/summary", response_model=S.PrSummary)
async def pull_request_summary(request: Request, number: int):
    """The auto-generated reviewer summary for a change: `ready` with the text
    once the summarizer's comment lands, `pending` while the loop works (a
    fresh push resets to pending until the new head is summarized)."""
    from agentplatform.prsummarizer import parse_marker
    gh = await _client(request)
    pr = await asyncio.to_thread(gh.pull_request, number)
    sha = pr.get("head", {}).get("sha", "")
    comments = await asyncio.to_thread(gh.list_issue_comments, number)
    for c in reversed(comments):
        body = c.get("body", "")
        if parse_marker(body) == sha:
            text = body.split("-->", 1)[1].replace("**AI reviewer summary**", "", 1).strip()
            return {"state": "ready", "summary": text, "sha": sha}
    return {"state": "pending", "summary": None, "sha": sha}


async def _cleanup_branch(gh: GitHubClient, number: int) -> None:
    """Best-effort head-branch deletion after a merge/close — no clutter. The
    per-block branch is recreated (force-pushed) on the next propose, so
    deleting it never loses anything."""
    try:
        pr = await asyncio.to_thread(gh.pull_request, number)
        branch = pr.get("head", {}).get("ref", "")
        if branch.startswith(CODER_BRANCH_PREFIX):
            await asyncio.to_thread(gh.delete_branch, branch)
    except Exception:
        pass  # cleanup must never fail the accept/discard itself


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
    await _cleanup_branch(gh, number)
    return {"merged": bool(r.get("merged")), "sha": r.get("sha")}


@router.post("/api/pull-requests/{number}/close")
async def close_pull_request(request: Request, number: int):
    gh = await _client(request)
    try:
        r = await asyncio.to_thread(gh.close_pull_request, number)
    except urllib.error.HTTPError as e:
        raise HTTPException(e.code, e.read().decode()[:300])
    await _cleanup_branch(gh, number)
    return r
