"""Apps: the app.yaml contract, the provisioner, the registry API
(docs/design/11)."""
import pytest
from sqlalchemy import select

from agentplatform.apikeys import hash_token
from agentplatform.appprovisioner import AppProvisioner, pg_ident
from agentplatform.appregistry import AppRegistry
from agentplatform.db import ApiKey
from agentplatform.secrets import InMemorySecretStore


def _app(tmp_path, name, yaml_text):
    d = tmp_path / name
    d.mkdir()
    (d / "app.yaml").write_text(yaml_text)
    return d


# --- registry ----------------------------------------------------------------

def test_registry_parses_and_validates(tmp_path):
    _app(tmp_path, "news", (
        "description: Browse news.\nicon: X\nui: true\napi: true\n"
        "needs:\n  postgres: true\n  kafka_topics: [app.news.item.ingested]\n"
        "agent_key:\n  role: operator\n"))
    _app(tmp_path, "badtopic", "needs:\n  kafka_topics: [news.item]\n")
    _app(tmp_path, "badrole", "agent_key:\n  role: admin\n")
    reg = AppRegistry(tmp_path)
    news = reg.get("news")
    assert news.spec.ui and news.spec.needs.postgres
    assert news.spec.needs.kafka_topics == ["app.news.item.ingested"]
    assert news.spec.agent_key.role == "operator"
    assert "namespaced app.badtopic" in reg.get("badtopic").error
    # admin keys for apps are refused at parse time — least privilege
    assert "reader, annotator, or operator" in reg.get("badrole").error


def test_pg_ident_rules():
    assert pg_ident("news") == "app_news"
    assert pg_ident("my-app2") == "app_my_app2"
    with pytest.raises(ValueError):
        pg_ident("Bad App")
    with pytest.raises(ValueError):
        pg_ident("2start")


# --- provisioner (key minting; postgres needs a real pg, covered live) --------

@pytest.fixture
def provisioner(tmp_path, sf):
    from agentplatform.config import Settings
    _app(tmp_path, "news", "agent_key:\n  role: operator\n")
    store = InMemorySecretStore()
    p = AppProvisioner(AppRegistry(tmp_path), None, sf, store, Settings())
    return p, store


async def test_key_minted_once_and_single_owner(provisioner, sf):
    p, store = provisioner
    r1 = await p.provision_once()
    assert r1["news"] == ["key app:news (operator)"]
    sec = await store.get("app-news-key")
    assert sec["AP_API_TOKEN"].startswith("ap_")
    # convergence: second pass is a no-op
    assert (await p.provision_once())["news"] == []
    async with sf() as s:
        rows = (await s.execute(select(ApiKey).where(ApiKey.name == "app:news"))).scalars().all()
    assert len(rows) == 1 and rows[0].role == "operator"
    assert rows[0].key_hash == hash_token(sec["AP_API_TOKEN"])


async def test_key_reminted_when_secret_lost(provisioner, sf):
    p, store = provisioner
    await p.provision_once()
    store._d.pop("app-news-key")           # secret deleted out-of-band
    r = await p.provision_once()
    assert r["news"] == ["key app:news (operator)"]
    async with sf() as s:
        rows = (await s.execute(select(ApiKey).where(ApiKey.name == "app:news"))).scalars().all()
    active = [k for k in rows if k.revoked_at is None]
    assert len(rows) == 2 and len(active) == 1   # predecessor revoked


# --- API ---------------------------------------------------------------------

async def test_apps_endpoint_lists_declared(admin_client):
    r = await admin_client.get("/api/apps")
    assert r.status_code == 200
    assert isinstance(r.json(), list)   # repo has no apps yet — empty is legal


async def test_auth_check_gates(client):
    assert (await client.get("/api/auth-check")).status_code == 401
    await client.post("/api/setup", json={"password": "pw12345678"})
    await client.post("/api/login", json={"password": "pw12345678"})
    r = await client.get("/api/auth-check")
    assert r.status_code == 204
    assert r.headers["x-ap-user"] == "admin" and r.headers["x-ap-role"] == "admin"


async def test_query_app_rejects_traversal(admin_client):
    for bad in ("..%2Fsecrets", "..", "a/../../b", "a\\b"):
        r = await admin_client.get(f"/api/apps/news/query/{bad}")
        assert r.status_code in (400, 404), bad
    # (httpx normalizes `a/../b` client-side to `b` before the server sees it,
    # so that shape can't be exercised here; the raw-`..` cases above cover the
    # server-side gate. Direct unit check of the validation predicate:)
    from agentplatform.api.apps import _path_ok
    assert not _path_ok("a/../b") and not _path_ok("/abs") and not _path_ok("a\x00b")
    assert _path_ok("items") and _path_ok("calendar")
