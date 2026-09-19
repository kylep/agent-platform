"""External MCP facade — the platform's own REST API as MCP tools (design/17).

An MCP server GENERATED from the platform's OpenAPI document, so it cannot
drift from the API: the same spec that generates `sdk/` generates this tool
surface. External clients (Claude Code on a laptop) reach it at `pai:8090/mcp`
through the ap-web nginx proxy.

The surface is CURATED into three tiers (curation 2026-08-24; see
.superpowers/sdd/2026-08-24-facade-curation/scope.md):

- **KEEP** — the day-to-day management surface (observe/operate runs,
  conversations, jobs, schedules, agents, pending changes, memory, metrics,
  health, reports, registries, apps, help, the relay rooms — read a channel,
  post in it, react, DM, search, presence/stats — the ticket board: file,
  read, edit, move, assign, comment, stats — and the wiki: read, search, write,
  append, history, restore, promote, wanted — the usage snapshot and its
  gate — and the artifacts: list, read, save, edit, delete, generate, the
  model registry and the stats). Always tools. 95 of them.
- **GATE** — authorized-but-sharp: the credential/secret plane, admin audit
  reads, destructive/bulk ops, the relay channel lifecycle (creating, renaming
  and archiving rooms), and archiving a wiki page. Offered ONLY when
  `AP_MCP_ADMIN_TOOLS` is truthy (`admin_tools_enabled()`). 27 of them. The
  role ladder authorizes every call regardless — the flag controls the MENU,
  not the kitchen.
- **EXCLUDE** — UI form-feeders, reviewer digests the client can compute,
  git-edit conveniences redundant with having the repo, and system-agent
  endpoints. Never tools. 19 of them, plus the 18 session/internal/streaming/
  byte-serving operations below — 159 graded operations in all.

It is deliberately NOT the mcp-broker. The broker authenticates in-cluster run
identities and scopes tools to an agent's grants (design/13, design/15); this
service authenticates nothing at all. Like the broker it holds NO credential:
the caller's `Authorization: Bearer ap_…` header is forwarded verbatim on the
upstream request for that one call, so the platform's role ladder is the whole
authorization story and attribution (`agent_versions.changed_by`, the audit
trail) still names the caller. A request without a bearer is still forwarded —
it simply collects the API's own 401.

The spec is fetched from ap-api at startup, never baked into the image, so a
redeployed API refreshes the tool surface on this service's next restart (see
docs/deployment.md).
"""
import logging
import os
import time

import httpx
from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_request
from fastmcp.server.providers.openapi import MCPType, RouteMap
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.responses import Response

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("mcp-facade")

_API = os.environ.get("AP_API_URL", "http://agent-platform-api:8000").rstrip("/")
_UPSTREAM_TIMEOUT = float(os.environ.get("AP_UPSTREAM_TIMEOUT", "60"))
_SPEC_RETRY_SECONDS = float(os.environ.get("AP_SPEC_RETRY_SECONDS", "5"))
_ALLOWED_HOSTS = [h for h in os.environ.get(
    "AP_ALLOWED_HOSTS", "agent-platform-mcp-facade").split(",") if h.strip()]

# Route rules are (methods, pattern) pairs: `methods` is a tuple of HTTP verbs
# or "*" (all), and `pattern` is an anchored regex matched with re.search
# against the OpenAPI path — so `/api/setup-state` survives `/api/setup` being
# excluded, and `{param}` braces are escaped. Every pattern is pinned by a test
# against the real spec (a stale one fails). Method-scoping lets e.g. DELETE
# `/api/agents/{name}` gate while GET/PUT on the same path stay KEEP.

