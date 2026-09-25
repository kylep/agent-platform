"""Static typed-page publication and owner access, before Tool bindings ship."""
import httpx
from agentplatform.apikeys import hash_token
from agentplatform.db import ApiKey


async def _reader_key(sf, name="reader"):
    token = "ap_static_view_reader"
    async with sf() as session:
        session.add(ApiKey(name=name, role="reader", key_hash=hash_token(token),
                           prefix=token[:10]))
        await session.commit()
    return token


async def test_publish_version_rollback_and_no_active_content(admin_client, token_client, sf):
    assert (await admin_client.post("/api/app-collections", json={
        "name": "sample", "display_name": "Sample"})).status_code == 201
    definition = {"renderer": "typed/v1", "title": "First",
                  "blocks": [{"kind": "paragraph", "text": "Safe text"}]}
    bad = await admin_client.post("/api/live-views", json={
        "app_name": "sample", "slug": "overview",
        "definition": {**definition, "script": "alert(1)"}})
    assert bad.status_code == 422
    created = await admin_client.post("/api/live-views", json={
        "app_name": "sample", "slug": "overview", "definition": definition})
    assert created.status_code == 201
    view_id = created.json()["id"]
    assert (await admin_client.get(f"/api/live-views/{view_id}")).status_code == 404
    assert (await admin_client.post(f"/api/live-views/{view_id}/publish")).json()[
        "published_version"] == 1
    assert (await admin_client.get(f"/api/live-views/{view_id}")).json()[
        "definition"]["title"] == "First"

    second = {**definition, "title": "Second"}
    saved = await admin_client.put(f"/api/live-views/{view_id}/draft", json={
        "expected_revision": 1, "definition": second})
    assert saved.status_code == 200 and saved.json()["draft_revision"] == 2
    assert (await admin_client.put(f"/api/live-views/{view_id}/draft", json={
        "expected_revision": 1, "definition": definition})).status_code == 409
    assert (await admin_client.post(f"/api/live-views/{view_id}/publish")).json()[
        "published_version"] == 2
    assert (await admin_client.post(f"/api/live-views/{view_id}/rollback/1")).json()[
        "published_version"] == 1

    token = await _reader_key(sf, name="someone-else")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await token_client.get("/api/live-views", params={"app_name": "sample"},
                                   headers=headers)).status_code == 404
    assert (await token_client.get(f"/api/live-views/{view_id}",
                                   headers=headers)).status_code == 404
    assert (await token_client.get(f"/api/live-views/{view_id}/draft",
                                   headers=headers)).status_code == 403
    assert (await admin_client.get(f"/api/live-views/{view_id}")).json()[
        "definition"]["title"] == "First"


async def test_duplicate_slug_and_missing_app(admin_client):
    definition = {"title": "Page"}
    assert (await admin_client.post("/api/live-views", json={
        "app_name": "missing", "slug": "home", "definition": definition})).status_code == 404
    await admin_client.post("/api/app-collections", json={
        "name": "pages", "display_name": "Pages"})
    body = {"app_name": "pages", "slug": "home", "definition": definition}
    assert (await admin_client.post("/api/live-views", json=body)).status_code == 201
    assert (await admin_client.post("/api/live-views", json=body)).status_code == 409


async def test_running_read_uses_published_binding_and_owner_acl(
        admin_client, token_client, sf, monkeypatch):
    from agentplatform.api import live_views as views_api

    await admin_client.post("/api/app-collections", json={
        "name": "running", "display_name": "Running"})
    definition = {"title": "My running", "reads": [
        {"alias": "summary", "operation": "running.summary.read@1"}],
        "blocks": [{"kind": "metric", "label": "Distance", "source": "summary",
                    "field": "total_km"}]}
    bad = await admin_client.post("/api/live-views", json={
        "app_name": "running", "slug": "bad", "definition": {
            **definition, "blocks": [{"kind": "metric", "source": "missing",
                                      "field": "total_km"}]}})
    assert bad.status_code == 422
    created = await admin_client.post("/api/live-views", json={
        "app_name": "running", "slug": "overview", "definition": definition})
    view_id = created.json()["id"]
    await admin_client.post(f"/api/live-views/{view_id}/publish")
    calls = []

    async def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"totals": {
            "total_km": 42.5, "runs": 8, "activities": 10},
            "latest_day": "2026-09-24"})

    real = views_api.httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("base_url", None)
        return real(transport=httpx.MockTransport(upstream), base_url="http://running", **kwargs)

    monkeypatch.setattr(views_api.httpx, "AsyncClient", fake_client)
    reader_token = await _reader_key(sf, name="someone-else")
    denied = await token_client.get(f"/api/live-views/{view_id}/data/summary",
                                    headers={"Authorization": f"Bearer {reader_token}"})
    assert denied.status_code == 404 and calls == []
    got = await admin_client.get(f"/api/live-views/{view_id}/data/summary")
    assert got.status_code == 200
    assert got.json() == {"total_km": 42.5, "runs": 8, "activities": 10,
                          "latest_day": "2026-09-24"}
    assert got.headers["cache-control"] == "private, no-store"
    assert str(calls[0].url) == "http://running/apps/running/api/summary"
