"""The facade's three contracts (docs/design/17), against the REAL spec.

The tool surface is generated, so the only things worth testing are the
decisions around it: WHAT is excluded, WHAT reaches the API (the caller's
bearer, and nothing else they sent), and WHO gets through the front door. The
first is pinned against `create_app(...).openapi()` — the same document
`sdk/regenerate.py` generates the SDK from — so an API change that renames or
adds an endpoint the facade must not expose fails here instead of quietly
appearing as a tool.

Run in CI (the `mcp-facade` job) with the backend importable:
    pip install -e services/backend -r services/mcp-facade/requirements.txt
    cd services/mcp-facade && python -m pytest -q
"""
import asyncio
import contextvars
import re

import httpx
import pytest

import facade

METHODS = ("get", "post", "put", "patch", "delete")


@pytest.fixture(scope="module")
def spec():
    from agentplatform.api.app import create_app
    from agentplatform.config import Settings
    from agentplatform.events import FakeProducer
    return create_app(Settings(), None, FakeProducer()).openapi()


def build_tools(spec, admin_tools):
    """The generated tool surface for one admin-flag value: the list of tools.
    Every test passes admin_tools explicitly so ambient AP_MCP_ADMIN_TOOLS
    never leaks in."""
    mcp = facade.build(spec, client=httpx.AsyncClient(base_url="http://itest"),
                       admin_tools=admin_tools)
    return asyncio.run(mcp.list_tools())


@pytest.fixture(scope="module")
def tools(spec):
    """The DEFAULT (admin-off) tool surface — the 54-tool KEEP set."""
    return build_tools(spec, admin_tools=False)


@pytest.fixture(scope="module")
def admin_tools(spec):
    """The admin-on surface — KEEP + GATE (75 tools)."""
    return build_tools(spec, admin_tools=True)


ALL_RULES = facade.EXCLUDED_PATHS + facade.CURATED_OUT + facade.GATED_ADMIN


def operations(spec):
    return [(m.upper(), p) for p, ops in spec["paths"].items()
            for m in ops if m in METHODS]


def matches(rules, method, path):
    """Does any (methods, pattern) rule match this (method, path)?"""
    return any((verbs == "*" or method in verbs) and re.search(pattern, path)
               for verbs, pattern in rules)


def test_every_exclusion_still_matches_a_real_path(spec):
    """A stale rule is worse than none: it silently stops protecting anything
    when a path is renamed. Holds for ALL THREE rule tuples."""
    paths = list(spec["paths"])
    for _verbs, pattern in ALL_RULES:
        assert [p for p in paths if re.search(pattern, p)], \
            f"rule {pattern} matches no path in the API — stale?"


def test_excluded_paths_produce_no_tools(spec, tools):
    """The design's list, verbatim: session auth, run-scoped internals, and
    webhook ingress are not reachable from an API key."""
    exposed = {t._route.path for t in tools}
    for path in ("/api/login", "/api/logout", "/api/setup",
                 "/api/runs/{run_id}/session", "/api/runs/{run_id}/agentdef",
                 "/api/webhooks/{path}"):
        assert path in spec["paths"], f"{path} vanished from the API"
        assert path not in exposed


def test_setup_state_is_not_caught_by_the_setup_exclusion():
    """The rules are anchored regexes: the `^/api/setup$` design-17 exclusion
    must NOT over-match `/api/setup-state` (which has its own curated-out rule
    instead — the anchoring is what keeps the two decisions independent)."""
    assert not matches(facade.EXCLUDED_PATHS, "GET", "/api/setup-state")
    assert matches(facade.EXCLUDED_PATHS, "POST", "/api/setup")


def test_everything_else_is_a_tool(spec, tools):
    """The default surface, by construction: exactly the operations that are
    not design-17-excluded, not curated out, and not gated. Pinned at 54."""
    hidden = {(m, p) for m, p in operations(spec) if matches(ALL_RULES, m, p)}
    expected = set(operations(spec)) - hidden
    assert {(t._route.method, t._route.path) for t in tools} == expected
    assert len(tools) == len(expected) == 54, \
        sorted({(t._route.method, t._route.path) for t in tools})


def test_admin_flag_restores_gated(spec, admin_tools):
    """With AP_MCP_ADMIN_TOOLS on, the gated set returns (75 total) but the
    design-17 exclusions and CURATED_OUT never come back."""
    still_hidden = facade.EXCLUDED_PATHS + facade.CURATED_OUT
    hidden = {(m, p) for m, p in operations(spec)
              if matches(still_hidden, m, p)}
    expected = set(operations(spec)) - hidden
    assert {(t._route.method, t._route.path) for t in admin_tools} == expected
    assert len(admin_tools) == len(expected) == 75, \
        sorted({(t._route.method, t._route.path) for t in admin_tools})
    names = {t.name for t in admin_tools}
    for gated in ("mint_api_key", "put_secret", "delete_agent", "import_agents",
                  "prune_transcripts", "discard_dlq", "change_password"):
        assert gated in names, f"{gated} not restored by the flag"
    for curated in ("tool_wizard", "save_report", "setup_state"):
        assert curated not in names, f"{curated} came back with the flag"


