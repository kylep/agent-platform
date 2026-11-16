"""The quota probe/refresh logic, lifted out of `api/quota.py` so a caller with
no FastAPI request (the rapid-chat dispatcher, docs/design/45 section 4) can
use it. Every function takes a `state`: anything with `settings`,
`session_factory` and `producer` attributes, and optionally `quota_client` /
`quota_lock` (made lazily). The API passes `request.app.state`; the dispatcher
a `SimpleNamespace`.

`refresh_claude` / `refresh_codex` raise the probe's 503 `HTTPException`s and
must be run under `lock(state)` by the caller; `refresh` is the entry point
that takes the lock itself and turns a 503 into None."""
import asyncio
import logging
import uuid
from datetime import timedelta

import httpx
from fastapi import HTTPException

from agentplatform import quota_store
from agentplatform.db import ACTIVE_STATES, Run, utcnow
from agentplatform.quota import Observation, is_stale, parse_codex_usage, parse_observation

log = logging.getLogger("quota")

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

# Anthropic's 429 answers to our hand-made probe carry no usage headers, while a
# real Claude Code run does (and the proxy records them). When the direct probe
# yields none, a minimal REAL run of this code-owned agent refreshes the reading
# through the ordinary runner path. At most one in flight, one per 5 minutes.
NO_USAGE_HEADERS = "the probe returned no usage headers"
PROBE_AGENT = "quota-probe"
PROBE_PROMPT = "Reply with a single period."
PROBE_RUN_MIN_INTERVAL = timedelta(minutes=5)


async def launch_probe_run(state) -> str:
    """Kick off the `quota-probe` run without waiting for it. Returns
    "started", "in_flight", "throttled" or "unavailable" (agent missing or
    disabled). Deduped on the runs table, so the API and the dispatcher share
    one budget; the caller holds `lock(state)`."""
    from sqlalchemy import select
    from agentplatform.materialize import materialize_run
    async with state.session_factory() as s:
        last = (await s.execute(select(Run).where(Run.agent == PROBE_AGENT).order_by(
            Run.created_at.desc()).limit(1))).scalars().first()
    if last is not None:
        if last.state in ACTIVE_STATES:
            return "in_flight"
        created = last.created_at if last.created_at.tzinfo else last.created_at.replace(
            tzinfo=utcnow().tzinfo)
        if utcnow() - created < PROBE_RUN_MIN_INTERVAL:
            return "throttled"
    try:
        await materialize_run(state.session_factory, state.producer, {
            "run_id": uuid.uuid4().hex, "agent": PROBE_AGENT, "prompt": PROBE_PROMPT,
            "trigger": "quota_probe", "requested_by": "system:quota-probe", "initiated_by": "admin"})
    except ValueError:
        log.warning("quota-probe agent is missing or disabled; no fallback probe run")
        return "unavailable"
    return "started"


def lock(state) -> asyncio.Lock:
    """The per-process probe lock. Lazily made rather than built in
    `create_app` so the lock belongs to the loop that first needs it — the SDK
    generator builds an app with no loop at all."""
    lock = getattr(state, "quota_lock", None)
    if lock is None:
        lock = state.quota_lock = asyncio.Lock()
    return lock


def _client(state) -> httpx.AsyncClient:
    """The probe's HTTP client, from the state object so a test can hand in a
    `MockTransport` and the suite never opens a socket."""
    client = getattr(state, "quota_client", None)
    if client is None:
        client = state.quota_client = httpx.AsyncClient()
    return client


async def refresh_claude(state) -> dict:
    """The refresh, which the caller runs UNDER THE LOCK: the cached answer
    when the short-circuit allows it, else one probe and the row it wrote.
    Raises the probe's 503s."""
    st = state
    cached = await _cache_hit(state)
    if cached is not None:
        return {**cached, "probe": None}
    try:
        obs, step = await _probe(state)
    except HTTPException as exc:
        if exc.status_code != 503 or exc.detail != NO_USAGE_HEADERS:
            raise                          # unreachable/unconfigured proxy: not this fallback
        launched = await launch_probe_run(state)
        async with st.session_factory() as s:
            held = quota_store.serialize(await quota_store.latest(s), utcnow())
        # Not waited on: the existing snapshot, marked stale; `probe` says a run
        # is (or recently was) refreshing it.
        return {**held, "stale": True, "probe": f"run_{launched}"}
    async with st.session_factory() as s:
        row = await quota_store.observe(s, st.producer, obs)
        return {**quota_store.serialize(row, utcnow()), "probe": step}


async def _cache_hit(state, provider: str = "claude") -> dict | None:
    """The snapshot, when it is recent enough AND still believable — otherwise
    None, meaning "probe". Both halves are load-bearing: recency alone would
    keep serving a window that has already turned over, and freshness alone
    would let a loop spend a probe per call."""
    st = state
    now = utcnow()
    async with st.session_factory() as s:
        row = await quota_store.latest(s, provider)
        body = quota_store.serialize(row, now)
    if row is None or is_stale(row, now):
        return None
    age = body["age_seconds"]
    window = st.settings.quota_refresh_min_seconds
    return body if age is not None and age < window else None


async def _probe(state) -> tuple[Observation, str]:
    """Two steps through the proxy, stopping at the first response that says
    anything about usage. Raises the route's 503s."""
    st = state
    base = (st.settings.claude_proxy_url or "").rstrip("/")
    if not base:
        raise HTTPException(503, "no claude proxy configured")
    body = {"model": st.settings.quota_probe_model, "messages": PROBE_MESSAGE}
    steps = (("count_tokens", COUNT_TOKENS, body),
             ("message", MESSAGES, {**body, "max_tokens": 1}))
    client = _client(state)
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
    raise HTTPException(503, NO_USAGE_HEADERS)


async def refresh_codex(state) -> dict:
    """Read subscription usage through the Codex credential boundary."""
    st = state
    cached = await _cache_hit(state, "codex")
    if cached is not None:
        return {**cached, "probe": None}
    base = (st.settings.codex_proxy_url or "").rstrip("/")
    if not base:
        raise HTTPException(503, "no codex proxy configured")
    try:
        response = await _client(state).get(
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


async def refresh(state, provider: str) -> dict | None:
    """One provider's serialized snapshot ("claude" or "codex") after a
    coalesced, short-circuited refresh. None when the probe is unavailable
    (any 503: unconfigured or unreachable proxy, no usage headers)."""
    if provider not in ("claude", "codex"):
        raise ValueError(f"unknown provider {provider!r}")
    async with lock(state):
        try:
            if provider == "codex":
                return await refresh_codex(state)
            return await refresh_claude(state)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            log.warning("%s quota refresh unavailable: %s", provider, exc.detail)
            return None
