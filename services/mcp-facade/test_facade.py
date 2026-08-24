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


@pytest.fixture(scope="module")
def tools(spec):
    """The generated tool surface, as (name, method, path) plus the tool."""
    mcp = facade.build(spec, client=httpx.AsyncClient(base_url="http://itest"))
    return asyncio.run(mcp.list_tools())


def operations(spec):
    return [(m.upper(), p) for p, ops in spec["paths"].items()
            for m in ops if m in METHODS]


def test_every_exclusion_still_matches_a_real_path(spec):
    """A stale exclusion is worse than none: it silently stops protecting
    anything when a path is renamed."""
    paths = list(spec["paths"])
    for pattern in facade.EXCLUDED_PATHS:
        assert [p for p in paths if re.search(pattern, p)], \
            f"exclusion {pattern} matches no path in the API — stale?"


def test_excluded_paths_produce_no_tools(spec, tools):
    """The design's list, verbatim: session auth, run-scoped internals, and
    webhook ingress are not reachable from an API key."""
    exposed = {t._route.path for t in tools}
    for path in ("/api/login", "/api/logout", "/api/setup",
                 "/api/runs/{run_id}/session", "/api/runs/{run_id}/agentdef",
                 "/api/webhooks/{path}"):
        assert path in spec["paths"], f"{path} vanished from the API"
        assert path not in exposed


def test_setup_state_is_not_caught_by_the_setup_exclusion(tools):
    """The exclusions are anchored regexes — `/api/setup-state` is a normal
    read and must survive `/api/setup` being excluded."""
    assert "/api/setup-state" in {t._route.path for t in tools}


def test_everything_else_is_a_tool(spec, tools):
    """Full API capability by construction: exactly the operations that are
    not excluded, no hand-curated allowlist to drift."""
    excluded = {(m, p) for m, p in operations(spec)
                if any(re.search(x, p) for x in facade.EXCLUDED_PATHS)}
    assert len(excluded) == 7, sorted(excluded)
    assert len(tools) == len(operations(spec)) - len(excluded)
    assert {(t._route.method, t._route.path) for t in tools} == \
        set(operations(spec)) - excluded


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
