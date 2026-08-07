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


def _caller_headers() -> dict:
    """The caller's identity headers, forwarded verbatim: bearer (SA token or
    API key) plus the sender-constrained run JWT when present (design/13 C)."""
    req = get_http_request()
    headers = {}
    if req.headers.get("authorization"):
        headers["Authorization"] = req.headers["authorization"]
    if req.headers.get("x-ap-run-token"):
        headers["X-AP-Run-Token"] = req.headers["x-ap-run-token"]
    return headers


async def _call(method: str, path: str, params: dict | None = None, json: dict | None = None) -> str:
    # Forward the caller's identity — the broker never substitutes its own.
    headers = _caller_headers()
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
    scope='kafka' → event-bus health (reachability, consumer lag, DLQ backlog);
    scope='tools' → per-tool call/denial/error counts from the audit trail."""
    if scope == "agents":
        return await _call("GET", "/api/metrics/agents")
    if scope == "kafka":
        return await _call("GET", "/api/health/kafka")
    if scope == "tools":
        return await _call("GET", "/api/metrics/tools")
    if scope == "overview":
        return await _call("GET", "/api/metrics/overview")
    return "error: scope must be one of overview|agents|kafka|tools"


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


# --- audit + rate limits (docs/design/13 E) ----------------------------------
# The broker is the single chokepoint for custom-tool calls, so it carries the
# audit log (published to Kafka — the recorder side writes the table; this
# service stays credential-free) and per-identity rate limits.

import hashlib as _hashlib
import json as _json
import time as _time
import uuid as _uuid
from collections import defaultdict
from datetime import datetime, timezone

_KAFKA = os.environ.get("AP_KAFKA_BOOTSTRAP", "")
_TOPIC_AUDIT = "platform.tool.audit"
_audit_producer = None

# Token bucket per (agent, tool): burst 30, ~30 calls/minute refill.
_RATE_CAPACITY = 30.0
_RATE_REFILL_PER_S = 0.5
_buckets: dict[tuple[str, str], list[float]] = defaultdict(
    lambda: [_RATE_CAPACITY, _time.monotonic()])


def _rate_ok(agent: str, tool: str) -> bool:
    b = _buckets[(agent, tool)]
    now = _time.monotonic()
    b[0] = min(_RATE_CAPACITY, b[0] + (now - b[1]) * _RATE_REFILL_PER_S)
    b[1] = now
    if b[0] < 1.0:
        return False
    b[0] -= 1.0
    return True


def _args_digest(arguments: dict) -> str:
    # A digest, never the raw args — they may embed sensitive content.
    return _hashlib.sha256(
        _json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()


async def _audit(agent: str, run_id: str, initiated_by: str, tool: str,
                 arguments: dict, decision: str, t0: float, result_bytes: int = 0) -> None:
    """Fire-and-forget audit event; auditing must never break a tool call."""
    global _audit_producer
    if not _KAFKA:
        return
    try:
        if _audit_producer is None:
            from aiokafka import AIOKafkaProducer
            _audit_producer = AIOKafkaProducer(bootstrap_servers=_KAFKA)
            await _audit_producer.start()
        env = {"type": "tool.audit", "schema_version": 1, "id": _uuid.uuid4().hex,
               "ts": datetime.now(timezone.utc).isoformat(), "key": agent,
               "source": "mcp-broker",
               "data": {"agent": agent, "run_id": run_id or None,
                        "initiated_by": initiated_by or None, "tool": tool,
                        "args_digest": _args_digest(arguments), "decision": decision,
                        "latency_ms": int((_time.monotonic() - t0) * 1000),
                        "result_bytes": result_bytes}}
        await _audit_producer.send_and_wait(
            _TOPIC_AUDIT, _json.dumps(env).encode(), key=agent.encode() or b"unknown")
    except Exception:
        log.exception("tool audit publish failed (call unaffected)")


# --- custom tools (docs/design/12) -------------------------------------------

async def _whoami() -> dict | None:
    """Resolve the caller's identity headers to a verified identity (or None)."""
    headers = _caller_headers()
    if not headers.get("Authorization"):
        return None
    async with httpx.AsyncClient(base_url=_API, timeout=10) as c:
        r = await c.get("/api/whoami", headers=headers)
    return r.json() if r.status_code == 200 else None


class CustomTool(Tool):
    """An MCP tool whose schema comes from tool.yaml and whose execution is a
    verified forward to the tool-executor. The caller's token stays between
    broker and platform API — the executor gets identity, never credentials."""

    async def run(self, arguments: dict) -> ToolResult:
        t0 = _time.monotonic()
        ident = await _whoami()
        if ident is None:
            await _audit("", "", "", self.name, arguments, "deny:unauthenticated", t0)
            return ToolResult(content="error: unauthenticated (no valid platform token)")
        agent = ident.get("agent") or ident.get("principal") or ""
        run_id = ident.get("run_id") or ""
        initiated_by = ident.get("initiated_by") or ""
        declared = ident.get("tools")
        if declared is not None and f"mcp__platform__{self.name}" not in declared:
            await _audit(agent, run_id, initiated_by, self.name, arguments, "deny:undeclared", t0)
            return ToolResult(content=f"error: your agent does not declare the {self.name} tool")
        if not _rate_ok(agent, self.name):
            await _audit(agent, run_id, initiated_by, self.name, arguments, "deny:rate-limit", t0)
            return ToolResult(content="error: rate limit exceeded for this tool — slow down and retry shortly")
        caller = {"agent": agent, "run_id": run_id}
        try:
            async with httpx.AsyncClient(base_url=_EXECUTOR, timeout=150) as c:
                r = await c.post("/run", json={"tool": self.name, "args": arguments,
                                               "caller": caller})
        except httpx.HTTPError as e:
            await _audit(agent, run_id, initiated_by, self.name, arguments, "error:executor-unreachable", t0)
            return ToolResult(content=f"error: tool-executor unreachable ({e})")
        if r.status_code != 200:
            await _audit(agent, run_id, initiated_by, self.name, arguments, f"error:http-{r.status_code}", t0)
            return ToolResult(content=f"error: tool-executor returned {r.status_code}: {r.text[:500]}")
        body = r.json()
        if not body.get("ok"):
            await _audit(agent, run_id, initiated_by, self.name, arguments, "error:tool", t0)
            return ToolResult(content=f"error: {body.get('error', 'unknown tool failure')}")
        output = body.get("output", "")
        await _audit(agent, run_id, initiated_by, self.name, arguments, "allow", t0,
                     result_bytes=len(output))
        return ToolResult(content=output)


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
