from pathlib import Path

from agentplatform.skills import SkillStore, parse_frontmatter


def _mk_skill(root: Path, name: str, body: str):
    d = root / name
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body)


def test_parse_frontmatter():
    fm, body = parse_frontmatter("---\nname: git\nsecrets: [a, b]\n---\nUse git.\n")
    assert fm["name"] == "git" and fm["secrets"] == ["a", "b"]
    assert body.strip() == "Use git."
    # No frontmatter → empty dict, body unchanged.
    fm2, body2 = parse_frontmatter("just text")
    assert fm2 == {} and body2 == "just text"


def test_store_loads_and_unions_secrets(tmp_path):
    _mk_skill(tmp_path, "git", "---\nname: git\ndescription: Git ops\nsecrets: [github-token]\n---\nbody")
    _mk_skill(tmp_path, "discord", "---\nname: discord\nsecrets: [discord-webhook, github-token]\n---\nbody")
    _mk_skill(tmp_path, "no-frontmatter", "just a body, no yaml")
    store = SkillStore(tmp_path)
    names = {s.name for s in store.list()}
    assert {"git", "discord", "no-frontmatter"} <= names
    # union dedupes github-token across git+discord
    assert set(store.secrets_for(["git", "discord"])) == {"github-token", "discord-webhook"}
    assert store.secrets_for(["git"]) == ["github-token"]
    # unknown skill contributes nothing
    assert store.secrets_for(["nope"]) == []


def test_bad_frontmatter_quarantines(tmp_path):
    _mk_skill(tmp_path, "broken", "---\nsecrets: [unclosed\n---\nbody")
    store = SkillStore(tmp_path)
    info = store.get("broken")
    assert info is not None and info.error is not None and info.skill is None


async def test_skills_api_lists_with_used_by(admin_client, sf):
    # The default test agent store has no skills wired, so used_by is empty but
    # the endpoint must still return 200 with the shape.
    r = await admin_client.get("/api/skills")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
