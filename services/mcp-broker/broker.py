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
@mcp.tool
async def list_runs(needs_summary: bool = False, limit: int = 10) -> str:
    """Recent runs as JSON. needs_summary=true returns only runs lacking a summary."""
    return await _call("GET", "/api/runs", {"needs_summary": str(needs_summary).lower(), "limit": limit})


@mcp.tool
async def get_run(run_id: str) -> str:
    """Full detail of one run (agent, trigger, state, prompt, …) as JSON."""
    return await _call("GET", f"/api/runs/{run_id}")


@mcp.tool
async def list_tags() -> str:
    """Existing run tags as a JSON array (reuse these when annotating)."""
    return await _call("GET", "/api/tags")


@mcp.tool
async def annotate_run(run_id: str, summary: str, tags: list[str] | None = None) -> str:
    """Set a run's one-line summary and tags."""
    return await _call("POST", f"/api/runs/{run_id}/annotate", json={"summary": summary, "tags": tags or []})


# --- health + memory + notify (health-monitor) -------------------------------
@mcp.tool
async def metrics_overview() -> str:
    """Platform run-metrics overview (JSON)."""
    return await _call("GET", "/api/metrics/overview")


@mcp.tool
async def metrics_agents() -> str:
    """Per-agent metrics incl. failure_streak (JSON)."""
    return await _call("GET", "/api/metrics/agents")


@mcp.tool
async def kafka_health() -> str:
    """Kafka health incl. reachability, lag, backlog (JSON)."""
    return await _call("GET", "/api/health/kafka")


@mcp.tool
async def recall_memory(q: str = "") -> str:
    """Search your own agent memory (JSON). Empty q lists all."""
    return await _call("GET", "/api/memories", {"q": q or None})


@mcp.tool
async def remember(content: str, key: str | None = None, tags: list[str] | None = None) -> str:
    """Save a memory in your namespace. Give a key ONLY for state you overwrite
    in place (reusing a key replaces that memory); omit it for plain notes —
    don't invent meaningless keys."""
    return await _call("POST", "/api/memories", json={"key": key, "content": content, "tags": tags or []})


@mcp.tool
async def post_message(channel: str, text: str) -> str:
    """Post a message to a Discord channel by name (via the platform connector)."""
    return await _call("POST", "/api/notify", json={"channel": channel, "text": text})


if __name__ == "__main__":
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
