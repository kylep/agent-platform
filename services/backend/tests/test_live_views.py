"""Versioned typed pages, ACLs, and durable write intents."""
import httpx
import pytest
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
    versions = (await admin_client.get(f"/api/live-views/{view_id}/versions")).json()
    assert [(item["version"], item["current"]) for item in versions] == [
        (2, False), (1, True)]

    token = await _reader_key(sf, name="someone-else")
    headers = {"Authorization": f"Bearer {token}"}
    listed_apps = await token_client.get("/api/apps", headers=headers)
    assert "sample" not in [app["name"] for app in listed_apps.json()]
    assert (await token_client.get("/api/live-views", params={"app_name": "sample"},
                                   headers=headers)).status_code == 404
    assert (await token_client.get(f"/api/live-views/{view_id}",
                                   headers=headers)).status_code == 404
    assert (await token_client.get(f"/api/live-views/{view_id}/draft",
                                   headers=headers)).status_code == 403
    assert (await token_client.get(f"/api/live-views/{view_id}/versions",
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


async def test_domain_link_stays_inside_its_app(admin_client):
    await admin_client.post("/api/app-collections", json={
        "name": "ttrpg", "display_name": "Tabletop RPG"})
    base = {"app_name": "ttrpg", "slug": "table"}
    for href in ("https://example.com", "javascript:alert(1)", "/apps/news/"):
        denied = await admin_client.post("/api/live-views", json={**base,
            "definition": {"title": "Table", "blocks": [
                {"kind": "link", "label": "Play", "href": href}]}})
        assert denied.status_code == 422
    accepted = await admin_client.post("/api/live-views", json={**base,
        "definition": {"title": "Table", "blocks": [
            {"kind": "link", "label": "Play", "href": "/apps/ttrpg/"}]}})
    assert accepted.status_code == 201


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


@pytest.mark.parametrize(("app_name", "operation", "field", "upstream_data", "expected"), [
    ("news", "news.summary.read@1", "today",
     {"today": 3, "week": 12, "total": 80, "topics": 4,
      "latest_day": "2026-09-24", "private_extra": "discard"}, 3),
    ("stockmarket", "stockmarket.summary.read@1", "watchlist",
     {"indexes": [{"symbol": "SPY"}], "watchlist": [{"symbol": "QQQ"}],
      "latest_day": "2026-09-24", "latest_brief_day": None}, 1),
    ("tcms", "tcms.overview.read@1", "failing",
     {"attention": {"failing": 2, "flaky": 1, "unlinked": 0,
                    "prune_candidates": 3}, "coverage": {"pct": 81.5},
      "latest_run": {"secret_extra": "discard"}}, 2),
])
async def test_domain_summary_reads_are_scoped_and_normalized(
        admin_client, monkeypatch, app_name, operation, field, upstream_data, expected):
    from agentplatform.api import live_views as views_api

    await admin_client.post("/api/app-collections", json={
        "name": app_name, "display_name": app_name.title()})
    definition = {"title": app_name.title(), "reads": [
        {"alias": "summary", "operation": operation}], "blocks": [
        {"kind": "metric", "label": field, "source": "summary", "field": field}]}
    wrong = await admin_client.post("/api/live-views", json={
        "app_name": app_name, "slug": "wrong", "definition": {
            **definition, "reads": [{"alias": "summary", "operation": "running.summary.read@1"}]}})
    assert wrong.status_code == 422
    created = await admin_client.post("/api/live-views", json={
        "app_name": app_name, "slug": "overview", "definition": definition})
    assert created.status_code == 201, created.text
    view_id = created.json()["id"]
    await admin_client.post(f"/api/live-views/{view_id}/publish")
    urls = []

    async def upstream(request):
        urls.append(str(request.url))
        return httpx.Response(200, json=upstream_data)

    real = views_api.httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("base_url", None)
        return real(transport=httpx.MockTransport(upstream), base_url="http://app", **kwargs)

    monkeypatch.setattr(views_api.httpx, "AsyncClient", fake_client)
    got = await admin_client.get(f"/api/live-views/{view_id}/data/summary")
    assert got.status_code == 200, got.text
    assert got.json()[field] == expected
    assert "private_extra" not in got.json() and "latest_run" not in got.json()
    endpoint = "overview" if app_name == "tcms" else "summary"
    assert urls == [f"http://app/apps/{app_name}/api/{endpoint}"]


async def _ticket_page(admin_client):
    await admin_client.post("/api/app-collections", json={
        "name": "running", "display_name": "Running"})
    definition = {"title": "Running", "actions": [
        {"alias": "feedback", "operation": "tickets.create@1", "channel": "general"}],
        "blocks": [{"kind": "action", "label": "Send feedback",
                    "action_alias": "feedback"}]}
    created = await admin_client.post("/api/live-views", json={
        "app_name": "running", "slug": "feedback", "definition": definition})
    assert created.status_code == 201, created.text
    view_id = created.json()["id"]
    assert (await admin_client.post(f"/api/live-views/{view_id}/publish")).status_code == 200
    return view_id


async def test_ticket_action_needs_grant_and_replays_one_receipt(admin_client, sf):
    from agentplatform.db import Ticket
    from sqlalchemy import select

    view_id = await _ticket_page(admin_client)
    intent_path = f"/api/live-views/{view_id}/intents"
    request = {"alias": "feedback", "arguments": {"title": "Improve splits",
               "body": "The chart should show a weekly total."}}
    denied = await admin_client.post(intent_path, json=request)
    assert denied.status_code == 403
    grant = await admin_client.post("/api/live-operation-grants", json={
        "app_name": "running", "principal_id": "admin",
        "operation": "tickets.create@1"})
    assert grant.status_code == 200, grant.text
    intent = await admin_client.post(intent_path, json=request)
    assert intent.status_code == 201, intent.text
    assert intent.json()["target"] == "#general"
    call = {"intent_id": intent.json()["intent_id"],
            "idempotency_key": "test_unique_ticket_01"}
    first = await admin_client.post(f"/api/live-views/{view_id}/calls", json=call)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "succeeded"
    assert first.json()["result"]["ticket_key"] == "GEN-1"
    replay = await admin_client.post(f"/api/live-views/{view_id}/calls", json=call)
    assert replay.json()["id"] == first.json()["id"]
    assert (await admin_client.post(f"/api/live-views/{view_id}/calls", json={
        **call, "idempotency_key": "test_unique_ticket_02"})).status_code == 409
    async with sf() as session:
        assert len((await session.execute(select(Ticket))).scalars().all()) == 1
    receipt = await admin_client.get(f"/api/live-invocations/{first.json()['id']}")
    assert receipt.json()["status"] == "succeeded"


async def test_ticket_action_revocation_before_call_denies_dispatch(admin_client, sf):
    from agentplatform.db import Ticket
    from sqlalchemy import select

    view_id = await _ticket_page(admin_client)
    grant = {"app_name": "running", "principal_id": "admin",
             "operation": "tickets.create@1"}
    await admin_client.post("/api/live-operation-grants", json=grant)
    intent = await admin_client.post(f"/api/live-views/{view_id}/intents", json={
        "alias": "feedback", "arguments": {"title": "Check pace"}})
    assert intent.status_code == 201
    assert (await admin_client.post("/api/live-operation-grants/revoke",
                                    json=grant)).status_code == 200
    called = await admin_client.post(f"/api/live-views/{view_id}/calls", json={
        "intent_id": intent.json()["intent_id"],
        "idempotency_key": "test_unique_ticket_03"})
    assert called.status_code == 200
    assert called.json()["status"] == "denied_at_dispatch"
    async with sf() as session:
        assert (await session.execute(select(Ticket))).scalars().all() == []


async def test_api_key_cannot_use_browser_action(admin_client, token_client, sf):
    view_id = await _ticket_page(admin_client)
    token = "ap_live_view_admin_key"
    async with sf() as session:
        session.add(ApiKey(name="admin", role="admin", key_hash=hash_token(token),
                           prefix=token[:10]))
        await session.commit()
    await admin_client.post("/api/live-operation-grants", json={
        "app_name": "running", "principal_id": "admin",
        "operation": "tickets.create@1"})
    response = await token_client.post(f"/api/live-views/{view_id}/intents",
                                       headers={"Authorization": f"Bearer {token}"},
                                       json={"alias": "feedback",
                                             "arguments": {"title": "Should not create"}})
    assert response.status_code == 403


async def test_private_snapshot_resource_and_revocation(
        admin_client, token_client, sf, monkeypatch):
    from agentplatform.api import live_views as views_api

    await admin_client.post("/api/app-collections", json={
        "name": "running", "display_name": "Running"})
    created = await admin_client.post("/api/live-views", json={
        "app_name": "running", "slug": "summary", "definition": {
            "title": "Running summary", "reads": [{"alias": "summary",
                "operation": "running.summary.read@1"}],
            "blocks": [{"kind": "metric", "label": "Distance",
                        "source": "summary", "field": "total_km"}]}})
    view_id = created.json()["id"]
    await admin_client.post(f"/api/live-views/{view_id}/publish")

    async def upstream(_request):
        return httpx.Response(200, json={"totals": {
            "total_km": 12.5, "runs": 3, "activities": 3},
            "latest_day": "2026-09-24"})

    real = views_api.httpx.AsyncClient

    def fake_client(**kwargs):
        kwargs.pop("base_url", None)
        return real(transport=httpx.MockTransport(upstream), base_url="http://running", **kwargs)

    monkeypatch.setattr(views_api.httpx, "AsyncClient", fake_client)
    captured = await admin_client.post(f"/api/live-views/{view_id}/snapshots",
                                       json={"alias": "summary"})
    assert captured.status_code == 201, captured.text
    snap_id = captured.json()["id"]
    assert captured.json()["resource_uri"] == f"ap://snapshot/{snap_id}"
    assert captured.headers["cache-control"] == "private, no-store"
    token = await _reader_key(sf, name="other-reader")
    headers = {"Authorization": f"Bearer {token}"}
    assert (await token_client.get(f"/api/live-snapshots/{snap_id}",
                                   headers=headers)).status_code == 404
    assert (await token_client.get(f"/api/live-snapshots/{snap_id}/resource",
                                   headers=headers)).status_code == 404
    resource = await admin_client.get(f"/api/live-snapshots/{snap_id}/resource")
    assert resource.status_code == 200 and '"total_km": 12.5' in resource.text
    assert resource.headers["cache-control"] == "private, no-store"
    assert (await admin_client.delete(f"/api/live-snapshots/{snap_id}")).status_code == 200
    assert (await admin_client.get(f"/api/live-snapshots/{snap_id}/resource")).status_code == 404


async def test_private_snapshot_bytes_are_pruned_after_delete(sf):
    from datetime import timedelta

    from agentplatform.db import LiveSnapshot, utcnow
    from agentplatform.pruning import LiveDataPruner

    async with sf() as session:
        row = LiveSnapshot(view_id="v", view_version=1, owner_id="admin",
                           title="Sensitive summary", content={"distance": 4},
                           expires_at=utcnow() + timedelta(days=30),
                           deleted_at=utcnow())
        session.add(row)
        await session.commit()
        row_id = row.id
    assert await LiveDataPruner(sf).prune_once() == 1
    async with sf() as session:
        assert await session.get(LiveSnapshot, row_id) is None