# Never tools (design/17): session auth (browser/cookie flows), run-scoped
# internals (session-token-only, useful only to the owning runner), and webhook
# ingress (for external services holding a webhook secret, design/16).
EXCLUDED_PATHS = (
    ("*", r"^/api/login$"),
    ("*", r"^/api/logout$"),
    ("*", r"^/api/setup$"),
    ("*", r"^/api/runs/\{run_id\}/session$"),
    ("*", r"^/api/runs/\{run_id\}/agentdef$"),
    ("*", r"^/api/webhooks/\{path\}$"),
    # Relay's event stream (design/19): a `text/event-stream` that by design
    # never ends. As a tool it would be a call that never returns, which is the
    # one shape MCP has no way to represent.
    ("*", r"^/api/relay/channels/\{channel_id\}/events$"),
    # The ticket board's live feed (design/20) is the same never-ending shape.
    ("*", r"^/api/tickets/events$"),
    # And the wiki's (design/21).
    ("*", r"^/api/wiki/events$"),
    # And the usage sidebar's (design/22).
    ("*", r"^/api/quota/events$"),
    # And the Studio's (design/23).
    ("*", r"^/api/artifacts/events$"),
    # And the Workbench's (design/24), plus its two run-scoped routes: a dev
    # run's own session token is the only caller they answer, and an MCP
    # client holds no run.
    ("*", r"^/api/workbench/events$"),
    ("*", r"^/api/runs/\{run_id\}/workbench$"),
    ("*", r"^/api/runs/\{run_id\}/publish$"),
    # An artifact's two byte routes (design/23): a PNG has no place in an MCP
    # text result, and a client that wants the bytes has both URLs from the
    # metadata the artifact tools do return. Agents see a picture through the
    # broker's `artifacts` tool, which attaches it as an image block.
    ("*", r"^/api/artifacts/\{artifact_id\}/content$"),
    ("*", r"^/api/artifacts/\{artifact_id\}/thumb$"),
    # The internal plane (design/22's `POST /api/internal/quota`): these routes
    # authenticate on a shared secret this service does not hold and must never
    # forward, and their callers are infrastructure, not MCP clients. A prefix
    # rather than one path, so a second internal endpoint is excluded the day
    # it is written rather than the day someone notices.
    ("*", r"^/api/internal/"),
)

# Curated out (curation 2026-08-24): UI plumbing, reviewer digests the client
# can compute itself, git-edit conveniences redundant with the repo, and
# system-agent endpoints. Never tools regardless of the admin flag.
CURATED_OUT = (
    (("GET",),   r"^/api/setup-state$"),
    (("GET",),   r"^/api/connectors$"),
    (("PATCH",), r"^/api/conversations/\{conversation_id\}$"),   # rename_conversation
    (("GET",),   r"^/api/tags$"),
    (("POST",),  r"^/api/runs/\{run_id\}/annotate$"),
    (("PATCH",), r"^/api/memories/\{memory_id\}$"),              # edit_memory
    (("GET",),   r"^/api/metrics/durations$"),
    (("GET",),   r"^/api/maintenance/retention$"),
    (("GET",),   r"^/api/report-types$"),
    (("POST",),  r"^/api/reports$"),                             # save_report (GET list survives)
    (("POST",),  r"^/api/report-kit/chart$"),
    (("GET",),   r"^/api/pull-requests/\{number\}/impact$"),
    (("GET",),   r"^/api/pull-requests/\{number\}/summary$"),
    (("POST",),  r"^/api/tools/\{name\}/quick-edit$"),
    (("POST",),  r"^/api/tools/new$"),
    (("POST",),  r"^/api/skills/\{name\}/quick-edit$"),
    (("POST",),  r"^/api/skills/new$"),
    # Relay's cross-channel binding list (design/19): it exists for the
    # connectors, which read it over HTTP with their own token on a timer. As a
    # tool it would answer a question no MCP client asks.
    (("GET",),   r"^/api/relay/bindings$"),
    # The deliberate usage probe (design/22). Agents reach it through the
    # `quota` tool, which is where the per-agent metering lives; offering the
    # raw route as well would be a second, unmetered way to spend the probe.
    # `GET /api/quota` stays a tool — reading the snapshot costs nothing.
    (("POST",),  r"^/api/quota/refresh$"),
)

