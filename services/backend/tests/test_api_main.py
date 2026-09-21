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


def test_build_app_seeds_the_qa_principal_after_init_db(monkeypatch):
    """The production boot (docs/design/25): once init_db has run, the API —
    the only service holding the secret store — mints the `qa` row and the
    `qa-web-login` secret, and the row logs in through the ordinary route."""
    monkeypatch.setenv("AP_DB_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(api_main, "Producer", lambda *_a, **_kw: FakeProducer())
    get_settings.cache_clear()
    try:
        app = api_main.build_app()
        with TestClient(app) as tc:
            import asyncio
            data = asyncio.run(app.state.secret_store.get("qa-web-login"))
            assert data and data["QA_WEB_USER"] == "qa"
            r = tc.post("/api/login", json={"principal": "qa",
                                            "password": data["QA_WEB_PASSWORD"]})
            assert r.status_code == 200
            who = tc.get("/api/whoami").json()
            assert (who["principal"], who["role"]) == ("qa", "reader")
    finally:
        get_settings.cache_clear()
