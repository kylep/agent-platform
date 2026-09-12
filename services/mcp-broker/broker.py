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

`agents_edit` / `agents_grant` (docs/design/15) are core tools on both counts:
they forward the bearer like the rest, AND they check the declared grant like a
custom tool, because they are authorized by a grant rather than by a role. Their
logic lives in `agenttools.py`; only the MCP surface is here.
"""
import logging
import os
import re
from pathlib import Path

import agenttools
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


async def _request(method: str, path: str, params: dict | None = None,
                   json: dict | None = None) -> httpx.Response:
    # Forward the caller's identity — the broker never substitutes its own.
    headers = _caller_headers()
    clean = {k: v for k, v in (params or {}).items() if v is not None}
    async with httpx.AsyncClient(base_url=_API, timeout=20) as c:
        return await c.request(method, path, params=clean or None, json=json,
                               headers=headers)


async def _call(method: str, path: str, params: dict | None = None, json: dict | None = None) -> str:
    r = await _request(method, path, params, json)
    if r.status_code >= 400:
        # A model reads text, not status codes: an unprefixed `{"detail": ...}`
        # body looks exactly like a successful answer, and a tool that returns
        # the platform's refusal as if it were data is how an agent comes to
        # report work it never did. Every core tool goes through here, so this
        # is the one place that has to say so.
        return f"error: {r.status_code} {r.text}".rstrip()
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


# --- agent definitions (docs/design/15) --------------------------------------
# Core tools, because they forward the caller's BEARER: the platform API must
# see the agent itself, or `agent_versions.changed_by` would name a shared
# credential instead of the writer. The executor never gets a token, so these
# cannot be `tools/<name>/` customs — see agenttools.py's header.
#
# Unlike the other core tools these carry the custom-tool guard rail: the API
# is the real authorization (it re-derives the grant from the token on every
# write), but a run that was never granted them should be told so here, and the
# attempt should land in the audit trail rather than only in an API 403.

async def _guarded(tool: str, handler, args: dict) -> str:
    """whoami → declared-grant check → rate limit → run → audit, for a core
    tool that is grant-gated rather than role-gated."""
    t0 = _time.monotonic()
    ident = await _whoami()
    refused, decision = agenttools.guard(ident, tool)
    agent = (ident or {}).get("agent") or (ident or {}).get("principal") or ""
    run_id = (ident or {}).get("run_id") or ""
    initiated_by = (ident or {}).get("initiated_by") or ""
    if refused is not None:
        await _audit(agent, run_id, initiated_by, tool, args, decision, t0)
        return f"error: {refused}"
    if not _rate_ok(agent, tool):
        await _audit(agent, run_id, initiated_by, tool, args, "deny:rate-limit", t0)
        return "error: rate limit exceeded for this tool — slow down and retry shortly"
    # agenttools raises only ToolError, which it renders itself — but the API
    # pod restarting (httpx) or answering with something unparseable would
    # otherwise escape as a raw MCP exception, with no audit row for an attempt
    # that was made. Same shape as CustomTool.run's error handling.
    try:
        out = await handler(_request, args)
    except httpx.HTTPError as e:
        await _audit(agent, run_id, initiated_by, tool, args, "error:api-unreachable", t0)
        return f"error: the platform API is unreachable ({e}) — retry shortly"
    except Exception as e:
        log.exception("%s failed", tool)
        await _audit(agent, run_id, initiated_by, tool, args, "error:tool", t0)
        return f"error: {tool} failed unexpectedly: {type(e).__name__}: {e}"
    await _audit(agent, run_id, initiated_by, tool, args,
                 "error:tool" if out.startswith("error:") else "allow", t0,
                 result_bytes=len(out))
    return out


@mcp.tool
async def agents_edit(action: str, name: str | None = None,
                      definition: dict | None = None) -> str:
    """Read and write agent DEFINITIONS — what an agent IS.

    action='list' → every agent, one compact row each;
    action='get' (name) → one agent's full definition;
    action='create' (name, definition) → a new agent, live immediately;
    action='update' (name, definition) → change only the given fields (the
      rest of the definition is preserved for you);
    action='delete' (name) → remove it (its runs and change log survive).

    `definition` fields: prompt, description, model, entrypoints, enabled,
    concurrency, timeout_seconds, result_topic, transcript_retention_days.
    (`system` is admin-only and refused here — no tool can set it.)

    It can NEVER change what an agent may DO — tools, skills, secrets,
    can_invoke, role — that is the agents_grant tool, and attempting it here is
    refused. CARE: this permission is about the KIND of change, not the target,
    so you can rewrite the prompt (or add a cron) of an agent more privileged
    than you are. Every write is logged against your name."""
    return await _guarded("agents_edit", agenttools.agents_edit,
                          {"action": action, "name": name, "definition": definition})


@mcp.tool
async def agents_grant(action: str, name: str, field: str | None = None,
                       values: list[str] | None = None,
                       harness_tools: list[str] | None = None,
                       platform_tools: list[str] | None = None,
                       skills: list[str] | None = None,
                       secrets: list[str] | None = None,
                       can_invoke: bool | None = None) -> str:
    """Change what an agent may DO — GRANTS-EDITING, handle with care.

    action='get' (name) → that agent's current grants;
    action='set_grants' (name, + any of harness_tools/platform_tools/skills/
      secrets/can_invoke) → replace those lists wholesale; omitted ones are
      left exactly as they are;
    action='add_grant' (name, field, values) → add names to one list;
    action='remove_grant' (name, field, values) → take names off one list.
    `field` is harness_tools | platform_tools | skills | secrets.

    Use /api/help/tools names verbatim; a grant naming something the platform
    does not ship is refused at save time. It does not touch prompts or config
    (that is agents_edit, and the server refuses it from this grant). It does
    not EXPOSE `role`, which is a choice about this tool's surface rather than
    a boundary — the server counts `role` as a grant, so an agents_grant holder
    can still set it through the API directly. The `system` flag is a real
    boundary: admin-only, server-side. You can grant capabilities you do not
    hold yourself, including this tool — the change log is the control, so make
    the reason obvious."""
    return await _guarded("agents_grant", agenttools.agents_grant, {
        "action": action, "name": name, "field": field, "values": values,
        "harness_tools": harness_tools, "platform_tools": platform_tools,
        "skills": skills, "secrets": secrets, "can_invoke": can_invoke})


# --- relay (docs/design/19) --------------------------------------------------
# The messenger nearly every agent is born holding. A CORE tool because it has
# to post AS the caller: authorship is the forwarded bearer, never an argument,
# so a prompt-injected agent cannot speak as someone else. It is the one core
# tool that does not promote a run's token (agentspec.PLATFORM_MCP_RELAY_TOOLS)
# — it reaches /api/relay/* only, and membership bounds it from there.
RELAY_ACTIONS = ("post", "read", "channels", "dm", "react", "search")
# Every platform id is uuid4 hex — a channel's, a message's. For a channel it
# decides a reading: anything else the agent typed is a room NAME, because a
# name is what everyone says out loud ("#general") and what the room is called
# in every message the agent has read. For a message id there is no second
# reading, and the id goes into a URL path, so it is a gate.
_HEX_ID_RE = re.compile(r"[0-9a-f]{32}")


async def _relay_rooms() -> tuple[list, str | None]:
    """The rooms this agent can see, as (rooms, error)."""
    listing = await _call("GET", "/api/relay/channels")
    if listing.startswith("error:"):
        return [], listing
    try:
        rooms = _json.loads(listing)
    except ValueError:
        return [], f"error: could not list channels: {listing[:200]}"
    return rooms if isinstance(rooms, list) else [], None


async def _relay_by_name(channel: str) -> tuple[str, str | None]:
    """Resolve a room NAME to its id, as (id, error). An exact match wins
    outright; a case-insensitive one only when it is the single candidate,
    because guessing between two rooms is how a message meant for one lands in
    the other — and in Relay the wrong room is the wrong audience."""
    want = channel.lstrip("#")
    rooms, error = await _relay_rooms()
    if error:
        return "", error
    exact = [r for r in rooms if (r.get("name") or "") == want]
    if len(exact) == 1:
        return exact[0]["id"], None
    loose = [r for r in rooms if (r.get("name") or "").lower() == want.lower()]
    if len(loose) == 1:
        return loose[0]["id"], None
    if len(loose) > 1:
        names = ", ".join(sorted(r.get("name") or "" for r in loose))
        return "", (f"error: ambiguous channel name {channel} (matches {names}) "
                    f"— use the channel id")
    # Only rooms the agent is in come back, so this is "no such room FOR YOU" —
    # which is the answer that matters, and the one it can act on.
    return "", f"error: no channel named {channel}"


async def _relay_channel(channel: str | None) -> tuple[str, str | None]:
    """Resolve a room to its id, as (id, error). An id goes through untouched:
    the API decides membership, and looking it up here would only turn "you are
    not in that room" into a misleading "no such room"."""
    raw = (channel or "").strip()
    if not raw:
        return "", "error: this action needs a channel (a #name or a channel id)"
    if _HEX_ID_RE.fullmatch(raw):
        return raw, None
    return await _relay_by_name(raw)