def test_gated_tools_hidden_by_default(tools):
    """Sharp tools are off the default menu; the day-to-day KEEP set is on."""
    names = {t.name for t in tools}
    for gated in ("mint_api_key", "revoke_api_key", "put_secret", "delete_agent",
                  "import_agents", "prune_transcripts", "discard_dlq",
                  "change_password"):
        assert gated not in names, f"{gated} leaked into the default surface"
    for kept in ("list_agents", "create_run"):
        assert kept in names, f"{kept} missing from the default surface"


def test_method_scoped_gates_do_not_overreach(tools):
    """Method-scoping must not hide sibling verbs on a gated/curated path."""
    surface = {(t._route.method, t._route.path) for t in tools}
    # DELETE /api/agents/{name} gates; GET/PUT stay.
    assert ("GET", "/api/agents/{name}") in surface
    assert ("PUT", "/api/agents/{name}") in surface
    assert ("DELETE", "/api/agents/{name}") not in surface
    # GET /api/reports stays; POST (curated) and DELETE {id} (gated) go.
    assert ("GET", "/api/reports") in surface
    assert ("POST", "/api/reports") not in surface
    assert ("DELETE", "/api/reports/{report_id}") not in surface
    # GET+DELETE /api/memories/{id} stay; PATCH (edit_memory, curated) goes.
    assert ("GET", "/api/memories/{memory_id}") in surface
    assert ("DELETE", "/api/memories/{memory_id}") in surface
    assert ("PATCH", "/api/memories/{memory_id}") not in surface


def test_renames_applied(tools, admin_tools):
    """The ambiguous names are clarified and the old ones are gone."""
    default = {t.name for t in tools}
    for new in ("metrics_overview", "metrics_by_model", "metrics_by_agent",
                "metrics_by_tool", "notify_channel", "post_conversation_message",
                "set_schedule_enabled"):
        assert new in default, f"{new} not applied"
    for old in ("overview", "by_model", "per_agent", "metrics_tools",
                "notify", "post_message", "set_enabled"):
        assert old not in default, f"old name {old} still present"
    # The two gated renames apply only when the flag exposes them.
    admin = {t.name for t in admin_tools}
    assert "audit_secret_access" in admin and "secret_access" not in admin
    assert "list_integrations" in admin and "integrations" not in admin


def test_every_mcp_name_key_is_a_real_operation(spec):
    """A rename keyed on a vanished operationId silently does nothing — pin
    each MCP_NAMES key to a real route function name (operationId)."""
    op_ids = {op.get("operationId") for ops in spec["paths"].values()
              for op in ops.values() if isinstance(op, dict)}
    for key in facade.MCP_NAMES:
        assert key in op_ids, f"MCP_NAMES key {key!r} is not an operationId"


def test_admin_tools_enabled_parses_env(monkeypatch):
    """The flag is off unless explicitly truthy; whitespace/case tolerant."""
    for raw, want in (("", False), ("0", False), ("no", False), (" ", False),
                      ("1", True), ("true", True), ("TRUE", True),
                      (" yes ", True), ("on", True)):
        monkeypatch.setenv("AP_MCP_ADMIN_TOOLS", raw)
        assert facade.admin_tools_enabled() is want, raw
    monkeypatch.delenv("AP_MCP_ADMIN_TOOLS", raising=False)
    assert facade.admin_tools_enabled() is False


def test_a_sampled_tool_carries_its_json_schema(tools):
    """Generated tools are usable, not just present: the run-creation tool
    exposes the request body as JSON Schema properties."""
    create = next(t for t in tools if t._route.path == "/api/runs"
                  and t._route.method == "POST")
    schema = create.parameters
    assert schema["type"] == "object"
    assert "agent" in schema["properties"], schema
    get = next(t for t in tools if t._route.path == "/api/runs/{run_id}")
    assert "run_id" in get.parameters["properties"]
    assert "run_id" in get.parameters.get("required", [])


# --- auth passthrough --------------------------------------------------------

class FakeRequest:
    def __init__(self, headers):
        self.headers = headers


def hooked(monkeypatch, request):
    """One upstream request, stamped by the hook as if `request` were the
    caller currently being served."""
    monkeypatch.setattr(facade, "current_request", lambda: request)
    upstream = httpx.Request("GET", "http://api/api/runs")
    asyncio.run(facade.forward_caller_auth(upstream))
    return upstream


def test_callers_bearer_is_forwarded_verbatim(monkeypatch):
    req = hooked(monkeypatch, FakeRequest({"authorization": "Bearer ap_secret"}))
    assert req.headers["Authorization"] == "Bearer ap_secret"


def test_no_authorization_is_invented(monkeypatch):
    """The facade holds no credential: an anonymous caller reaches the API
    anonymously and collects its 401."""
    assert "Authorization" not in hooked(monkeypatch, FakeRequest({})).headers
    assert "Authorization" not in hooked(monkeypatch, None).headers


