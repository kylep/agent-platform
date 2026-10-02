"""Materialized tool views refresh under a system read and authorize at read time."""
from datetime import timedelta
from pathlib import Path

import pytest
from agentplatform import maintenance_mode
from agentplatform.appdata import materialized
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import AppDataApp, AppDataDefinition
from agentplatform.appdata.records import create_record, load_app
from agentplatform.appdata.views import run_view
from agentplatform.db import utcnow
from agentplatform.toolregistry import ToolRegistry
from sqlalchemy import select

from tests import test_appdata_toolviews as toolview_tests
from tests import test_tool_call_credentials as fixture_module

pytest_plugins = ("tests.test_tool_call_credentials",)
_app = fixture_module._app


@pytest.fixture
def scan_env(request):
    return request.getfixturevalue("env")


async def _setup(sf, env, *, domain_values=("private",)):
    collection = {"collection": "results", "fields": {
        "field": {"type": "string"}, "title": {"type": "string"},
        "private": {"type": "string", "access": {"read": ["kyle"]}},
    }, "indexed": ["field"],
        "access": {"read": ["kyle", "login:qa"], "create": ["kyle", "agent:pai"]}}
    app_id = await _app(sf, "kyle", [collection], tools=[{
        "tool": "app_summary", "roles": {
            "source": {"collection": "results", "verbs": ["read"]}}},
        {"tool": "ledger", "roles": {
            "results": {"collection": "results", "verbs": ["read", "create"]}}}])
    async with sf() as s:
        s.add(AppDataDefinition(app_id=app_id, kind="view", name="summary", version=1,
                                body={"view": "summary", "tool": "app_summary",
                                      "action": "counts", "sources": ["source"],
                                      "params": {"field": {"type": "string", "required": True}},
                                      "materialize": {"every": "5m", "domain": "field"}},
                                state="published", author="kyle"))
        await s.commit()
        ctx = await load_app(s, app_id)
        for value in domain_values:
            await create_record(s, ctx, Caller("kyle"), "results", {
                "field": value, "title": "blue", "private": "hidden"})
    env.app.state.tool_registry = ToolRegistry(Path(__file__).resolve().parents[3] / "tools")
    return app_id


async def test_refresh_domain_reader_intersection_and_grant_without_refresh(scan_env, sf,
                                                                             monkeypatch):
    app_id = await _setup(sf, scan_env)
    calls = toolview_tests._fake_pool(monkeypatch, scan_env)
    assert await materialized.refresh_due(sf, scan_env.app.state) == 1
    assert calls[-1] == 200
    async with sf() as s:
        ctx = await load_app(s, app_id)
        kyle = await run_view(s, ctx, Caller("kyle"), "summary",
                              {"field": "private"}, app_state=scan_env.app.state)
        assert kyle["rows"][0]["values"]["count"] == 1
        with pytest.raises(RecordError) as denied:
            await run_view(s, ctx, Caller("login:qa"), "summary",
                           {"field": "private"}, app_state=scan_env.app.state)
        assert denied.value.status == 403
    # Publish a wider field grant. A read rechecks current facts; no refresh.
    async with sf() as s:
        app = await s.get(AppDataApp, app_id)
        old = (await s.execute(select(AppDataDefinition).where(
            AppDataDefinition.app_id == app_id, AppDataDefinition.kind == "collection",
            AppDataDefinition.name == "results"))).scalar_one()
        wider = dict(old.body)
        wider["fields"] = dict(wider["fields"])
        wider["fields"]["private"] = {"type": "string", "access": {
            "read": ["kyle", "login:qa"]}}
        s.add(AppDataDefinition(app_id=app_id, kind="collection", name="results",
                                version=2, body=wider, state="published", author="kyle"))
        app.approved_version = 2
        app.authority_generation += 1
        await s.commit()
        ctx = await load_app(s, app_id)
        out = await run_view(s, ctx, Caller("login:qa"), "summary",
                             {"field": "private"}, app_state=scan_env.app.state)
        assert out["as_of"] == kyle["as_of"]
    assert len(calls) == 2


async def test_refresh_requests_coalesce_and_maintenance_pauses(scan_env, sf, monkeypatch):
    app_id = await _setup(sf, scan_env, domain_values=("title", "private"))
    calls = toolview_tests._fake_pool(monkeypatch, scan_env)
    async with sf() as s:
        ctx = await load_app(s, app_id)
        view = ctx.bundle.views["summary"]
        now = utcnow()
        assert await materialized.request_refresh(s, ctx, view, now=now)
        assert not await materialized.request_refresh(s, ctx, view, now=now + timedelta(seconds=30))
        assert await materialized.request_refresh(s, ctx, view, now=now + timedelta(seconds=61))
    assert await materialized.refresh_due(sf, scan_env.app.state, now=now + timedelta(seconds=62)) == 2
    assert len(calls) == 4
    async with sf() as s:
        await maintenance_mode.enter_restore(s, "test")
        await s.commit()
    assert await materialized.refresh_due(sf, scan_env.app.state, now=now + timedelta(hours=1)) == 0
    assert len(calls) == 4


async def test_batch_writer_credential_can_request_refresh(scan_env, sf):
    app_id = await _setup(sf, scan_env, domain_values=("title",))
    scan_env.app.state.tool_registry = ToolRegistry(scan_env.app.state.settings.tools_root)
    minted = await fixture_module._mint(scan_env, tool="ledger")
    assert minted.status_code == 200, minted.text
    headers = fixture_module._as_executor(minted.json())
    body = {"app": app_id, "view": "summary"}
    first = await scan_env.post("/api/app-data/agent/records/request_refresh",
                                headers=headers, json=body)
    assert first.status_code == 200 and first.json()["queued"] is True
    second = await scan_env.post("/api/app-data/agent/records/request_refresh",
                                 headers=headers, json=body)
    assert second.status_code == 200 and second.json()["queued"] is False


async def test_new_domain_value_gets_its_own_result(scan_env, sf, monkeypatch):
    app_id = await _setup(sf, scan_env, domain_values=("title",))
    calls = toolview_tests._fake_pool(monkeypatch, scan_env)
    now = utcnow()
    assert await materialized.refresh_due(sf, scan_env.app.state, now=now) == 1
    async with sf() as s:
        ctx = await load_app(s, app_id)
        await create_record(s, ctx, Caller("kyle"), "results", {
            "field": "private", "title": "red", "private": "secret"})
    assert await materialized.refresh_due(sf, scan_env.app.state,
                                          now=now + timedelta(seconds=30)) == 1
    assert len(calls) == 4