def _clamp(value, high: int) -> int:
    """The API's own page bounds, applied here: a model that asks for 5000 gets
    the biggest page there is, rather than a 422 it has to interpret."""
    try:
        return max(1, min(int(value), high))
    except (TypeError, ValueError):
        return 30


async def _relay_post(channel_id: str, body: str, reply_to: str | None) -> str:
    return await _call("POST", f"/api/relay/channels/{channel_id}/messages",
                       json={"body": body, "reply_to": reply_to})


@mcp.tool
async def relay(action: str, channel: str | None = None, body: str | None = None,
                reply_to: str | None = None, limit: int = 30,
                before: str | None = None, to: str | None = None,
                message_id: str | None = None, emoji: str | None = None,
                q: str | None = None) -> str:
    """Relay chat — you are `agent:<you>`; authorship is your token, not text.
    Actions: post · read · channels · dm · react · search; `channel` is a
    `#name` or a channel id. `@name` in a body summons that agent: each costs
    one hop and the room pauses at the cap — you cannot address the whole room.
    Summoned? Your final answer is posted as your reply automatically, so post
    only for an extra message or another room; read the room first for context."""
    if action not in RELAY_ACTIONS:
        return "error: action must be one of " + "|".join(RELAY_ACTIONS)
    if action == "channels":
        return await _call("GET", "/api/relay/channels")
    if action == "react":
        if not message_id:
            return "error: action='react' requires message_id"
        if not emoji:
            return "error: action='react' requires emoji"
        if not _HEX_ID_RE.fullmatch(message_id.strip()):
            # The id is interpolated into a path and httpx resolves `..` before
            # the request leaves, so a "message id" that is really a traversal
            # would spend the caller's own bearer on an endpoint this tool does
            # not reach. Refused here, where the shape is known exactly.
            return "error: message_id must be a message id (32 hex characters)"
        return await _call("POST", f"/api/relay/messages/{message_id.strip()}/reactions",
                           json={"emoji": emoji})
    if action == "search":
        if not q:
            return "error: action='search' requires q, the text to look for"
        channel_id, error = await _relay_channel(channel) if channel else ("", None)
        if error:
            return error
        return await _call("GET", "/api/relay/search",
                           {"q": q, "channel": channel_id or None,
                            "limit": _clamp(limit, 100)})
    if action == "dm":
        if not to:
            return "error: action='dm' requires to, e.g. agent:news or user:admin"
        if not body:
            return "error: action='dm' requires body"
        # Get-or-create, then post: the room is plumbing, the message is the
        # act, so the message is what comes back.
        opened = await _call("POST", "/api/relay/dm", json={"with": to})
        if opened.startswith("error:"):
            return opened
        try:
            channel_id = _json.loads(opened)["id"]
        except (ValueError, TypeError, KeyError):
            return f"error: could not open that dm: {opened[:200]}"
        return await _relay_post(channel_id, body, None)
    if action == "post" and not body:
        return "error: action='post' requires body, the text to say"

    async def act(channel_id: str) -> str:
        if action == "read":
            return await _call("GET", f"/api/relay/channels/{channel_id}/messages",
                               {"limit": _clamp(limit, 200), "before": before})
        return await _relay_post(channel_id, body, reply_to)

    raw = (channel or "").strip()
    channel_id, error = await _relay_channel(raw)
    if error:
        return error
    out = await act(channel_id)
    if out.startswith("error: 404") and _HEX_ID_RE.fullmatch(raw):
        # It parsed as an id and there is no such room — but a channel NAME may
        # be 32 hex characters (it is a legal slug), so the name is the second
        # reading of what the agent typed, not a retry of the same one.
        named, name_error = await _relay_by_name(raw)
        if name_error is None:
            return await act(named)
    return out


