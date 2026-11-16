"""`quota_service.refresh` with no FastAPI app: a SimpleNamespace state and a
MockTransport for the upstream, as in test_quota_api."""
from types import SimpleNamespace

import httpx

from agentplatform import quota_service

from .test_quota_api import PROXY, usage_headers


async def test_refresh_claude_probes_once_then_serves_the_cache(sf, producer):
    calls = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, headers=usage_headers(), json={"input_tokens": 1})

    from agentplatform.config import Settings
    settings = Settings()
    settings.claude_proxy_url = PROXY
    state = SimpleNamespace(
        settings=settings, session_factory=sf, producer=producer,
        quota_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    first = await quota_service.refresh(state, "claude")
    assert first["five_hour"]["utilization"] == 0.22 and first["probe"] == "count_tokens"
    second = await quota_service.refresh(state, "claude")
    assert second["probe"] is None and len(calls) == 1


async def test_refresh_codex_unconfigured_is_none(sf, producer):
    from agentplatform.config import Settings
    settings = Settings()
    settings.codex_proxy_url = ""
    state = SimpleNamespace(settings=settings, session_factory=sf, producer=producer)
    assert await quota_service.refresh(state, "codex") is None


def _state(sf, producer, handler):
    from agentplatform.config import Settings
    settings = Settings()
    settings.claude_proxy_url = PROXY
    return SimpleNamespace(settings=settings, session_factory=sf, producer=producer,
                           quota_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


async def _probe_runs(sf):
    from agentplatform.db import Run
    from sqlalchemy import select
    async with sf() as s:
        return (await s.execute(select(Run).where(Run.agent == "quota-probe"))).scalars().all()


async def test_probe_run_fallback_is_deduped_in_flight_and_per_five_minutes(sf, producer):
    async def bare(request):
        return httpx.Response(429, json={"error": "rate_limit_error"})   # no usage headers
    state = _state(sf, producer, bare)
    first = await quota_service.refresh(state, "claude")
    assert first["probe"] == "run_started" and first["stale"] is True
    assert (await quota_service.refresh(state, "claude"))["probe"] == "run_in_flight"
    from agentplatform.db import Run, RunState
    async with sf() as s:
        (await s.get(Run, (await _probe_runs(sf))[0].id)).state = RunState.SUCCEEDED
        await s.commit()
    assert (await quota_service.refresh(state, "claude"))["probe"] == "run_throttled"
    assert len(await _probe_runs(sf)) == 1


async def test_probe_run_fallback_only_on_a_no_headers_probe(sf, producer):
    async def down(request):
        raise httpx.ConnectError("refused")
    assert await quota_service.refresh(_state(sf, producer, down), "claude") is None

    async def good(request):
        return httpx.Response(200, headers=usage_headers(), json={"input_tokens": 1})
    assert (await quota_service.refresh(_state(sf, producer, good), "claude"))["probe"] == "count_tokens"
    assert await _probe_runs(sf) == []                  # neither an outage nor success starts a run
