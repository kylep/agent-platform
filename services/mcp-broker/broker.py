"""Platform MCP broker — a first-class HTTP service that exposes the agent-
platform API as MCP tools over streamable-HTTP.

It holds NO credentials of its own: every tool forwards the caller's own
`Authorization` header to the platform API, so a run's per-run token keeps its
exact scope (a confused-deputy is impossible). Token-bearing agents connect to
this service via `claude --mcp-config` instead of shelling out with curl, so
they need no `Bash` — closing the read-the-mounted-secret path.
"""
import os

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request

_API = os.environ.get("AP_API_URL", "http://agent-platform-api:8000").rstrip("/")
mcp = FastMCP("platform")


async def _call(method: str, path: str, params: dict | None = None, json: dict | None = None) -> str:
    # Forward the caller's bearer token — the broker never substitutes its own.
    auth = get_http_request().headers.get("authorization", "")
    headers = {"Authorization": auth} if auth else {}
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    async with httpx.AsyncClient(base_url=_API, timeout=20) as c:
        r = await c.request(method, path, params=clean or None, json=json, headers=headers)
        return r.text or "ok"


# --- runs (run-summarizer) ---------------------------------------------------
# Consolidation convention (design/12): 1-2 tools per domain with an action/
# scope discriminator, split read/write where grants should differ.
@mcp.tool
async def runs_read(action: str = "list", run_id: str | None = None,
                    needs_summary: bool = False, limit: int = 10) -> str:
    """Read run history (JSON). action='list' → recent runs (needs_summary=true
    filters to runs still lacking a summary; limit caps the page);
    action='get' → one run's full detail (requires run_id);
    action='tags' → the run tags that already exist (reuse instead of inventing)."""
    if action == "get":
        if not run_id:
            return "error: action='get' requires run_id"
        return await _call("GET", f"/api/runs/{run_id}")
    if action == "tags":
        return await _call("GET", "/api/tags")
    if action == "list":
        return await _call("GET", "/api/runs",
                           {"needs_summary": str(needs_summary).lower(), "limit": limit})
    return "error: action must be one of list|get|tags"


@mcp.tool
async def runs_write(run_id: str, summary: str, tags: list[str] | None = None) -> str:
    """Annotate a run: set its one-line summary and tags (the only run mutation)."""
    return await _call("POST", f"/api/runs/{run_id}/annotate", json={"summary": summary, "tags": tags or []})


# --- health/metrics (health-monitor) -----------------------------------------
@mcp.tool
async def metrics(scope: str = "overview") -> str:
    """Platform health metrics (JSON). scope='overview' → run volumes, success
    rate, token spend; scope='agents' → per-agent metrics incl. failure_streak;
    scope='kafka' → event-bus health (reachability, consumer lag, DLQ backlog)."""
    if scope == "agents":
        return await _call("GET", "/api/metrics/agents")
    if scope == "kafka":
        return await _call("GET", "/api/health/kafka")
    if scope == "overview":
        return await _call("GET", "/api/metrics/overview")
    return "error: scope must be one of overview|agents|kafka"


@mcp.tool
async def read_memory(q: str = "") -> str:
    """Search your own agent memory (JSON). Empty q lists all."""
    return await _call("GET", "/api/memories", {"q": q or None})


@mcp.tool
async def save_memory(content: str, key: str | None = None, tags: list[str] | None = None) -> str:
    """Save a memory in your namespace. Give a key ONLY for state you overwrite
    in place (reusing a key replaces that memory); omit it for plain notes —
    don't invent meaningless keys."""
    return await _call("POST", "/api/memories", json={"key": key, "content": content, "tags": tags or []})


@mcp.tool
async def post_message(channel: str, text: str) -> str:
    """Post a message to a Discord channel by name (via the platform connector)."""
    return await _call("POST", "/api/notify", json={"channel": channel, "text": text})


# --- apps (news-librarian etc.) ----------------------------------------------
@mcp.tool
async def query_app(app: str, path: str, params: dict | None = None) -> str:
    """Call a read-only API endpoint of an installed platform app (GET only,
    through the platform's traversal-guarded proxy — mutations stay with the
    app's own flows). `app` is the app's name from /apps, `path` the endpoint
    within its API, `params` the query string. The app's companion skill
    documents its endpoints. e.g. query_app(app='news', path='items',
    params={'topic': 'ai-industry', 'day_from': '2026-08-01'})."""
    return await _call("GET", f"/api/apps/{app}/query/{path}", params or {})


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
