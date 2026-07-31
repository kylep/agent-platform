"""Skill quick-edit + New-Skill wizard (docs/design/10 phase 4)."""
from agentplatform.events import TOPIC_RUN_REQUESTS


async def test_skill_detail_includes_raw(admin_client):
    r = await admin_client.get("/api/skills/git")
    assert r.status_code == 200
    d = r.json()
    assert d["raw"].startswith("---") and d["body"] in d["raw"]


async def test_skill_quick_edit_unknown_404(admin_client):
    r = await admin_client.post("/api/skills/nope/quick-edit", json={"value": "x"})
    assert r.status_code == 404


async def test_skill_quick_edit_unconfigured_409(admin_client):
    # no git remote configured in the test app → a clear 409, not a crash
    r = await admin_client.post("/api/skills/git/quick-edit", json={"value": "# x"})
    assert r.status_code == 409


async def test_wizard_validates_and_dispatches(admin_client, tmp_agents, producer):
    # platform-coder must exist for the wizard to dispatch
    d = tmp_agents / "platform-coder"; d.mkdir()
    (d / "agent.md").write_text("# coder")
    (d / "manifest.yaml").write_text("role: coder\n")

    r = await admin_client.post("/api/skills/new", json={"name": "Bad Name", "purpose": "x"})
    assert r.status_code == 422
    r = await admin_client.post("/api/skills/new", json={"name": "git", "purpose": "x"})
    assert r.status_code == 409  # exists

    r = await admin_client.post("/api/skills/new", json={
        "name": "notion", "purpose": "Create pages in Notion.",
        "when_to_use": "When asked to publish notes.",
        "secret": {"name": "notion-token", "env_var": "NOTION_TOKEN",
                   "description": "Notion internal integration token"},
        "notes": "Keep it small."})
    assert r.status_code == 202
    rid = r.json()["id"]
    reqs = [p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS]
    assert reqs and reqs[-1][1] == rid
    # the run's prompt scopes the coder to the skill + secret folders
    runs = await admin_client.get(f"/api/runs/{rid}")
    prompt = runs.json()["prompt"]
    assert "skills/notion/" in prompt and "secrets/notion-token/secret.yaml" in prompt
    assert "$NOTION_TOKEN" in prompt and runs.json()["agent"] == "platform-coder"


async def test_wizard_without_coder_409(admin_client):
    r = await admin_client.post("/api/skills/new", json={"name": "notion", "purpose": "x"})
    assert r.status_code == 409
