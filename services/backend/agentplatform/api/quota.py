"""The usage snapshot's REST surface (docs/design/22): what everyone may read,
what the proxy reports, and the one probe the platform will pay for.

Four doors, and they differ in who may knock rather than in what they say —
the first three answer with `quota_store.serialize`, so the sidebar, the
stream, the tool and the proxy's own ack can never disagree about what `stale`
means, and the fourth is that same snapshot reduced to one boolean.

- READING is open to every participant (`READ_ROLES` + the per-run `relay`
  role). There is no grant behind it and no room to be a member of: a number
  the whole platform is bounded by is not a secret from anyone bounded by it.
- REFRESHING is open to the same set, on purpose — AC-4 puts the button in the
  web UI where a reader can press it. What bounds the cost is not the role but
  the SHORT-CIRCUIT below: one in-flight probe per process, and a cached answer
  for anything arriving within `quota_refresh_min_seconds` of a snapshot that
  is still believable. A looping agent hammering the tool therefore spends one
  probe per interval, not one per call.
- OBSERVING (`/api/internal/quota`) is nginx's door and accepts exactly one
  credential, because the proxy has no session, no API key and no service
  account. It is registered on its own router with NO auth dependency — the
  secret IS the authentication here — and is excluded from the facade.
- DECIDING (`/api/quota/ok`, docs/design/24) is the reading turned into one
  boolean for the caller in front of it. Open to the same set as reading, and
  it spends a probe only when the reading is stale — through the same lock
  and short-circuit as REFRESHING — so an agent that asks at the top of every
  run costs the platform nothing most of the time.

The probe runs HERE rather than in the broker or the runner because the
subscription token lives in the claude-proxy (docs/design/09) and nowhere else:
the API asks the proxy, the proxy signs the request. Which of the two probe
steps answered is reported back as `probe`, since Anthropic's behaviour around
`count_tokens` was not observable from this machine before implementation."""
import asyncio
import hashlib
import hmac
import json
import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from agentplatform import quota_store
from agentplatform.api import relay as relay_api
from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, require_role
from agentplatform.db import AgentDef, utcnow
from agentplatform.quota import (Observation, _percent, is_stale,
                                 parse_codex_usage, parse_observation,
                                 parse_observed_at)
from agentplatform.quota_store import STREAM
from agentplatform.relay_feed import OVERFLOW

log = logging.getLogger("quota")

router = APIRouter(tags=["quota"])
# The proxy's report has no dependency on this router at all: no session, no
# API key, no role. Separate so that stays visible rather than being one route
# in a file whose others are all guarded.
internal_router = APIRouter(tags=["quota"])

# Who may look: every human read role plus `relay`, the role a run carries.
VIEW = (*READ_ROLES, "relay")
# The credential the proxy presents. Compared with `hmac.compare_digest`, as
# `webhooksecrets.verify_secret` does, and answered with a uniform 401 that
# says nothing about which part was wrong.
SECRET_HEADER = "X-AP-Internal-Secret"
# What the probe sends. The bearer is a placeholder the proxy replaces — the
# API never holds the token — and the beta header is what the OAuth
# subscription path needs to be honoured at all.
PROBE_HEADERS = {"Authorization": "Bearer placeholder",
                 "anthropic-version": "2023-06-01",
                 "anthropic-beta": "oauth-2025-04-20",
                 "content-type": "application/json"}
# The cheapest thing that can carry a usage header, twice over: `count_tokens`
# spends no output tokens at all, and a `max_tokens: 1` completion is the floor
# for a real one. The order is the whole point — step 2 only runs when step 1's
# response said nothing about usage.
PROBE_MESSAGE = [{"role": "user", "content": "."}]
COUNT_TOKENS, MESSAGES = "/v1/messages/count_tokens", "/v1/messages"
# What a caller with no agent row is judged against: the column defaults, read
# off the column so this file cannot hold a second opinion about them.
_COLUMNS = AgentDef.__table__.c
DEFAULT_MAX_PCT = (_COLUMNS.quota_5h_max_pct.default.arg,
                   _COLUMNS.quota_7d_max_pct.default.arg)
