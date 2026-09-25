"""Skill quick-edit + New-Skill wizard (docs/design/10 phase 4)."""
import pytest

from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.skills import SkillStore


@pytest.fixture(autouse=True)
def sample_skill(tmp_path, admin_client):
    skill = tmp_path / "release-review"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: release-review\ndescription: Review a release.\n---\nCheck evidence.\n")
    admin_client._transport.app.state.skill_store = SkillStore(tmp_path)


async def test_skill_detail_includes_raw(admin_client):
    r = await admin_client.get("/api/skills/release-review")
    assert r.status_code == 200
    d = r.json()
    assert d["raw"].startswith("---") and d["body"] in d["raw"]


async def test_skill_quick_edit_unknown_404(admin_client):
    r = await admin_client.post("/api/skills/nope/quick-edit", json={"value": "x"})
    assert r.status_code == 404


async def test_skill_quick_edit_unconfigured_409(admin_client):
    # no git remote configured in the test app → a clear 409, not a crash
    r = await admin_client.post("/api/skills/release-review/quick-edit", json={"value": "# x"})
    assert r.status_code == 409


async def test_wizard_validates_and_dispatches(admin_client, seed_agent, producer):
    # The seeded engineer Workbench authors wizard changes.

    r = await admin_client.post("/api/skills/new", json={"name": "Bad Name", "purpose": "x"})
    assert r.status_code == 422
    r = await admin_client.post("/api/skills/new", json={"name": "release-review", "purpose": "x"})
    assert r.status_code == 409  # exists

    r = await admin_client.post("/api/skills/new", json={
        "name": "notion", "purpose": "Create pages in Notion.",
        "when_to_use": "When asked to publish notes.",
        "secret": {"name": "notion-token", "env_var": "NOTION_TOKEN",
                   "description": "Notion internal integration token"},
        "notes": "Keep it small."})
    assert r.status_code == 422  # a skill cannot request secret authority
    r = await admin_client.post("/api/skills/new", json={
        "name": "notion", "purpose": "Create pages in Notion.",
        "when_to_use": "When asked to publish notes.",
        "notes": "Keep it small."})
    assert r.status_code == 202
    rid = r.json()["id"]
    reqs = [p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS]
    assert reqs and reqs[-1][1] == rid
    # the run's prompt scopes the coder to the skill alone
    runs = await admin_client.get(f"/api/runs/{rid}")
    prompt = runs.json()["prompt"]
    assert "skills/notion/" in prompt and "secrets/notion-token/" not in prompt
    assert "skill grants no secrets" in prompt.lower() and runs.json()["agent"] == "coder"


async def test_wizard_without_engineer_409(admin_client, sf, agent_store):
    from agentplatform.db import AgentDef
    async with sf() as s:
        (await s.get(AgentDef, "coder")).enabled = False
        await s.commit()
    await agent_store.reload()
    r = await admin_client.post("/api/skills/new", json={"name": "notion", "purpose": "x"})
    assert r.status_code == 409