# --- tickets (docs/design/20) ------------------------------------------------
# Relay's sibling: the board an agent's work is tracked on. A CORE tool for the
# same reason — the reporter, the actor and a comment's author are the forwarded
# bearer, never an argument — and it rides the same `relay` role, which reaches
# `/api/tickets/*` as the agent the token names.
TICKET_ACTIONS = ("create", "get", "list", "update", "move", "assign", "comment",
                  "search")
TICKET_STATES = ("open", "in_progress", "blocked", "review", "done", "cancelled")
TICKET_PRIORITIES = ("p0", "p1", "p2", "p3")
TICKET_CLOSED = ("done", "cancelled")
# `settings.tickets_thread_context_messages`, hard-coded: the broker holds no
# settings, and asking the API for its own configuration would be a round trip
# per read to learn a number that changes about never.
TICKET_THREAD_MESSAGES = 40
# The board's own page cap (`api/tickets.LIST_LIMIT`).
TICKET_LIST_LIMIT = 500
# `OPS-12` as a model types it. An id has no dash, so the two never collide.
_TICKET_KEY_RE = re.compile(r"[A-Za-z][A-Za-z0-9]{1,5}-\d+")
# The closed charset a ticket reference has to fit before it may become a URL
# path. Keys and ids are letters, digits and a dash and nothing else, so this
# is exact rather than a blocklist.
_TICKET_REF_RE = re.compile(r"[A-Za-z0-9-]{1,64}")
# What a model wraps an identifier in: a code span, or quotes. Stripped rather
# than refused, because a 404 for punctuation teaches an agent nothing about
# what it actually got wrong.
_WRAPPERS = "`'\"“”‘’ "