# The biggest report this route will read. A real one is a few hundred bytes;
# this is four orders of magnitude of headroom and still a hard ceiling, so an
# unauthenticated caller cannot make the API buffer a megabyte before the
# secret is even looked at.
MAX_OBSERVE_BYTES = 64 * 1024


def _authenticated(request: Request) -> None:
    """The internal door's whole authorization, and the FIRST thing that runs
    on that route — before the body is touched, let alone parsed.

    Both compares are over SHA-256 digests, the way
    `webhooksecrets.verify_secret` does it. That is not belt-and-braces: it is
    what makes the comparison total. `hmac.compare_digest` on `str` raises
    TypeError the moment either side is non-ASCII, so a secret with an accent
    in it would turn every call — right secret included — into a 500, and the
    fix would look like a proxy fault. Hashing first also makes the compare
    length-independent, so the digest size stops leaking the secret's."""
    secret = request.app.state.settings.internal_secret
    if not secret:
        raise HTTPException(503, "no internal secret configured")
    presented = request.headers.get(SECRET_HEADER, "")
    if not hmac.compare_digest(hashlib.sha256(presented.encode()).digest(),
                               hashlib.sha256(secret.encode()).digest()):
        raise HTTPException(401, "unauthorized")


async def _capped_body(request: Request) -> bytes:
    """The request body, or a 413 — never more than `MAX_OBSERVE_BYTES` held.

    `Content-Length` is checked first because it is free and refuses the
    obvious case outright, but it is a CLAIM, not a fact: a chunked request
    carries none, and a lying one carries the wrong number. So the stream is
    also accumulated with the same cap and abandoned the moment it is passed,
    which is the check that actually holds."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_OBSERVE_BYTES:
        raise HTTPException(413, "the report is too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_OBSERVE_BYTES:
            raise HTTPException(413, "the report is too large")
    return bytes(body)


def _lock(app) -> asyncio.Lock:
    """The per-process probe lock. Lazily made rather than built in
    `create_app` so the lock belongs to the loop that first needs it — the SDK
    generator builds an app with no loop at all."""
    lock = getattr(app.state, "quota_lock", None)
    if lock is None:
        lock = app.state.quota_lock = asyncio.Lock()
    return lock


def _client(app) -> httpx.AsyncClient:
    """The probe's HTTP client, from `app.state` so a test can hand in a
    `MockTransport` and the suite never opens a socket."""
    client = getattr(app.state, "quota_client", None)
    if client is None:
        client = app.state.quota_client = httpx.AsyncClient()
    return client


async def _snapshot(request: Request) -> dict:
    now = utcnow()
    async with request.app.state.session_factory() as s:
        claude = quota_store.serialize(await quota_store.latest(s), now)
        codex_row = await quota_store.latest(s, "codex")
        codex = quota_store.serialize(codex_row, now) if codex_row else None
        return {**claude, "codex": codex}


@router.get("/api/quota", response_model=S.Quota)
async def get_quota(request: Request, caller: str = Depends(require_role(*VIEW))):
    """What Anthropic last said about this account's usage. 200 with nulls and
    `stale: true` before the first observation: the sidebar draws either way,
    and the flag is what tells it which it is drawing."""
    return await _snapshot(request)


@router.post("/api/quota/refresh", response_model=S.Quota)
async def refresh_quota(request: Request,
                        provider: Literal["all", "claude", "codex"] = "all",
                        caller: str = Depends(require_role(*VIEW))):
    """Ask on purpose. Coalesced and rate-limited platform-wide, so this is
    safe to put behind a button and behind a tool.

    Everything happens under the lock, the short-circuit included: a caller
    that arrives during a probe waits for it and then finds the fresh snapshot
    it wrote, which is the same answer it would have got from its own probe and
    one fewer request to Anthropic."""
    async with _lock(request.app):
        snapshot = await _snapshot(request)
        claude = (await _refresh(request) if provider in ("all", "claude")
                  else snapshot)
        codex = snapshot.get("codex")
        if (provider in ("all", "codex")
                and request.app.state.settings.codex_proxy_url):
            try:
                codex = await _refresh_codex(request)
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                log.warning("Codex quota refresh unavailable: %s", exc.detail)
        return {**claude, "codex": codex}


async def _refresh(request: Request) -> dict:
    """The refresh, which the caller runs UNDER THE LOCK: the cached answer
    when the short-circuit allows it, else one probe and the row it wrote.
    Raises the probe's 503s."""
    st = request.app.state
    cached = await _cache_hit(request)
    if cached is not None:
        return {**cached, "probe": None}
    obs, step = await _probe(request)
    async with st.session_factory() as s:
        row = await quota_store.observe(s, st.producer, obs)
        return {**quota_store.serialize(row, utcnow()), "probe": step}


