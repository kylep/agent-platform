"""Platform MCP broker — a first-class HTTP service that exposes the agent-
platform API as MCP tools over streamable-HTTP.

It holds NO credentials of its own: every core tool forwards the caller's own
`Authorization` header to the platform API, so a run's per-run token keeps its
exact scope (a confused-deputy is impossible). Token-bearing agents connect to
this service via `claude --mcp-config` instead of shelling out with curl, so
they need no `Bash` — closing the read-the-mounted-secret path.

Custom tools (docs/design/12) are loaded dynamically from the synced checkout
(`tools/*/tool.yaml`) and forwarded to the tool-executor. For those the caller
token is NEVER forwarded outward; instead the broker resolves it via
`/api/whoami` and enforces that the calling agent's definition declares the
tool, then sends only the verified identity to the executor.
"""
import logging
import os
from pathlib import Path

import httpx
import yaml
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.tools import Tool
from fastmcp.tools.tool import ToolResult

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-broker")

_API = os.environ.get("AP_API_URL", "http://agent-platform-api:8000").rstrip("/")
_EXECUTOR = os.environ.get("AP_EXECUTOR_URL", "http://agent-platform-tool-executor:8000").rstrip("/")
_TOOLS_ROOT = Path(os.environ.get("AP_TOOLS_ROOT", "/agents/tools"))
_REFRESH_SECONDS = int(os.environ.get("AP_TOOLS_REFRESH_SECONDS", "60"))
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


# --- custom tools (docs/design/12) -------------------------------------------

async def _whoami(auth: str) -> dict | None:
    """Resolve the caller's token to a verified identity (or None)."""
    if not auth:
        return None
    async with httpx.AsyncClient(base_url=_API, timeout=10) as c:
        r = await c.get("/api/whoami", headers={"Authorization": auth})
    return r.json() if r.status_code == 200 else None


class CustomTool(Tool):
    """An MCP tool whose schema comes from tool.yaml and whose execution is a
    verified forward to the tool-executor. The caller's token stays between
    broker and platform API — the executor gets identity, never credentials."""

    async def run(self, arguments: dict) -> ToolResult:
        auth = get_http_request().headers.get("authorization", "")
        ident = await _whoami(auth)
        if ident is None:
            return ToolResult(content="error: unauthenticated (no valid platform token)")
        declared = ident.get("tools")
        if declared is not None and f"mcp__platform__{self.name}" not in declared:
            return ToolResult(content=f"error: your agent does not declare the {self.name} tool")
        caller = {"agent": ident.get("agent") or ident.get("principal") or "",
                  "run_id": ident.get("run_id") or ""}
        try:
            async with httpx.AsyncClient(base_url=_EXECUTOR, timeout=150) as c:
                r = await c.post("/run", json={"tool": self.name, "args": arguments,
                                               "caller": caller})
        except httpx.HTTPError as e:
            return ToolResult(content=f"error: tool-executor unreachable ({e})")
        if r.status_code != 200:
            return ToolResult(content=f"error: tool-executor returned {r.status_code}: {r.text[:500]}")
        body = r.json()
        if not body.get("ok"):
            return ToolResult(content=f"error: {body.get('error', 'unknown tool failure')}")
        return ToolResult(content=body.get("output", ""))


def _scan_custom_tools() -> dict[str, dict]:
    """tool name → manifest for every valid tool dir (invalid ones are the
    registry/UI's problem to surface; the broker just skips them)."""
    found: dict[str, dict] = {}
    if not _TOOLS_ROOT.is_dir():
        return found
    for d in sorted(_TOOLS_ROOT.iterdir()):
        yml = d / "tool.yaml"
        if not yml.is_file() or not (d / "run.py").is_file():
            continue
        try:
            m = yaml.safe_load(yml.read_text()) or {}
        except yaml.YAMLError:
            continue
        name = m.get("name", d.name)
        if name != d.name or not m.get("description"):
            continue
        found[name] = m
    return found


_registered: dict[str, str] = {}  # name → description (change detection)


def refresh_custom_tools() -> None:
    """Sync FastMCP's tool set with the checkout. Core tools (registered via
    decorators above) are never touched — only names discovered by the scan."""
    current = _scan_custom_tools()
    for name in list(_registered):
        if name not in current:
            mcp.local_provider.remove_tool(name)
            del _registered[name]
            log.info("custom tool removed: %s", name)
    for name, m in current.items():
        desc = m["description"]
        if _registered.get(name) == desc:
            continue
        if name in _registered:
            mcp.local_provider.remove_tool(name)
        mcp.add_tool(CustomTool(
            name=name, description=desc,
            parameters=m.get("params") or {"type": "object", "properties": {}}))
        _registered[name] = desc
        log.info("custom tool registered: %s", name)


def _refresh_forever():
    # A plain daemon thread: mcp.run() owns the event loop, and registry
    # add/remove is dict-level work that doesn't need to sit on it.
    import time
    while True:
        time.sleep(_REFRESH_SECONDS)
        try:
            refresh_custom_tools()
        except Exception:
            log.exception("custom tool refresh failed")


if __name__ == "__main__":
    refresh_custom_tools()
    import threading
    threading.Thread(target=_refresh_forever, daemon=True).start()
    mcp.run(transport="http", host="0.0.0.0", port=8000, path="/mcp")
