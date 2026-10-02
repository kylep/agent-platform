"""Published tool views run only in the views pool with viewer-scoped scans."""
from pathlib import Path

import httpx
import pytest
from agentplatform.appdata import toolviews
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import AppDataDefinition, AppDataToolCall
from agentplatform.appdata.records import create_record, load_app
from agentplatform.appdata.views import run_view
from agentplatform.toolregistry import ToolRegistry

from tests import test_tool_call_credentials as fixture_module

pytest_plugins = ("tests.test_tool_call_credentials",)
_app = fixture_module._app
VIEWS_EXECUTOR = fixture_module.VIEWS_EXECUTOR


@pytest.fixture
def scan_env(request):
    return request.getfixturevalue("env")


async def _published(sf, env, *, field="title"):
    body = {"collection": "results", "fields": {
        "title": {"type": "string"},
        "private": {"type": "string", "access": {"read": ["kyle"]}},
    }, "access": {"read": ["kyle", "login:qa"], "create": ["kyle"]}}
    app_id = await _app(sf, "kyle", [body], tools=[{
        "tool": "app_summary", "roles": {
            "source": {"collection": "results", "verbs": ["read"]}}}])
    async with sf() as s:
        s.add(AppDataDefinition(app_id=app_id, kind="view", name="summary", version=1,
                                body={"view": "summary", "tool": "app_summary",
                                      "action": "counts", "sources": ["source"],
                                      "params": {"field": {"type": "string",
                                                          "default": field}}},
                                state="published", author="kyle"))
        await s.commit()
        ctx = await load_app(s, app_id)
        await create_record(s, ctx, Caller("kyle"), "results",
                            {"title": "blue", "private": "hidden"})
    env.app.state.tool_registry = ToolRegistry(Path(__file__).resolve().parents[3] / "tools")
    return app_id


def _fake_pool(monkeypatch, env, *, output=None):
    calls = []
    real_client = httpx.AsyncClient

    class Pool:
        def __init__(self, *, base_url, timeout):
            calls.append((base_url, timeout))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, path, *, json):
            assert path == "/run" and json["tool"] == "app_summary"
            token = json["credential"]["token"]
            call_id = json["credential"]["call_id"]
            async with real_client(transport=httpx.ASGITransport(app=env.app),
                                   base_url="http://t") as client:
                scan = await client.post("/api/app-data/scan", headers={
                    "Authorization": f"Bearer {VIEWS_EXECUTOR}",
                    "X-AP-Tool-Call": token, "X-AP-Tool-Call-Id": call_id},
                    json={"role": "source", "fields": [json["args"]["field"]]})
            calls.append(scan.status_code)
            if scan.status_code != 200:
                return httpx.Response(200, json={"ok": False, "error": "scan refused"})
            return httpx.Response(200, json={"ok": True, "output": output if output is not None
                else '{"rows":[{"value":"blue","count":1}]}'})

    monkeypatch.setattr(toolviews.httpx, "AsyncClient", Pool)
    return calls


async def test_tool_view_scans_as_viewer_and_revokes_credential(scan_env, sf, monkeypatch):
    app_id = await _published(sf, scan_env)
    calls = _fake_pool(monkeypatch, scan_env)
    async with sf() as s:
        ctx = await load_app(s, app_id)
        out = await run_view(s, ctx, Caller("login:qa"), "summary",
                             app_state=scan_env.app.state)
    assert out["rows"] == [{"values": {"value": "blue", "count": 1}, "restricted": []}]
    assert out["next_cursor"] is None and out["stale"] is False
    assert calls == [("http://agent-platform-tool-executor-views:8000", 65), 200]
    async with sf() as s:
        from sqlalchemy import select
        row = (await s.execute(select(AppDataToolCall).where(
            AppDataToolCall.kind == "view_exec"))).scalar_one()
        assert row.revoked_at is not None and row.scan_fields == ["created_at", "id", "title"]


async def test_tool_view_cannot_infer_a_hidden_field(scan_env, sf, monkeypatch):
    app_id = await _published(sf, scan_env, field="private")
    calls = _fake_pool(monkeypatch, scan_env)
    async with sf() as s:
        ctx = await load_app(s, app_id)
        with pytest.raises(RecordError) as exc:
            await run_view(s, ctx, Caller("login:qa"), "summary",
                           app_state=scan_env.app.state)
    assert exc.value.code == "AD-TOOL-VIEW-FAILED"
    assert calls[-1] == 403


@pytest.mark.parametrize("output,code", [
    ('{"rows":[{"value":"blue","count":0}]}', "AD-TOOL-VIEW-INVALID"),
    ('{"rows":[]}' + " " * 9000, "AD-TOOL-VIEW-SIZE"),
])
async def test_tool_view_refuses_invalid_or_oversize_output(scan_env, sf, monkeypatch,
                                                            output, code):
    app_id = await _published(sf, scan_env)
    _fake_pool(monkeypatch, scan_env, output=output)
    async with sf() as s:
        ctx = await load_app(s, app_id)
        with pytest.raises(RecordError) as exc:
            await run_view(s, ctx, Caller("kyle"), "summary",
                           app_state=scan_env.app.state)
    assert exc.value.code == code
