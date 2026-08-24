"""External MCP facade — the platform's own REST API as MCP tools (design/17).

An MCP server GENERATED from the platform's OpenAPI document, so it cannot
drift from the API: the same spec that generates `sdk/` generates this tool
surface, and everything the API exposes is a tool unless it is on the exclusion
list below. External clients (Claude Code on a laptop) reach it at
`pai:8090/mcp` through the ap-web nginx proxy.

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

# Paths the facade refuses to turn into tools (design/17). Anchored regexes,
# matched with re.search against the OpenAPI path — `/api/setup-state` must
# survive `/api/setup` being excluded. Pinned by a test against the real spec.
EXCLUDED_PATHS = (
    # Session auth: browser/cookie flows, meaningless to a bearer-token client.
    r"^/api/login$",
    r"^/api/logout$",
    r"^/api/setup$",
    # Run-scoped internals: authenticated by a run's session token only, and
    # only useful to the runner that owns the run.
    r"^/api/runs/\{run_id\}/session$",
    r"^/api/runs/\{run_id\}/agentdef$",
    # Webhook ingress: that surface is for external services holding a webhook
    # secret (design/16), not for an API-key client.
    r"^/api/webhooks/\{path\}$",
)

ROUTE_MAPS = [RouteMap(pattern=p, mcp_type=MCPType.EXCLUDE) for p in EXCLUDED_PATHS]


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
# therefore have been admin on all 91 tools. So this is an ALLOWLIST: the
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


def build(spec: dict, client: httpx.AsyncClient | None = None) -> FastMCP:
    """The MCP server for one spec: every route a tool except the exclusions.
    (Tools only — no resources/resource-templates: one flat surface is what
    Claude Code's tool search reads best.)"""
    return FastMCP.from_openapi(spec, client=client or make_client(),
                                name="agent-platform", route_maps=ROUTE_MAPS)


class RequireAuthorization:
    """Refuse every request that arrives without an `Authorization` header —
    `initialize` and `tools/list` included.

    It does NOT validate the header (only the platform API can, and it does, on
    every actual call); it just refuses to talk to a caller who is obviously
    not carrying a key. Without this the full 91-tool schema — every endpoint,
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