# Sharp/admin tools: OFFERED only when AP_MCP_ADMIN_TOOLS is truthy. The role
# ladder still authorizes every call either way — this controls the menu, not
# the kitchen. The `^/api/secrets` and `^/api/audit/` prefixes have no `$` on
# purpose: each covers its whole domain and nothing else starts with it (the
# stale-pattern test keeps that honest).
GATED_ADMIN = (
    (("POST",),   r"^/api/change-password$"),
    ("*",         r"^/api/api-keys$"),                            # list + mint
    (("DELETE",), r"^/api/api-keys/\{key_id\}$"),                 # revoke
    ("*",         r"^/api/secrets"),   # list/put/verify/declare/declaration/quick-edit
    ("*",         r"^/api/agents/\{name\}/webhooks/\{path\}/secret$"),
    (("GET",),    r"^/api/integrations$"),
    ("*",         r"^/api/audit/"),                               # secret-access + tools
    (("DELETE",), r"^/api/agents/\{name\}$"),                     # delete_agent (GET/PUT survive)
    (("POST",),   r"^/api/agents/\{name\}/rollback/\{version\}$"),
    (("POST",),   r"^/api/agents/import$"),
    (("POST",),   r"^/api/maintenance/prune-transcripts$"),
    (("POST",),   r"^/api/dlq/\{run_id\}/discard$"),
    (("DELETE",), r"^/api/reports/\{report_id\}$"),
    # Relay's channel lifecycle (design/19): the rooms of the place are named
    # and retired by the operator, not by whoever holds a key — reading and
    # posting in them stays KEEP.
    (("POST",),   r"^/api/relay/channels$"),                     # create_relay_channel
    (("PATCH", "DELETE"), r"^/api/relay/channels/\{channel_id\}$"),  # rename/archive (GET survives)
    # Binding a room to Discord decides who can read it, which is the same
    # class of decision as naming or retiring one (the GET stays KEEP: "is this
    # room bridged?" is worth answering).
    (("POST",),   r"^/api/relay/channels/\{channel_id\}/bindings$"),
    (("DELETE",), r"^/api/relay/channels/\{channel_id\}/bindings/\{binding_id\}$"),
    # Archiving a wiki page (design/21) takes it out of search, the links and
    # the prompt block — the one wiki operation that removes rather than adds a
    # version. Restoring one stays KEEP so a mistake is reversible without the
    # flag; every other write is an ordinary edit the history records.
    (("DELETE",), r"^/api/wiki/pages/\{slug\}$"),
)

# operationId -> MCP tool name. Keys are route function names (api/app.py sets
# generate_unique_id_function=lambda route: route.name). Only the genuinely
# ambiguous get renamed; entries for gated tools apply when the flag exposes
# them. Every key is pinned to a real operationId by a test.
MCP_NAMES = {
    "overview":      "metrics_overview",           # GET /api/metrics/overview
    "by_model":      "metrics_by_model",           # GET /api/metrics/models
    "per_agent":     "metrics_by_agent",           # GET /api/metrics/agents
    "metrics_tools": "metrics_by_tool",            # GET /api/metrics/tools
    "notify":        "notify_channel",             # POST /api/notify — Discord channel post
    "post_message":  "post_conversation_message",  # POST /api/conversations/{id}/messages
    "set_enabled":   "set_schedule_enabled",       # POST /api/schedules/{agent}/{action}
    "secret_access": "audit_secret_access",        # GET /api/audit/secret-access (gated)
    "integrations":  "list_integrations",          # GET /api/integrations (gated)
}

_TRUTHY = ("1", "true", "yes", "on")


def admin_tools_enabled() -> bool:
    """Whether the sharp/admin tier is OFFERED (default off — a fresh facade
    serves the 95-tool KEEP surface). Offering-only: the API's role ladder
    authorizes every call regardless of this flag."""
    return os.environ.get("AP_MCP_ADMIN_TOOLS", "").strip().lower() in _TRUTHY


def route_maps(admin_tools: bool) -> list[RouteMap]:
    """The EXCLUDE rules for one build: always the design-17 exclusions and the
    curated-out set; the gated-admin set too unless the flag is on."""
    rules = EXCLUDED_PATHS + CURATED_OUT + (() if admin_tools else GATED_ADMIN)
    return [RouteMap(methods=(m if m == "*" else list(m)),
                     pattern=p, mcp_type=MCPType.EXCLUDE)
            for m, p in rules]


def current_request():
    """The HTTP request being served, or None outside a request (a seam: the
    tests drive `caller_auth_headers` without a live ASGI context)."""
    try:
        return get_http_request()
    except Exception:
        return None


def caller_auth_headers(request) -> dict:
    """The caller's Authorization, forwarded verbatim — and nothing invented
    when there is none, so an anonymous caller gets the API's own 401 rather
    than this service's authority."""
    if request is None:
        return {}
    value = request.headers.get("authorization")
    return {"Authorization": value} if value else {}