async def _cache_hit(request: Request, provider: str = "claude") -> dict | None:
    """The snapshot, when it is recent enough AND still believable — otherwise
    None, meaning "probe". Both halves are load-bearing: recency alone would
    keep serving a window that has already turned over, and freshness alone
    would let a loop spend a probe per call."""
    st = request.app.state
    now = utcnow()
    async with st.session_factory() as s:
        row = await quota_store.latest(s, provider)
        body = quota_store.serialize(row, now)
    if row is None or is_stale(row, now):
        return None
    age = body["age_seconds"]
    window = st.settings.quota_refresh_min_seconds
    return body if age is not None and age < window else None


async def _probe(request: Request) -> tuple[Observation, str]:
    """Two steps through the proxy, stopping at the first response that says
    anything about usage. Raises the route's 503s."""
    st = request.app.state
    base = (st.settings.claude_proxy_url or "").rstrip("/")
    if not base:
        raise HTTPException(503, "no claude proxy configured")
    body = {"model": st.settings.quota_probe_model, "messages": PROBE_MESSAGE}
    steps = (("count_tokens", COUNT_TOKENS, body),
             ("message", MESSAGES, {**body, "max_tokens": 1}))
    client = _client(request.app)
    for step, path, payload in steps:
        try:
            resp = await client.post(base + path, json=payload, headers=PROBE_HEADERS,
                                     timeout=st.settings.quota_probe_timeout_seconds)
        except httpx.HTTPError:
            log.warning("quota probe could not reach the claude proxy", exc_info=True)
            raise HTTPException(503, "the claude proxy could not be reached")
        # ANY status: a 429 is exactly when these numbers matter most, and it
        # carries them. `parse_observation` is the single place a header
        # becomes a value, here as everywhere, and the status rides along into
        # `raw` for the reason the proxy's does — otherwise `http_status` would
        # be present on proxy rows and absent on refresh rows, and its absence
        # would read as "the status was unknown" rather than "nobody recorded
        # one".
        obs = parse_observation(resp.headers, utcnow(), "refresh",
                                http_status=resp.status_code)
        if obs is not None:
            return obs, step
    raise HTTPException(503, "the probe returned no usage headers")


async def _refresh_codex(request: Request) -> dict:
    """Read subscription usage through the Codex credential boundary."""
    st = request.app.state
    cached = await _cache_hit(request, "codex")
    if cached is not None:
        return {**cached, "probe": None}
    base = (st.settings.codex_proxy_url or "").rstrip("/")
    if not base:
        raise HTTPException(503, "no codex proxy configured")
    try:
        response = await _client(request.app).get(
            base + "/internal/quota",
            headers={SECRET_HEADER: st.settings.internal_secret},
            timeout=st.settings.quota_probe_timeout_seconds)
    except httpx.HTTPError:
        log.warning("quota probe could not reach the codex proxy", exc_info=True)
        raise HTTPException(503, "the codex proxy could not be reached")
    if response.status_code != 200:
        raise HTTPException(503, f"the codex usage probe returned {response.status_code}")
    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(503, "the codex usage probe returned invalid JSON") from None
    obs = parse_codex_usage(payload, utcnow(), "refresh")
    if obs is None:
        raise HTTPException(503, "the codex usage probe returned no known windows")
    async with st.session_factory() as s:
        row = await quota_store.observe(s, st.producer, obs, "codex")
    return {**quota_store.serialize(row, utcnow()), "probe": "usage"}


