"""A view-pool credential can scan only its approved source as its viewer."""
import uuid

import pytest
from agentplatform.appdata import credentials as tc
from agentplatform.appdata import quotas
from agentplatform.appdata.access import Caller
from agentplatform.appdata.models import AppDataToolCall
from agentplatform.appdata.records import create_record, load_app

from tests import test_tool_call_credentials as fixture_module

pytest_plugins = ("tests.test_tool_call_credentials",)
EXECUTOR = fixture_module.EXECUTOR
VIEWS_EXECUTOR = fixture_module.VIEWS_EXECUTOR
_app = fixture_module._app
_collection = fixture_module._collection


@pytest.fixture
def scan_env(request):
    return request.getfixturevalue("env")


async def _mint(c, sf, app_id, principal="kyle"):
    keys = await tc.keypair(c.app.state)
    call_id = uuid.uuid4().hex
    token, claim = tc.mint_view_exec(
        keys["private_key"], call_id=call_id, principal=principal, app_id=app_id,
        tool="ledger", action="counts", sources={"results": "results"},
        cnf_sa="ap-tool-executor-views", ttl_seconds=120)
    async with sf() as s:
        await tc.record(s, claim)
        await s.commit()
    return claim, {"Authorization": f"Bearer {VIEWS_EXECUTOR}",
                   "X-AP-Tool-Call": token, "X-AP-Tool-Call-Id": call_id}


async def test_scan_pages_as_viewer_and_records_fields_used(scan_env, sf):
    app_id = await _app(sf, "kyle", [{"collection": "results", "fields": {
        "title": {"type": "string"},
        "private": {"type": "string", "access": {"read": ["kyle"]}},
    }, "access": {"read": ["kyle", "login:qa"], "create": ["kyle"]}}])
    async with sf() as s:
        ctx = await load_app(s, app_id)
        for title in ("one", "two"):
            await create_record(s, ctx, Caller("kyle"), "results",
                                {"title": title, "private": "hidden"})
    claim, headers = await _mint(scan_env, sf, app_id, "login:qa")
    body = {"role": "results", "fields": ["title"], "limit": 1,
            "sort": [{"field": "title", "dir": "asc"}]}
    first = await scan_env.post("/api/app-data/scan", headers=headers, json=body)
    assert first.status_code == 200, first.text
    assert [r["values"]["title"] for r in first.json()["rows"]] == ["one"]
    body["cursor"] = first.json()["next_cursor"]
    second = await scan_env.post("/api/app-data/scan", headers=headers, json=body)
    assert second.status_code == 200, second.text
    assert [r["values"]["title"] for r in second.json()["rows"]] == ["two"]
    assert second.json()["next_cursor"] is None
    async with sf() as s:
        row = await s.get(AppDataToolCall, claim["jti"])
        assert row.scan_fields == ["results.id", "results.title"]
        assert row.scan_rows == 4  # limit + lookahead reserved for each page
    forbidden = await scan_env.post("/api/app-data/scan", headers=headers,
                               json={"role": "results", "fields": ["private"]})
    assert forbidden.status_code == 403
    predicate = await scan_env.post("/api/app-data/scan", headers=headers, json={
        "role": "results", "fields": ["title"],
        "filter": [{"field": "private", "op": "eq", "value": "hidden"}]})
    assert predicate.status_code == 403
    async with sf() as s:
        assert await tc.revoke(s, claim["jti"])
        await s.commit()
    replay = await scan_env.post("/api/app-data/scan", headers=headers, json=body)
    assert replay.status_code == 401


async def test_scan_credential_is_sender_and_route_bound(scan_env, sf):
    app_id = await _app(sf, "kyle", [_collection("results")])
    _, headers = await _mint(scan_env, sf, app_id)
    body = {"role": "results", "fields": ["title"]}
    wrong_sa = await scan_env.post("/api/app-data/scan", headers={**headers,
        "Authorization": f"Bearer {EXECUTOR}"}, json=body)
    assert wrong_sa.status_code == 401
    wrong_role = await scan_env.post("/api/app-data/scan", headers=headers,
                                json={"role": "other", "fields": ["title"]})
    assert wrong_role.status_code == 403
    other_route = await scan_env.post("/api/app-data/agent/records/describe", headers=headers,
                                 json={"app": app_id})
    assert other_route.status_code == 401
    write = await scan_env.post("/api/app-data/agent/records/create", headers=headers,
                                json={"app": app_id, "collection": "results",
                                      "request_id": uuid.uuid4().hex,
                                      "values": {"title": "forbidden"}})
    assert write.status_code == 401
    no_credential = await scan_env.post("/api/app-data/scan", json=body)
    assert no_credential.status_code == 401


async def test_scan_execution_row_ceiling_persists_across_pages(scan_env, sf):
    app_id = await _app(sf, "kyle", [_collection("results")])
    _, headers = await _mint(scan_env, sf, app_id)
    scan_env.app.state.settings.app_data_scan_max_rows = 3
    body = {"role": "results", "fields": ["title"], "limit": 1}
    assert (await scan_env.post("/api/app-data/scan", headers=headers,
                           json=body)).status_code == 200
    again = await scan_env.post("/api/app-data/scan", headers=headers, json=body)
    assert again.status_code == 413
    assert again.json()["detail"]["code"] == "AD-QUOTA-SCAN-EXECUTION"


async def test_scan_hourly_app_budget_fails_closed(scan_env, sf, monkeypatch):
    app_id = await _app(sf, "kyle", [_collection("results")])
    async with sf() as s:
        ctx = await load_app(s, app_id)
        await create_record(s, ctx, Caller("kyle"), "results", {"title": "one"})
    _, headers = await _mint(scan_env, sf, app_id)
    monkeypatch.setattr(quotas, "_settings", lambda: quotas.Settings(
        app_data_app_scan_rows_per_hour=1))
    body = {"role": "results", "fields": ["title"], "limit": 1}
    assert (await scan_env.post("/api/app-data/scan", headers=headers,
                           json=body)).status_code == 200
    again = await scan_env.post("/api/app-data/scan", headers=headers, json=body)
    assert again.status_code == 429
    assert again.json()["detail"]["code"] == "AD-QUOTA-SCAN-ROWS"