# The ONLY caller header that reaches the platform API. fastmcp copies the
# incoming MCP request's headers onto the upstream request minus a denylist of
# its own — a denylist that strips `authorization` (hence this hook) but NOT
# `cookie`, and the API's authenticate() tries the session cookie BEFORE the
# bearer. A reader-scoped key plus a stray browser `ap_session` cookie would
# therefore have been admin on every tool. So this is an ALLOWLIST: the
# bearer is forwarded, everything else the caller sent is deleted, and only the
# transport headers httpx/the body need survive.
FORWARDED_HEADERS = frozenset({"authorization"})
TRANSPORT_HEADERS = frozenset({
    "host", "accept", "accept-encoding", "connection", "user-agent",
    "content-type", "content-length", "mcp-protocol-version",
})


async def forward_caller_auth(request: httpx.Request) -> None:
    """httpx request hook: strip the request down to transport headers, then
    stamp THIS call with THIS caller's bearer. The hook runs after fastmcp has
    copied the caller's headers on, so the deletions stick. The client is
    shared across callers, so the bearer is set per request and removed when
    the current caller has none — never carried over."""
    for name in list(request.headers.keys()):
        if name.lower() not in TRANSPORT_HEADERS | FORWARDED_HEADERS:
            del request.headers[name]
    headers = caller_auth_headers(current_request())
    if "Authorization" in headers:
        request.headers["Authorization"] = headers["Authorization"]
    else:
        request.headers.pop("Authorization", None)


def make_client(base_url: str = _API) -> httpx.AsyncClient:
    """The upstream client. It carries no credential of its own; every request
    is stamped by the hook with the caller's."""
    return httpx.AsyncClient(base_url=base_url, timeout=_UPSTREAM_TIMEOUT,
                             event_hooks={"request": [forward_caller_auth]})


def fetch_spec(base_url: str = _API, sleep=time.sleep) -> dict:
    """The API's OpenAPI document, retried until the API answers — this pod
    routinely starts before ap-api is ready, and a facade with no spec has no
    reason to serve."""
    while True:
        try:
            r = httpx.get(f"{base_url}/openapi.json", timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            log.warning("openapi fetch from %s failed (%s); retrying in %ss",
                        base_url, e, _SPEC_RETRY_SECONDS)
            sleep(_SPEC_RETRY_SECONDS)


def build(spec: dict, client: httpx.AsyncClient | None = None,
          admin_tools: bool | None = None) -> FastMCP:
    """The MCP server for one spec: the KEEP surface, plus the GATE tier when
    `admin_tools` (None ⇒ read `AP_MCP_ADMIN_TOOLS`), minus the curated/design-17
    exclusions. Ambiguous names are clarified via MCP_NAMES. (Tools only — no
    resources/resource-templates: one flat surface is what Claude Code's tool
    search reads best.)"""
    if admin_tools is None:
        admin_tools = admin_tools_enabled()
    return FastMCP.from_openapi(spec, client=client or make_client(),
                                name="agent-platform",
                                route_maps=route_maps(admin_tools),
                                mcp_names=MCP_NAMES)


class RequireAuthorization:
    """Refuse every request that arrives without an `Authorization` header —
    `initialize` and `tools/list` included.

    It does NOT validate the header (only the platform API can, and it does, on
    every actual call); it just refuses to talk to a caller who is obviously
    not carrying a key. Without this the full tool schema — every endpoint,
    parameter and description the platform has — is readable by anyone on the
    LAN who can open a socket. A real MCP client always sends its configured
    header on every request, so the cost is nothing.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and not Headers(scope=scope).get("authorization"):
            # Detail-free: an unauthenticated caller learns only that a key is
            # required, never what lives here.
            await Response(status_code=401,
                           headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
            return
        await self.app(scope, receive, send)


MIDDLEWARE = [Middleware(RequireAuthorization)]


if __name__ == "__main__":
    mcp = build(fetch_spec())
    mcp.run(transport="http", host=os.environ.get("AP_BIND_HOST", "0.0.0.0"),
            port=8000, path="/mcp", middleware=MIDDLEWARE,
            # DNS-rebinding protection, off by default in fastmcp. nginx does
            # not rewrite Host, so what arrives is the upstream Service name
            # (`agent-platform-mcp-facade:8000`; the port is normalized away).
            # fastmcp always allows localhost/127.0.0.1 on top of this, which
            # is what an in-pod probe or a local run uses. A browser-based MCP
            # client is not a supported caller: with allowed_hosts explicit,
            # any cross-origin `Origin` is refused.
            host_origin_protection=True, allowed_hosts=_ALLOWED_HOSTS)
