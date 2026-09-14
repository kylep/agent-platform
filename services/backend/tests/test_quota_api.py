"""The quota surface (docs/design/22): what anyone may read, what the proxy may
report, and the one probe the platform will pay for.

Three doors, three different answers to "who are you". `GET /api/quota` and its
stream are open to every participant — a reader session and a per-run `relay`
token see the same snapshot, because a number everybody is bounded by is not a
secret from anyone. `POST /api/quota/refresh` is open to the same set, and the
guard there is not the role but the SHORT-CIRCUIT: the cost of the tool has to
be bounded platform-wide or a looping agent turns a usage check into the thing
that exhausts the usage. And `POST /api/internal/quota` is open to nothing but
the shared secret, because nginx calls it with no session and no API key.

No test here opens a socket: the probe's HTTP client is injected onto
`app.state` as an `httpx.MockTransport`, so "what did the platform send to
Anthropic" is an assertion rather than a hope."""
import asyncio
import json
from datetime import timedelta

import httpx

from agentplatform import quota_store
from agentplatform.db import utcnow
from agentplatform.events import TOPIC_QUOTA_EVENTS
from agentplatform.quota import (H_5H_RESET, H_5H_UTILIZATION, H_7D_RESET,
                                 H_7D_UTILIZATION, H_STATUS, parse_observation)
from agentplatform.quota_store import STREAM

from .test_relay_api import _agent_token, _human_token, _seed, token_client  # noqa: F401
from .test_relay_sse import StubConsumer, _msg, sse  # noqa: F401

SECRET = "s" * 32
PROXY = "http://claude-proxy:8000"


def _epoch(hours: float) -> str:
    return str(int((utcnow() + timedelta(hours=hours)).timestamp()))


def usage_headers(util="0.22", *, five_hour_in=4.0) -> dict:
    """What Anthropic puts on a response. The resets are in the FUTURE by
    default because a snapshot whose window has already turned over is stale,
    and staleness is what half these tests are steering."""
    return {H_5H_UTILIZATION: util, H_5H_RESET: _epoch(five_hour_in),
            H_7D_UTILIZATION: "0.81", H_7D_RESET: _epoch(24 * 3),
            H_STATUS: "allowed"}


def arm_proxy(client, *steps, url=PROXY, delay=0.0):
    """Point the app's probe at a fake proxy that answers `steps` in order (the
    last one repeats). A step is a header dict, or None for a response that
    carries no usage headers at all. Returns the list of (path, body) the probe
    actually sent."""
    calls: list[tuple[str, dict]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, json.loads(request.content or b"{}")))
        if delay:
            await asyncio.sleep(delay)
        step = steps[min(len(calls) - 1, len(steps) - 1)]
        return httpx.Response(200, headers=step or {}, json={"input_tokens": 1})

    app = client._transport.app
    app.state.settings.claude_proxy_url = url
    app.state.quota_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return calls


def arm_internal(client, secret=SECRET):
    client._transport.app.state.settings.internal_secret = secret
    return {"X-AP-Internal-Secret": secret}


def observe_body(headers=None, **extra) -> dict:
    return {"headers": usage_headers() if headers is None else headers,
            "status": 200, **extra}


async def _snapshot(client) -> dict:
    r = await client.get("/api/quota")
    assert r.status_code == 200, r.text
    return r.json()


# --- reading ------------------------------------------------------------------

async def test_get_before_any_observation_is_nulls_and_stale(admin_client):
    """The sidebar draws before the platform has ever seen a header, so the
    empty answer is the same shape with nulls — not a 404 the client has to
    special-case."""
    body = await _snapshot(admin_client)
    assert body["five_hour"] == {"utilization": None, "resets_at": None}
    assert body["seven_day"] == {"utilization": None, "resets_at": None}
    assert (body["stale"], body["observed_at"], body["age_seconds"]) == (True, None, None)


async def test_an_unauthenticated_read_is_401(token_client):
    assert (await token_client.get("/api/quota")).status_code == 401
    assert (await token_client.post("/api/quota/refresh")).status_code == 401


