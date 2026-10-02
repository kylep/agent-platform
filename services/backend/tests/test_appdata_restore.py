"""A restored App keeps its definitions but disables unavailable tool bindings."""
from datetime import datetime, timezone

import pytest
from agentplatform import operation_catalog
from agentplatform.appdata import restore, toolviews
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import AppDataDefinition
from agentplatform.appdata.records import load_app
from agentplatform.appdata.views import run_view
from agentplatform.db import ScheduledTask
from sqlalchemy import select

from tests import test_tool_call_credentials as fixture_module

pytest_plugins = ("tests.test_tool_call_credentials",)
_app = fixture_module._app
_collection = fixture_module._collection


@pytest.fixture
def scan_env(request):
    return request.getfixturevalue("env")


async def test_restore_reports_missing_tool_and_action_without_rewriting(scan_env, sf,
                                                                          monkeypatch):
    app_id = await _app(sf, "kyle", [_collection("results")], tools=[{
        "tool": "app_summary", "roles": {
            "source": {"collection": "results", "verbs": ["read"]}}}])
    async with sf() as s:
        s.add(AppDataDefinition(app_id=app_id, kind="view", name="summary", version=1,
                                body={"view": "summary", "tool": "app_summary",
                                      "action": "counts", "sources": ["source"],
                                      "params": {"field": {"type": "string"}}},
                                state="published", author="kyle"))
        task = ScheduledTask(title="later", agent="pai", prompt="x", runtime="codex",
                             model="gpt-6-sol", run_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                             expires_at=datetime(2027, 1, 1, tzinfo=timezone.utc),
                             creator="kyle", run_id="r" * 32)
        s.add(task)
        await s.commit()
        original = [(row.name, row.body) for row in (await s.execute(
            select(AppDataDefinition).where(
                AppDataDefinition.app_id == app_id))).scalars().all()]
        first = await restore.report(s, scan_env.app.state.tool_registry,
                                     now=datetime(2026, 10, 2, tzinfo=timezone.utc))
        second = await restore.report(s, scan_env.app.state.tool_registry,
                                      now=datetime(2026, 10, 2, tzinfo=timezone.utc))
        assert first == second
        assert first["disabled_count"] == 2
        assert first["task_watermarks"] == [{"agent": "pai", "last_fired_at": None,
                                              "overdue_scheduled": 1}]
        assert original == [(row.name, row.body) for row in (await s.execute(
            select(AppDataDefinition).where(
                AppDataDefinition.app_id == app_id))).scalars().all()]

    # The catalog can also disappear across restore. Ordinary App loading
    # stays available; only executing that particular view answers 503.
    monkeypatch.setattr(operation_catalog, "view_action", lambda *_: None)
    monkeypatch.setattr(toolviews, "view_action", lambda *_: None)
    async with sf() as s:
        ctx = await load_app(s, app_id)
        assert "results" in ctx.bundle.collections
        with pytest.raises(RecordError) as exc:
            await run_view(s, ctx, Caller("kyle"), "summary",
                           app_state=scan_env.app.state)
        assert exc.value.status == 503