def test_nothing_but_the_bearer_reaches_the_api(monkeypatch):
    """The header allowlist, and the whole reason it is one: fastmcp copies the
    caller's headers onto the upstream request, and the API's authenticate()
    tries the session COOKIE before the bearer. A reader key plus a stray
    `ap_session` cookie must not become admin."""
    smuggled = {"cookie": "ap_session=stolen-admin-session",
                "x-ap-run-token": "a-run-jwt", "x-forwarded-for": "10.0.0.1",
                "x-ap-user": "admin", "authorization": "Bearer ap_reader"}
    monkeypatch.setattr(facade, "current_request", lambda: FakeRequest(smuggled))
    # As fastmcp hands it over: the caller's headers already copied on.
    upstream = httpx.Request("POST", "http://api/api/agents", json={},
                             headers={k: v for k, v in smuggled.items()
                                      if k != "authorization"})
    asyncio.run(facade.forward_caller_auth(upstream))
    assert upstream.headers["Authorization"] == "Bearer ap_reader"
    for name in ("cookie", "x-ap-run-token", "x-forwarded-for", "x-ap-user"):
        assert name not in upstream.headers, f"{name} reached the API"
    # The body still has to be sendable.
    assert upstream.headers["content-type"] == "application/json"
    assert "content-length" in upstream.headers


def test_concurrent_callers_never_borrow_each_others_bearer(monkeypatch):
    """The upstream client is shared and calls interleave: each in-flight
    request must carry exactly the bearer of the caller it belongs to."""
    ctx = contextvars.ContextVar("caller")
    monkeypatch.setattr(facade, "current_request", lambda: ctx.get(None))

    async def call(bearer):
        ctx.set(FakeRequest({"authorization": bearer} if bearer else {}))
        await asyncio.sleep(0)  # force the tasks to interleave
        req = httpx.Request("GET", "http://api/api/runs")
        await asyncio.sleep(0)
        await facade.forward_caller_auth(req)
        return req.headers.get("Authorization")

    async def main():
        return await asyncio.gather(call("Bearer ap_one"), call("Bearer ap_two"),
                                    call(None))

    assert asyncio.run(main()) == ["Bearer ap_one", "Bearer ap_two", None]


def test_a_previous_callers_bearer_is_never_reused(monkeypatch):
    """The upstream client is shared; the header must be per request."""
    upstream = httpx.Request("GET", "http://api/api/runs",
                             headers={"Authorization": "Bearer ap_someone_else"})
    monkeypatch.setattr(facade, "current_request", lambda: FakeRequest({}))
    asyncio.run(facade.forward_caller_auth(upstream))
    assert "Authorization" not in upstream.headers


def test_spec_fetch_retries_until_the_api_answers(monkeypatch):
    """ap-api is routinely not up yet when this pod starts."""
    calls, slept = [], []
    def get(url, timeout=None):
        calls.append(url)
        if len(calls) < 3:
            raise httpx.ConnectError("connection refused")
        return httpx.Response(200, json={"openapi": "3.1.0", "paths": {}},
                              request=httpx.Request("GET", url))
    monkeypatch.setattr(facade.httpx, "get", get)
    spec = facade.fetch_spec("http://api:8000", sleep=slept.append)
    assert spec["openapi"] == "3.1.0"
    assert calls == ["http://api:8000/openapi.json"] * 3
    assert len(slept) == 2


# --- the /mcp door -----------------------------------------------------------

INITIALIZE = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "t", "version": "1"}}}
MCP_ACCEPT = "application/json, text/event-stream"


def door(spec, headers, method="POST", json=INITIALIZE):
    """One HTTP request at the facade's front door, through the real ASGI app
    with the real middleware stack."""
    mcp = facade.build(spec, client=httpx.AsyncClient(base_url="http://itest"))
    app = mcp.http_app(path="/mcp", middleware=facade.MIDDLEWARE)

    async def go():
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport,
                                         base_url="http://facade") as c:
                return await c.request(method, "/mcp", json=json, headers=headers)
    return asyncio.run(go())


def test_the_door_is_shut_without_an_authorization_header(spec):
    """Not validation — the API does that on every real call — but presence:
    a keyless client must not be able to read the whole API's schema."""
    r = door(spec, {"Accept": MCP_ACCEPT})
    assert r.status_code == 401
    assert r.text == ""                    # detail-free: nothing to learn here
    assert "tool" not in r.text.lower()


def test_the_door_is_shut_for_the_sse_stream_too(spec):
    assert door(spec, {"Accept": "text/event-stream"}, method="GET",
                json=None).status_code == 401


def test_a_key_bearing_client_gets_through_the_door(spec):
    """The gate is presence-only: an obviously-bogus key still reaches MCP,
    where the first tool call collects the API's own 401."""
    r = door(spec, {"Accept": MCP_ACCEPT, "Authorization": "Bearer ap_whatever"})
    assert r.status_code != 401
    assert "protocolVersion" in r.text
