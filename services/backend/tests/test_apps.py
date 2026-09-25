"""Apps: the app.yaml contract, the provisioner, the registry API
(docs/design/11)."""
import httpx
import pytest
from sqlalchemy import select

from agentplatform.apikeys import hash_token
from agentplatform.appprovisioner import AppProvisioner, pg_ident
from agentplatform.appregistry import AppRegistry
from agentplatform.app_collections import import_legacy_apps
from agentplatform.db import ApiKey, AppCollection
from agentplatform.secrets import InMemorySecretStore


def _app(tmp_path, name, yaml_text):
    d = tmp_path / name
    d.mkdir()
    (d / "app.yaml").write_text(yaml_text)
    return d


# --- registry ----------------------------------------------------------------

def test_registry_parses_and_validates(tmp_path):
    _app(tmp_path, "news", (
        "display_name: Newsroom\ndescription: Browse news.\nicon: X\nui: true\napi: true\n"
        "needs:\n  postgres: true\n  kafka_topics: [app.news.item.ingested]\n"
        "agent_key:\n  role: operator\n"))
    _app(tmp_path, "badtopic", "needs:\n  kafka_topics: [news.item]\n")
    _app(tmp_path, "badrole", "agent_key:\n  role: admin\n")
    reg = AppRegistry(tmp_path)
    news = reg.get("news")
    assert news.spec.ui and news.spec.needs.postgres
    assert news.spec.display_name == "Newsroom"
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


async def test_legacy_app_import_keeps_db_metadata(tmp_path, sf):
    _app(tmp_path, "running", "display_name: Running\ndescription: Old text\nui: true\n")
    registry = AppRegistry(tmp_path)
    await import_legacy_apps(sf, registry)
    async with sf() as session:
        row = await session.get(AppCollection, "running")
        assert row.display_name == "Running" and row.source_app == "running"
        row.description = "Edited in the platform"
        await session.commit()
    await import_legacy_apps(sf, registry)
    async with sf() as session:
        row = await session.get(AppCollection, "running")
        assert row.description == "Edited in the platform"


async def test_db_app_collections_can_be_created_and_edited(admin_client, client):
    created = await admin_client.post("/api/app-collections", json={
        "name": "my-app", "display_name": "My App", "description": "First pass"})
    assert created.status_code == 201
    assert (await admin_client.post("/api/app-collections", json={
        "name": "my-app", "display_name": "Again"})).status_code == 409
    assert (await admin_client.post("/api/app-collections", json={
        "name": "Bad App", "display_name": "Bad"})).status_code == 422
    edited = await admin_client.patch("/api/app-collections/my-app", json={"description": "Second pass"})
    assert edited.status_code == 200
    apps = (await admin_client.get("/api/apps")).json()
    app = next(app for app in apps if app["name"] == "my-app")
    assert {k: app[k] for k in ("name", "display_name", "description", "source_app")} == {
        "name": "my-app", "display_name": "My App",
        "description": "Second pass", "source_app": None}
    assert (await client.patch("/api/app-collections/missing", json={"description": "x"})).status_code == 404


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


async def test_query_app_forwards_params(admin_client, monkeypatch):
    """The proxy accepts the app endpoint's query two ways: loose query params
    (what the broker sends) and a JSON `params` object (what OpenAPI-derived
    clients like the external facade can express). Both reach the app."""
    import json as _json
    from agentplatform.api import apps as apps_mod
    captured = []

    async def upstream(request):
        captured.append(str(request.url))
        return httpx.Response(200, json=[])

    real = apps_mod.httpx.AsyncClient

    def fake_client(**kw):
        kw.pop("base_url", None)
        return real(transport=httpx.MockTransport(upstream), base_url="http://up", **kw)

    monkeypatch.setattr(apps_mod.httpx, "AsyncClient", fake_client)
    r = await admin_client.get("/api/apps/news/query/items",
                               params={"params": _json.dumps({"q": "postgres", "limit": 5})})
    assert r.status_code == 200
    assert captured[-1] == "http://up/apps/news/api/items?q=postgres&limit=5"
    r = await admin_client.get("/api/apps/news/query/items", params={"topic": "security"})
    assert r.status_code == 200
    assert captured[-1] == "http://up/apps/news/api/items?topic=security"
    r = await admin_client.get("/api/apps/news/query/items", params={"params": "[1,2]"})
    assert r.status_code == 400
    r = await admin_client.get("/api/apps/news/query/items", params={"params": "{nope"})
    assert r.status_code == 400