def _ticket_ref(key: str | None) -> tuple[str, str | None]:
    """A ticket as the agent named it — a key or an id — as (ref, error).

    Two jobs. It reads what the model meant: keys are upper-case by definition
    and `ops-12` in backticks is what a model writes mid-sentence. And it is the
    GATE on everything this tool interpolates into `/api/tickets/{...}`: httpx
    resolves `..` before a request leaves, so `X/../../whoami` would be a GET of
    another endpoint entirely, made with the caller's own bearer — the tool's
    confinement to `/api/tickets/*` is this fullmatch and nothing else."""
    raw = (key or "").strip(_WRAPPERS)
    ref = raw.upper() if _TICKET_KEY_RE.fullmatch(raw) else raw
    if ref and not _TICKET_REF_RE.fullmatch(ref):
        return "", ("error: invalid ticket key or id — a key looks like OPS-12, "
                    "an id is the ticket's own id")
    return ref, None


def _is_none(value: str | None) -> bool:
    """`none` as the model says it: the sentinel that clears a field, since a
    tool argument that was simply left out means "leave it alone"."""
    return (value or "").strip(_WRAPPERS).lower() == "none"


def _given(**fields) -> dict:
    """Only the fields the caller actually named. Two reasons, both the API's:
    a create schema forbids a null where it has a default, and a patch tells
    "clear it" from "leave it alone" by which keys arrive."""
    return {k: v for k, v in fields.items() if v is not None}