async def test_a_reader_may_read_and_refresh(admin_client, token_client, sf):
    """AC-4: the refresh button is in the web UI, and a reader is allowed to
    press it. The cost is bounded by the short-circuit, not by the role."""
    headers = await _human_token(sf, "rita", "reader")
    calls = arm_proxy(admin_client, usage_headers())
    assert (await token_client.get("/api/quota", headers=headers)).status_code == 200
    r = await token_client.post("/api/quota/refresh", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["five_hour"]["utilization"] == 0.22
    assert len(calls) == 1


async def test_a_run_token_may_read_and_refresh(admin_client, token_client, sf,
                                                seed_agent, agent_store):
    """The `relay` role is what a run carries, and the quota tool calls these
    two routes with it. No grant check: every participant is bounded by the
    same window, so every participant may look at it."""
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    calls = arm_proxy(admin_client, usage_headers())
    assert (await token_client.get("/api/quota", headers=headers)).status_code == 200
    r = await token_client.post("/api/quota/refresh", headers=headers)
    assert r.status_code == 200 and len(calls) == 1


# --- the proxy's report -------------------------------------------------------

async def test_internal_observe_writes_the_row_and_publishes(admin_client, producer):
    """The hot path: every response the proxy relays carries these headers, so
    the snapshot is usually free — nobody spent a token to learn it."""
    headers = arm_internal(admin_client)
    r = await admin_client.post("/api/internal/quota", json=observe_body(),
                                headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["five_hour"]["utilization"] == 0.22
    assert r.json()["source"] == "proxy"
    assert [t for t, _, _ in producer.published] == [TOPIC_QUOTA_EVENTS]
    assert (await _snapshot(admin_client))["stale"] is False


async def test_internal_observe_takes_observed_at_from_the_body(admin_client):
    """A report that queued behind something must not look newer than it is."""
    headers = arm_internal(admin_client)
    when = (utcnow() - timedelta(minutes=10)).isoformat()
    r = await admin_client.post("/api/internal/quota",
                                json=observe_body(observed_at=when), headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["age_seconds"] >= 600


async def test_internal_observe_rejects_a_wrong_secret(admin_client, producer):
    """A wrong secret and a missing one get the SAME answer: which half was
    wrong is exactly what an enumerator wants told. The admin session the
    client is carrying opens nothing here — this door knows one credential."""
    arm_internal(admin_client)
    for sent in ({"X-AP-Internal-Secret": "nope"}, {}):
        r = await admin_client.post("/api/internal/quota", json=observe_body(),
                                    headers=sent)
        assert (r.status_code, r.json()) == (401, {"detail": "unauthorized"})
    assert producer.published == []


async def test_internal_observe_is_503_when_no_secret_is_configured(admin_client):
    """Fail closed: an API with no secret has no way to tell the proxy from
    anyone else, so it accepts nobody rather than everybody."""
    arm_internal(admin_client, secret="")
    r = await admin_client.post("/api/internal/quota", json=observe_body(),
                                headers={"X-AP-Internal-Secret": SECRET})
    assert r.status_code == 503


async def test_internal_observe_ignores_a_body_with_no_known_headers(admin_client,
                                                                     producer):
    """Most responses the proxy sees say nothing about usage. Those are not
    errors and they must not overwrite the snapshot with nulls.

    A 200 WITH A BODY, never a 204: the proxy is njs, and njs 1.0.0's
    `ngx.fetch` never settles its promise on a bodyless 204 — so the outcome
    the proxy hits most often would hang it until nginx timed the request
    out."""
    headers = arm_internal(admin_client)
    r = await admin_client.post("/api/internal/quota",
                                json=observe_body({"content-type": "application/json"}),
                                headers=headers)
    assert r.status_code == 200, r.text
    assert r.json() == {"ignored": True}
    assert producer.published == []
    assert (await _snapshot(admin_client))["observed_at"] is None


async def test_no_internal_outcome_is_ever_a_bodyless_204(admin_client):
    """The njs constraint, as a rule rather than one case: every answer this
    route can give carries a body, so `ngx.fetch` always settles."""
    headers = arm_internal(admin_client)
    calls = [
        ("ignored", observe_body({"content-type": "application/json"}), headers),
        ("recorded", observe_body(), headers),
        ("unauthorized", observe_body(), {"X-AP-Internal-Secret": "nope"}),
        ("unprocessable", {"headers": {f"h{i}": "1" for i in range(129)}}, headers),
    ]
    for name, payload, sent in calls:
        r = await admin_client.post("/api/internal/quota", json=payload, headers=sent)
        assert r.status_code != 204, name
        assert r.content, f"{name} answered {r.status_code} with an empty body"


# --- the probe ----------------------------------------------------------------

async def test_refresh_stops_at_count_tokens_when_it_carries_the_headers(admin_client):
    """The cheap step: no output tokens at all. `probe` names which step
    answered so live verification can read Anthropic's actual behaviour off the
    response instead of guessing at it."""
    calls = arm_proxy(admin_client, usage_headers())
    r = await admin_client.post("/api/quota/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["probe"] == "count_tokens"
    assert r.json()["source"] == "refresh"
    assert len(calls) == 1
    path, body = calls[0]
    assert path == "/v1/messages/count_tokens"
    assert body["messages"] == [{"role": "user", "content": "."}]
    assert "max_tokens" not in body
    # The model is the SETTING, not a literal in the probe: an operator whose
    # account cannot reach the default has to be able to point it elsewhere.
    assert body["model"] == admin_client._transport.app.state.settings.quota_probe_model


async def test_refresh_falls_through_to_a_one_token_message(admin_client):
    """count_tokens said nothing about usage, so the probe pays the floor for a
    real completion: one input-ish token and one output token."""
    calls = arm_proxy(admin_client, None, usage_headers("0.44"))
    r = await admin_client.post("/api/quota/refresh")
    assert r.status_code == 200, r.text
    assert r.json()["probe"] == "message"
    assert r.json()["five_hour"]["utilization"] == 0.44
    assert [p for p, _ in calls] == ["/v1/messages/count_tokens", "/v1/messages"]
    assert calls[1][1]["max_tokens"] == 1


async def test_refresh_sends_the_placeholder_bearer_and_the_beta_header(admin_client):
    """The proxy replaces the credential (docs/design/09): the API never holds
    the token, so what it sends is a placeholder plus the two version headers
    the OAuth path needs."""
    sent: list[httpx.Request] = []

    async def handler(request):
        sent.append(request)
        return httpx.Response(200, headers=usage_headers(), json={})

    app = admin_client._transport.app
    app.state.settings.claude_proxy_url = PROXY
    app.state.quota_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert (await admin_client.post("/api/quota/refresh")).status_code == 200
    h = sent[0].headers
    assert h["authorization"] == "Bearer placeholder"
    assert h["anthropic-version"] == "2023-06-01"
    assert h["anthropic-beta"] == "oauth-2025-04-20"
    assert h["content-type"] == "application/json"


async def test_refresh_observes_headers_on_an_error_response(admin_client, sf):
    """A 429 is exactly when the numbers matter most, and it carries them."""
    async def handler(request):
        return httpx.Response(429, headers=usage_headers("0.99"), json={})

    app = admin_client._transport.app
    app.state.settings.claude_proxy_url = PROXY
    app.state.quota_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = await admin_client.post("/api/quota/refresh")
    assert r.status_code == 200, r.text
    assert (r.json()["probe"], r.json()["five_hour"]["utilization"]) == ("count_tokens", 0.99)
    async with sf() as s:
        assert (await quota_store.latest(s)).raw["http_status"] == "429"


async def test_refresh_is_503_when_neither_step_carries_headers(admin_client):
    calls = arm_proxy(admin_client, None)
    r = await admin_client.post("/api/quota/refresh")
    assert (r.status_code, r.json()["detail"]) == (503, "the probe returned no usage headers")
    assert len(calls) == 2


async def test_refresh_is_503_without_a_proxy_configured(admin_client):
    """Dev has no proxy. The refresh says so plainly rather than dialling an
    empty base URL."""
    admin_client._transport.app.state.settings.claude_proxy_url = ""
    r = await admin_client.post("/api/quota/refresh")
    assert (r.status_code, r.json()["detail"]) == (503, "no claude proxy configured")


async def test_refresh_is_503_when_the_proxy_is_unreachable(admin_client):
    async def handler(request):
        raise httpx.ConnectError("nope")

    app = admin_client._transport.app
    app.state.settings.claude_proxy_url = PROXY
    app.state.quota_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    r = await admin_client.post("/api/quota/refresh")
    assert r.status_code == 503
    assert "proxy" in r.json()["detail"]


# --- what the refresh costs ---------------------------------------------------

async def test_two_concurrent_refreshes_probe_once(admin_client):
    """One in-flight probe per process: the second caller waits for the first
    and is answered from what it observed."""
    calls = arm_proxy(admin_client, usage_headers(), delay=0.05)
    first, second = await asyncio.gather(admin_client.post("/api/quota/refresh"),
                                         admin_client.post("/api/quota/refresh"))
    assert (first.status_code, second.status_code) == (200, 200)
    assert len(calls) == 1
    assert {first.json()["probe"], second.json()["probe"]} == {"count_tokens", None}


async def test_a_refresh_just_after_a_fresh_one_returns_the_cache(admin_client):
    """The platform-wide rate limit: a looping agent hitting the tool cannot
    spend more than one probe per QUOTA_REFRESH_MIN_SECONDS."""
    calls = arm_proxy(admin_client, usage_headers())
    assert (await admin_client.post("/api/quota/refresh")).json()["probe"] == "count_tokens"
    again = await admin_client.post("/api/quota/refresh")
    assert again.status_code == 200
    assert again.json()["probe"] is None
    assert again.json()["five_hour"]["utilization"] == 0.22
    assert len(calls) == 1


async def test_a_stale_snapshot_is_probed_even_when_it_is_recent(admin_client):
    """Recency is not the whole test: a window that has already reset makes the
    cached answer a description of a window that no longer exists."""
    arm_internal(admin_client)
    stale = {**usage_headers(), H_5H_RESET: _epoch(-1)}
    await admin_client.post("/api/internal/quota", json=observe_body(stale),
                            headers=arm_internal(admin_client))
    calls = arm_proxy(admin_client, usage_headers())
    r = await admin_client.post("/api/quota/refresh")
    assert (r.status_code, r.json()["probe"]) == (200, "count_tokens")
    assert len(calls) == 1


async def test_the_short_circuit_window_is_a_setting(admin_client):
    """Turned to zero, every call probes — which is what a test that wants a
    second observation does, and what an operator would do to debug one."""
    admin_client._transport.app.state.settings.quota_refresh_min_seconds = 0
    calls = arm_proxy(admin_client, usage_headers())
    await admin_client.post("/api/quota/refresh")
    await admin_client.post("/api/quota/refresh")
    assert len(calls) == 2


# --- the stream ---------------------------------------------------------------

async def test_the_stream_carries_a_quota_frame(admin_client, sf, producer):
    """Kafka-fed like the wiki's: an observation made by ANY pod reaches this
    one's sidebars off `quota.events`."""
    feed = admin_client._transport.app.state.quota_feed
    async with sse(admin_client, "/api/quota/events") as (resp, stream):
        assert resp["status"] == 200
        assert resp["headers"]["content-type"].startswith("text/event-stream")
        async with sf() as s:
            row = await quota_store.observe(
                s, producer, parse_observation(usage_headers("0.33"), utcnow(), "proxy"))
            payload = quota_store.serialize(row, utcnow())
        await feed.run(StubConsumer([_msg(TOPIC_QUOTA_EVENTS, STREAM,
                                          "quota.event", payload)]))
        event, data, _ = await stream.event()
    assert event == "quota"
    assert data["five_hour"]["utilization"] == 0.33


# --- the internal door's edges ------------------------------------------------

async def test_a_garbage_body_without_the_secret_is_401_not_422(admin_client):
    """The ORDER of the two checks, which is the whole reason this route parses
    its own body. A schema error tells an anonymous caller the shape of an
    endpoint they cannot use — and they had a body parsed to earn it."""
    arm_internal(admin_client)
    r = await admin_client.post("/api/internal/quota", content=b"{not json at all",
                                headers={"content-type": "application/json"})
    assert (r.status_code, r.json()) == (401, {"detail": "unauthorized"})


async def test_an_oversized_report_is_refused_before_it_is_parsed(admin_client):
    headers = arm_internal(admin_client)
    fat = {"headers": {H_5H_UTILIZATION: "0.22", "x-pad": "p" * 100_000}}
    r = await admin_client.post("/api/internal/quota", json=fat, headers=headers)
    assert r.status_code == 413


async def test_an_oversized_report_without_a_content_length_is_still_refused(admin_client):
    """Content-Length is a claim, not a fact: a chunked request carries none.
    The stream cap is the check that actually holds."""
    headers = arm_internal(admin_client)

    async def chunks():
        yield b'{"headers": {"x": "'
        for _ in range(20):
            yield b"p" * 8192
        yield b'"}}'

    r = await admin_client.post("/api/internal/quota", content=chunks(),
                                headers={**headers, "content-type": "application/json"})
    assert r.status_code == 413


async def test_too_many_headers_is_unprocessable(admin_client, producer):
    """A well-formed body under the byte cap can still be ten thousand
    one-byte headers, so the shape is bounded too."""
    headers = arm_internal(admin_client)
    many = {f"anthropic-ratelimit-unified-x{i}": "1" for i in range(129)}
    r = await admin_client.post("/api/internal/quota", json={"headers": many},
                                headers=headers)
    assert r.status_code == 422
    assert producer.published == []


async def test_an_overlong_header_value_is_unprocessable(admin_client):
    headers = arm_internal(admin_client)
    r = await admin_client.post("/api/internal/quota",
                                json={"headers": {H_5H_UTILIZATION: "0" * 513}},
                                headers=headers)
    assert r.status_code == 422


async def test_a_non_ascii_secret_compares_without_exploding(admin_client):
    """`hmac.compare_digest` on `str` raises TypeError the moment either side
    is non-ASCII, and the SERVER-side secret is the half nothing sanitises —
    it comes from helm values. Every call would then 500, the right secret
    included, and it would read as a proxy fault. Hashing both sides first is
    what makes the compare total.

    Sent as bytes because a header is latin-1 on the wire: httpx will not
    encode a `str` header outside ASCII at all."""
    secret = "sécret-ünicode-clé"
    admin_client._transport.app.state.settings.internal_secret = secret
    wrong = await admin_client.post(
        "/api/internal/quota", json=observe_body(),
        headers={"X-AP-Internal-Secret": "sécret-other".encode("latin-1")})
    assert (wrong.status_code, wrong.json()) == (401, {"detail": "unauthorized"})
    right = await admin_client.post(
        "/api/internal/quota", json=observe_body(),
        headers={"X-AP-Internal-Secret": secret.encode("latin-1")})
    assert right.status_code == 200, right.text
    assert right.json()["five_hour"]["utilization"] == 0.22


async def test_a_future_observed_at_is_clamped_to_now(admin_client):
    """Otherwise one report dated 9999 outranks every later observation for
    good and the snapshot never moves again."""
    headers = arm_internal(admin_client)
    r = await admin_client.post("/api/internal/quota",
                                json=observe_body(observed_at="9999-01-01T00:00:00Z"),
                                headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["age_seconds"] == 0
    # And the proof it did not wedge: the next report still lands.
    later = await admin_client.post(
        "/api/internal/quota",
        json={"headers": usage_headers("0.77"), "status": 200}, headers=headers)
    assert later.json()["five_hour"]["utilization"] == 0.77


async def test_the_reported_http_status_is_kept_in_raw(admin_client, sf):
    """Diagnostic only, but a 0.99 off a 429 and a 0.99 off a 200 are not the
    same fact about the account."""
    headers = arm_internal(admin_client)
    r = await admin_client.post("/api/internal/quota",
                                json={"headers": usage_headers("0.99"), "status": 429},
                                headers=headers)
    assert r.status_code == 200, r.text
    async with sf() as s:
        assert (await quota_store.latest(s)).raw["http_status"] == "429"


# --- the default grant --------------------------------------------------------
# "Default-granted" is rows, not a special case in the broker: a created agent
# is born holding the tool while `quota_default_grant` says so, and an admin can
# take it away afterwards like any other grant.

QUOTA_GRANT = "mcp__platform__get_quota_usage"


async def test_a_new_agent_is_born_able_to_read_the_usage(admin_client, sf):
    from agentplatform.db import AgentDef
    r = await admin_client.post("/api/agents", json={"name": "newbie",
                                                     "description": "test",
                                                     "prompt": "# newbie"})
    assert r.status_code == 201, r.text
    assert QUOTA_GRANT in r.json()["platform_tools"]
    async with sf() as s:
        assert QUOTA_GRANT in (await s.get(AgentDef, "newbie")).platform_tools


async def test_the_usage_default_can_be_turned_off_platform_wide(admin_client, sf):
    admin_client._transport.app.state.settings.quota_default_grant = False
    r = await admin_client.post("/api/agents", json={"name": "blind",
                                                     "description": "test",
                                                     "prompt": "# blind"})
    assert r.status_code == 201, r.text
    assert QUOTA_GRANT not in r.json()["platform_tools"]
