from starlette.testclient import TestClient

from agentplatform import api_main
from agentplatform.config import get_settings
from agentplatform.events import FakeProducer


def test_build_app_starts_and_serves(monkeypatch):
    monkeypatch.setenv("AP_DB_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(api_main, "Producer", lambda *_a, **_kw: FakeProducer())
    get_settings.cache_clear()
    try:
        app = api_main.build_app()
        with TestClient(app) as tc:
            resp = tc.get("/api/setup-state")
            assert resp.status_code == 200
    finally:
        get_settings.cache_clear()