@router.get("/api/quota/ok", response_model=S.QuotaOk)
async def quota_ok(request: Request, caller: str = Depends(require_role(*VIEW))):
    """May the caller start expensive work now? One boolean, and what it was
    made from (docs/design/24).

    The reading is what the platform already holds unless that is stale, in
    which case this is one `refresh` — the same lock and the same
    short-circuit, so two engineers starting at once cost one probe and a
    loop costs one per window. A refresh that cannot run is not a 503 here:
    the row is still there, `ok` is computed from it, and `stale: true` says
    how much that is worth. The thresholds are the caller's own row's when
    the token names an agent, and the column defaults for a person — a
    human asking is asking what an ordinary agent would be told."""
    async with _lock(request.app):
        body = await _snapshot(request)
        max_5h, max_7d, runtime = await _thresholds(request)
        reading = body.get("codex") if runtime == "codex" else body
        if reading is None or reading["stale"]:
            try:
                reading = (await _refresh_codex(request) if runtime == "codex"
                           else await _refresh(request))
            except HTTPException as exc:
                if exc.status_code != 503:
                    raise
                log.warning("quota gate could not refresh a stale reading: %s",
                            exc.detail)
    return gate(reading or quota_store.serialize(None, utcnow()), max_5h, max_7d,
                runtime)


async def _thresholds(request: Request) -> tuple[int, int, str]:
    """The caller's limits: its agent row's when the token names an agent that
    still exists, the column defaults otherwise. A row that predates the
    columns has been backfilled (`db._ensure_workbench_defaults`), but a null
    is still read as the default rather than as a comparison with None."""
    agent = getattr(request.state, "api_key_agent", None)
    if not agent:
        return (*DEFAULT_MAX_PCT, "claude")
    async with request.app.state.session_factory() as s:
        row = await s.get(AgentDef, agent)
    if row is None:
        return (*DEFAULT_MAX_PCT, "claude")
    return (DEFAULT_MAX_PCT[0] if row.quota_5h_max_pct is None else row.quota_5h_max_pct,
            DEFAULT_MAX_PCT[1] if row.quota_7d_max_pct is None else row.quota_7d_max_pct,
            "codex" if row.runtime == "codex" else "claude")


def gate(snapshot: dict, max_5h: int, max_7d: int, provider: str = "claude") -> dict:
    """The decision, from a serialized snapshot and two limits. Pure, so the
    same rule can be pinned without a request.

    Compared on the ROUNDED percent the answer reports, not the fraction under
    it: a model that reads "80%" beside "80" and gets "no" would be right to
    distrust the field. Above the limit fails, at it passes. A window with no
    reading fails closed — there is nothing to be under."""
    pcts = tuple(None if u is None else _percent(u)
                 for u in (snapshot["five_hour"]["utilization"],
                           snapshot["seven_day"]["utilization"]))
    answer = {"five_hour_pct": pcts[0], "seven_day_pct": pcts[1],
              "five_hour_max_pct": max_5h, "seven_day_max_pct": max_7d,
              "stale": snapshot["stale"], "provider": provider}
    if all(p is None for p in pcts) or (provider == "claude" and None in pcts):
        return {"ok": False, "reason": "no reading yet", **answer}
    over = [f"the {label} window is at {pct}%, over its {limit}% limit"
            for label, pct, limit in (("5-hour", pcts[0], max_5h),
                                      ("7-day", pcts[1], max_7d))
            if pct is not None and pct > limit]
    return {"ok": not over, "reason": ", and ".join(over) or "ok", **answer}


