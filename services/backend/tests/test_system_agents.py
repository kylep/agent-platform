"""Code ownership cannot overwrite operating choices or become an editor escape."""
from sqlalchemy import func, select

from agentplatform.db import AgentDef, AgentVersion
from agentplatform.system_agents import (SYSTEM_AGENT_NAMES, definitions,
                                         managed_field_changes, reconcile_system_agents)


async def _reconcile(sf):
    async with sf() as session:
        connection = await session.connection()
        result = await connection.run_sync(reconcile_system_agents)
        await session.commit()
        return result


async def test_registry_reconciles_managed_fields_and_preserves_operating_state(sf):
    async with sf() as session:
        row = await session.get(AgentDef, "health-monitor")
        if row is None:
            row = AgentDef(name="health-monitor", system=True)
            session.add(row)
        row.prompt = "old alert instructions"
        row.enabled = False
        row.runtime = "codex"
        row.model = "operator-choice"
        row.entrypoints = {"crons": [{"schedule": "7 * * * *", "prompt": "check", "model": ""}],
                           "timezone": "America/Toronto", "topics": ["unsafe-injected"], "webhooks": []}
        await session.commit()
    assert not await _reconcile(sf)
    async with sf() as session:
        row = await session.get(AgentDef, "health-monitor")
        assert row.system_source == "platform:health-monitor"
        assert row.system_revision
        assert not row.enabled and row.model == "operator-choice"
        assert row.entrypoints["crons"][0]["schedule"] == "7 * * * *"
        assert row.entrypoints["topics"] == []
        assert "discord_chat" not in row.prompt
        assert "mcp__platform__health_incident" in row.platform_tools
        assert row.discord_identity_id is None
        count = await session.scalar(select(func.count()).select_from(AgentVersion))
    await _reconcile(sf)
    async with sf() as session:
        assert await session.scalar(select(func.count()).select_from(AgentVersion)) == count


async def test_registry_refuses_user_name_collision_and_ignores_unknown_system(sf):
    async with sf() as session:
        row = await session.get(AgentDef, "run-summarizer")
        if row is None:
            row = AgentDef(name="run-summarizer")
            session.add(row)
        row.system, row.system_source, row.prompt = False, None, "User content"
        session.add(AgentDef(name="user-system", system=True, prompt="Keep me"))
        await session.commit()
    assert any("run-summarizer" in issue for issue in await _reconcile(sf))
    async with sf() as session:
        assert (await session.get(AgentDef, "run-summarizer")).prompt == "User content"
        assert (await session.get(AgentDef, "user-system")).prompt == "Keep me"


def test_registry_field_policy_only_allows_operational_overrides():
    defs = definitions()
    assert set(defs) == SYSTEM_AGENT_NAMES
    base = defs["wiki"]
    assert managed_field_changes(base, {**base, "model": "other", "enabled": False}) == []
    assert managed_field_changes(base, {**base, "prompt": "new"}) == ["prompt"]
    assert managed_field_changes(base, {**base, "system": False}) == ["system"]
    assert managed_field_changes(base, {**base, "entrypoints": {
        **base["entrypoints"], "topics": ["new-trigger"]}}) == ["entrypoints.topics"]


async def test_agent_api_protects_registry_definition_but_allows_operating_settings(admin_client):
    current = (await admin_client.get("/api/agents/wiki")).json()
    assert current["system_source"] == "platform:wiki"
    refused = await admin_client.put("/api/agents/wiki", json={**current, "prompt": "Replace librarian"})
    assert refused.status_code == 409, refused.text
    saved = await admin_client.put("/api/agents/wiki", json={**current, "enabled": False})
    assert saved.status_code == 200, saved.text
    assert saved.json()["enabled"] is False
