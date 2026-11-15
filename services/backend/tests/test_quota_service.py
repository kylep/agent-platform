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