def _ticket_line(t: dict) -> str:
    return "  ".join([t.get("key") or "?", str(t.get("state") or ""),
                      str(t.get("priority") or ""),
                      t.get("assignee") or "unassigned",
                      (t.get("title") or "").strip()])


def _ticket_lines(out: str, *, drop_closed: bool, limit: int) -> str:
    """A board as lines rather than as rows of JSON: a page of `TicketView`s
    carries faces and six timestamps the agent has no use for, and one line per
    ticket is what it can actually read through.

    The two notes are about the seam between the API's page and this filter.
    The API takes ONE state, so "neither done nor cancelled" is filtered here,
    AFTER the cap — which means a project whose recently-closed tickets fill the
    page would otherwise answer "no tickets" while open work sits behind them.
    Silence there is the dangerous answer, so both a full page and a page that
    the filter emptied say so, and say which argument fixes it."""
    try:
        rows = _json.loads(out)
    except ValueError:
        return out          # an `error:` string, or something we cannot render
    if not isinstance(rows, list):
        return out
    page = len(rows)
    if drop_closed:
        rows = [t for t in rows if t.get("state") not in TICKET_CLOSED]
    if not rows:
        if not page:
            return "no tickets"
        return (f"only closed tickets in the first {page} — pass state=done or "
                f"state=cancelled to see them, or name a channel to narrow it")
    lines = [_ticket_line(t) for t in rows]
    if page >= limit:
        lines.append("more tickets than shown — pass state=open (or in_progress/"
                     "blocked/review) to see one state fully")
    return "\n".join(lines)


async def _ticket_detail(key: str) -> str:
    """One ticket with everything around it in a single answer: the row, its
    history, the discussion and the runs it produced. The thread is Relay's to
    serve — the API deliberately hands back a `root_message_id` instead of
    duplicating the messages — so reading a ticket is two calls here rather than
    two tool calls the agent has to know to make."""
    out = await _call("GET", f"/api/tickets/{key}")
    try:
        detail = _json.loads(out)
    except ValueError:
        return out
    root = detail.get("root_message_id")
    channel_id = (detail.get("ticket") or {}).get("channel_id")
    thread: object = []
    if root and channel_id:
        raw = await _call("GET", f"/api/relay/channels/{channel_id}/messages",
                          {"thread": root, "limit": TICKET_THREAD_MESSAGES})
        try:
            thread = _json.loads(raw)
        except ValueError:
            # The ticket is the answer and the thread is context: a room that
            # refuses is reported in place rather than losing the whole read.
            thread = raw
    return _json.dumps({"ticket": detail.get("ticket"),
                        "events": detail.get("events") or [], "thread": thread,
                        "runs": detail.get("runs") or [],
                        "thinking": detail.get("thinking")})