# The request and response shapes, declared for the SPEC by hand.
#
# The body has to be declared here because reading it by hand takes it out of
# the signature, and with it out of the OpenAPI document — which would leave
# the one endpoint the proxy is written against documenting no body at all.
# The schema is the model's own, so the contract cannot drift from what the
# handler validates against; what changes is only WHEN that validation happens.
#
# The 200 has to be declared here because it is genuinely two shapes: the
# snapshot when the report moved it, and `{"ignored": true}` when the report
# carried no usage header. Both are 200 — see `QuotaIgnored` for why the
# second is not a 204 — and a spec claiming only the first would be lying to
# the generated client about the OUTCOME THE PROXY SEES MOST OFTEN.
_OBSERVE_BODY = {
    "requestBody": {
        "required": True,
        "content": {"application/json": {
            "schema": S.QuotaObserveIn.model_json_schema()}},
    },
    "responses": {
        "200": {
            "description": "The snapshot after the report, or `ignored` when "
                           "the report carried no usage header.",
            "content": {"application/json": {"schema": {"oneOf": [
                {"$ref": "#/components/schemas/Quota"},
                S.QuotaIgnored.model_json_schema()]}}},
        },
        "401": {"description": "Missing or wrong internal secret."},
        "413": {"description": "The report exceeds the size cap."},
        "422": {"description": "Malformed or out-of-bounds report."},
        "503": {"description": "The API has no internal secret configured."},
    },
}


@internal_router.post("/api/internal/quota", openapi_extra=_OBSERVE_BODY)
async def observe_quota(request: Request):
    """The proxy's report — the hot path, and the one that costs nothing: every
    response it relays already carries these headers, so the snapshot is
    usually a side effect of work someone else was doing anyway.

    No session and no API key reach this route, by design: nginx presents the
    shared secret and nothing else. A missing server-side secret is a 503
    rather than an open door, and a wrong one is a 401 that does not say
    whether the header was absent or merely wrong.

    The body is read BY HAND, in that order, which is the whole reason this
    route does not declare a pydantic parameter. FastAPI would parse and
    validate the body before the handler runs at all — so an anonymous caller
    would get a 422 describing the schema of an endpoint they cannot use, and
    would have had a megabyte of their JSON parsed to earn it. Here the secret
    is checked against nothing but a header, then the body is read under a
    cap, and only then is it anybody's schema."""
    st = request.app.state
    _authenticated(request)
    raw = await _capped_body(request)
    try:
        body = S.QuotaObserveIn.model_validate(json.loads(raw or b"{}"))
    except (ValueError, ValidationError) as exc:
        # One answer for malformed JSON and for a well-formed document that is
        # the wrong shape: the caller is our own proxy, and the difference is
        # a detail string, not a different outcome.
        raise HTTPException(422, f"unprocessable report: {exc}") from None
    now = utcnow()
    obs = parse_observation(body.headers, parse_observed_at(body.observed_at, now),
                            "proxy", http_status=body.status)
    if obs is None:
        # Most responses say nothing about usage. Writing them would overwrite
        # a real snapshot with nulls, so they are ignored rather than refused —
        # and answered with a BODY, because njs's `ngx.fetch` never settles its
        # promise on a bodyless 204 and the proxy would hang on the outcome it
        # hits most often.
        return {"ignored": True}
    async with st.session_factory() as s:
        row = await quota_store.observe(s, st.producer, obs)
        return quota_store.serialize(row, utcnow())


@router.get("/api/quota/events", response_class=StreamingResponse)
async def quota_stream(request: Request, caller: str = Depends(require_role(*VIEW))):
    """The snapshot, live: a `quota` frame whenever a number moves, a heartbeat
    so an idle stream is not mistaken for a dead one, and an `overflow` marker
    for a reader that fell behind.

    Kafka-fed only, and one stream for the whole platform — there is one
    snapshot, so everyone watching watches the same key."""
    feed = request.app.state.quota_feed
    queue = feed.subscribe(STREAM)

    async def stream():
        try:
            while True:
                try:
                    # Read per wait: it is a module global a test turns down
                    # without patching the route.
                    event, data = await asyncio.wait_for(
                        queue.get(), relay_api.HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event == OVERFLOW:
                    # No cursor to hand back: the client resyncs with one GET,
                    # which is always correct for a singleton.
                    yield relay_api._frame(OVERFLOW, {})
                    continue
                yield relay_api._frame(event, data)
        finally:
            feed.unsubscribe(STREAM, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
