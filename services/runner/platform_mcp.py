"""Platform MCP server — brokers the agent-platform HTTP API as MCP tools so a
system agent can read/annotate runs, read health metrics, use its memory, and
post notifications WITHOUT a shell.

Launched over stdio by the runner (`claude --mcp-config`) only for agents that
already get an `AP_API_TOKEN` (system / memory / can_invoke). The agent gets
these `mcp__platform__*` tools instead of `Bash`, so it can't read the pod's
mounted secrets or run arbitrary commands. Every call is scoped by the token.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request

from mcp.server.fastmcp import FastMCP

_API = os.environ.get("AP_API_URL", "").rstrip("/")
_TOKEN = os.environ.get("AP_API_TOKEN", "")
mcp = FastMCP("platform")


def _call(method: str, path: str, params: dict | None = None, body: dict | None = None) -> str:
    url = _API + path
    if params:
        q = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        if q:
            url += "?" + q
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Authorization": f"Bearer {_TOKEN}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, method=method, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode() or "ok"
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode(errors='replace')}"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


# --- runs (run-summarizer) ---------------------------------------------------
@mcp.tool()
def list_runs(needs_summary: bool = False, limit: int = 10) -> str:
    """Recent runs as JSON. needs_summary=true returns only runs lacking a summary."""
    return _call("GET", "/api/runs", {"needs_summary": str(needs_summary).lower(), "limit": limit})


@mcp.tool()
def get_run(run_id: str) -> str:
    """Full detail of one run (agent, trigger, state, prompt, …) as JSON."""
    return _call("GET", f"/api/runs/{run_id}")


@mcp.tool()
def list_tags() -> str:
    """Existing run tags as a JSON array (reuse these when annotating)."""
    return _call("GET", "/api/tags")


@mcp.tool()
def annotate_run(run_id: str, summary: str, tags: list[str] | None = None) -> str:
    """Set a run's one-line summary and tags."""
    return _call("POST", f"/api/runs/{run_id}/annotate", body={"summary": summary, "tags": tags or []})


# --- health + memory + notify (health-monitor) -------------------------------
@mcp.tool()
def metrics_overview() -> str:
    """Platform run metrics overview (JSON)."""
    return _call("GET", "/api/metrics/overview")


@mcp.tool()
def metrics_agents() -> str:
    """Per-agent metrics incl. failure_streak (JSON)."""
    return _call("GET", "/api/metrics/agents")


@mcp.tool()
def kafka_health() -> str:
    """Kafka health incl. reachability, lag, backlog (JSON)."""
    return _call("GET", "/api/health/kafka")


@mcp.tool()
def recall_memory(q: str = "") -> str:
    """Search your own agent memory (JSON). Empty q lists all."""
    return _call("GET", "/api/memories", {"q": q or None})


@mcp.tool()
def remember(key: str, content: str, tags: list[str] | None = None) -> str:
    """Save/overwrite a memory in your namespace (reusing a key overwrites it)."""
    return _call("POST", "/api/memories", body={"key": key, "content": content, "tags": tags or []})


@mcp.tool()
def post_message(channel: str, text: str) -> str:
    """Post a message to a Discord channel by name (via the platform connector)."""
    return _call("POST", "/api/notify", body={"channel": channel, "text": text})


if __name__ == "__main__":
    mcp.run()