@mcp.tool
async def tickets(action: str, key: str | None = None, channel: str | None = None,
                  title: str | None = None, body: str | None = None,
                  state: str | None = None, priority: str | None = None,
                  assignee: str | None = None, labels: list[str] | None = None,
                  parent: str | None = None, due: str | None = None,
                  to: str | None = None, reason: str | None = None,
                  notify: bool | None = None, q: str | None = None,
                  limit: int = 50) -> str:
    """Tickets — the board your work is tracked on; you act as yourself, from
    your token. Actions: create · get · list · update · move · assign · comment
    · search. `key` is `OPS-12` or a ticket id, `channel` a `#name` or an id.
    Move a ticket when you START it (in_progress) and when you FINISH
    (review/done); if you cannot do it, say why with `comment` and move it to
    `blocked` with a reason. Never close a ticket whose work you did not do.
    `assign` is a hand-off that WAKES the assignee (a mention, one hop) — to ask
    a question, `comment` instead; `to='none'` unassigns.
    `list` is yours and unfinished by default: pass `state` to see closed
    tickets, `assignee='any'` for everyone's."""
    if action not in TICKET_ACTIONS:
        return "error: action must be one of " + "|".join(TICKET_ACTIONS)
    # Both answered here rather than read back off a 400: the vocabulary is the
    # board's and does not change, and a model that guessed `wip` needs the list
    # of real states, not the API's opinion of its request.
    if state and state not in TICKET_STATES:
        return "error: state must be one of " + "|".join(TICKET_STATES)
    if priority and priority not in TICKET_PRIORITIES:
        return "error: priority must be one of " + "|".join(TICKET_PRIORITIES)
    ref, error = _ticket_ref(key)
    if error:
        return error
    parent_ref, error = _ticket_ref(None if _is_none(parent) else parent)
    if error:
        return error
    if action in ("get", "update", "move", "assign", "comment") and not ref:
        return (f"error: action='{action}' requires key, the ticket's key "
                f"(e.g. OPS-12) or its id")

    if action == "create":
        if not title:
            return "error: action='create' requires title, one line saying what the work is"
        channel_id, error = await _relay_channel(channel)
        if error:
            return error
        return await _call("POST", "/api/tickets", json={
            "channel": channel_id, "title": title,
            **_given(body=body, assignee=assignee, priority=priority, labels=labels,
                     parent=parent_ref or None,
                     due_at=None if _is_none(due) else due, notify=notify)})
    if action == "get":
        return await _ticket_detail(ref)
    if action in ("list", "search"):
        if action == "search" and not q:
            return "error: action='search' requires q, the text to look for"
        channel_id, error = await _relay_channel(channel) if channel else ("", None)
        if error:
            return error
        page = _clamp(limit, TICKET_LIST_LIMIT)
        if action == "search":
            return _ticket_lines(await _call("GET", "/api/tickets", {
                "q": q, "channel": channel_id or None, "limit": page}),
                drop_closed=False, limit=page)
        # An agent asks for its own queue far more often than for the board, so
        # that is what a bare `list` answers; naming an assignee (`any` for
        # everybody) is how it asks the wider question.
        mine = assignee is None
        out = await _call("GET", "/api/tickets", {
            "channel": channel_id or None, "state": state or None,
            "assignee": None if mine or assignee == "any" else assignee,
            "mine": "true" if mine else None, "limit": page})
        # Closed rows are dropped by `_ticket_lines` rather than asked for: the
        # API filters by ONE state, and "neither done nor cancelled" is two.
        return _ticket_lines(out, drop_closed=not state, limit=page)
    if action == "update":
        fields = _given(title=title, body=body, priority=priority, labels=labels,
                        parent=parent_ref or None,
                        due_at=None if _is_none(due) else due, reason=reason)
        # `none` clears: `_given` drops what the caller did not name, so the
        # only way to send an explicit null is to put it back afterwards.
        for name, sentinel in (("parent", parent), ("due_at", due)):
            if _is_none(sentinel):
                fields[name] = None
        if not set(fields) - {"reason"}:
            return ("error: action='update' needs a field to change: title, body, "
                    "priority, labels, parent or due (`none` clears parent or due)")
        return await _call("PATCH", f"/api/tickets/{ref}", json=fields)
    if action == "move":
        if not state:
            return "error: action='move' requires state: " + "|".join(TICKET_STATES)
        if state == "blocked" and not reason:
            return ("error: moving to blocked needs a reason — say what is "
                    "blocking it, so somebody can unblock it")
        return await _call("POST", f"/api/tickets/{ref}/move",
                           json={"state": state, **_given(reason=reason)})
    if action == "assign":
        if not to:
            return ("error: action='assign' requires to, a participant like "
                    "agent:news or user:kyle (or 'none' to unassign)")
        return await _call("POST", f"/api/tickets/{ref}/assign",
                           json={"to": None if _is_none(to) else to,
                                 **_given(reason=reason, notify=notify)})
    if not body:
        return "error: action='comment' requires body, the text to say in the thread"
    return await _call("POST", f"/api/tickets/{ref}/comments", json={"body": body})


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
    # design/13 B: with SPIRE mTLS on, bind localhost — the ghostunnel server
    # sidecar (8443) is the only way in, and it requires a client SVID.
    mcp.run(transport="http", host=os.environ.get("AP_BIND_HOST", "0.0.0.0"),
            port=8000, path="/mcp")
