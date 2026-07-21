import subprocess

import httpx
import pytest
import yaml

from agentplatform.agents import AgentStore
from agentplatform.agentspec import (AVAILABLE_TOOLS, mutate_agent_md,
                                     mutate_manifest_yaml, parse_agent_tools,
                                     render_agent_md, render_manifest,
                                     validate_agent_name)
from agentplatform.api.app import create_app
from agentplatform.config import Settings
from agentplatform.events import FakeProducer
from agentplatform.secrets import InMemorySecretStore
from agentplatform.skills import SkillStore
from agentplatform.tiers import (TIER_DIRECT, TIER_PR, FileChange, classify_tier)


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


# --- agentspec unit tests ---------------------------------------------------

def test_validate_agent_name():
    assert validate_agent_name("my-agent-1") == "my-agent-1"
    for bad in ["", "-lead", "UPPER", "has space", "a/b", "x" * 64]:
        with pytest.raises(ValueError):
            validate_agent_name(bad)


def test_render_agent_md_all_tools_omits_line():
    md = render_agent_md("bob", "does things", AVAILABLE_TOOLS, "You are bob.")
    assert "tools:" not in md                      # all selected → unrestricted
    fm = yaml.safe_load(md.split("---")[1])
    assert fm["name"] == "bob" and fm["description"] == "does things"
    assert md.rstrip().endswith("You are bob.")


def test_render_agent_md_subset_writes_list():
    md = render_agent_md("bob", "d", ["Bash", "Read"], "body")
    assert parse_agent_tools(md) == ["Bash", "Read"]


def test_render_agent_md_empty_selection_is_unrestricted():
    # No boxes checked collapses to "all tools" (documented convention).
    md = render_agent_md("bob", "d", [], "body")
    assert "tools:" not in md


def test_render_manifest_drops_empties():
    out = yaml.safe_load(render_manifest(
        {"description": "d", "role": None, "skills": [], "model": "sonnet"}))
    assert out == {"description": "d", "model": "sonnet"}


def test_mutate_manifest_preserves_other_fields():
    original = "description: d\nrole: operator\nconcurrency: 3\nsecrets:\n  - github-token\n"
    out = yaml.safe_load(mutate_manifest_yaml(original, skills=["git"], description="new"))
    assert out["concurrency"] == 3 and out["secrets"] == ["github-token"]
    assert out["skills"] == ["git"] and out["description"] == "new"


def test_mutate_manifest_empty_skills_drops_key():
    out = yaml.safe_load(mutate_manifest_yaml("description: d\nskills:\n  - git\n", skills=[]))
    assert "skills" not in out


def test_mutate_agent_md_updates_tools_keeps_body():
    md = "---\nname: bob\ndescription: d\ntools: Bash\n---\nYou are bob.\n"
    out = mutate_agent_md(md, tools=["Bash", "Read"])
    assert parse_agent_tools(out) == ["Bash", "Read"]
    assert "You are bob." in out
    fm = yaml.safe_load(out.split("---")[1])
    assert fm["name"] == "bob"                      # name preserved


def test_mutate_agent_md_all_tools_removes_line():
    md = "---\nname: bob\ndescription: d\ntools: Bash\n---\nbody\n"
    assert parse_agent_tools(mutate_agent_md(md, tools=AVAILABLE_TOOLS)) is None


def test_parse_agent_tools_no_line_is_none():
    assert parse_agent_tools("---\nname: bob\n---\nbody") is None


# --- tier classification ----------------------------------------------------

def test_agent_md_frontmatter_change_is_tier2():
    body_only = [FileChange("agents/x/agent.md", "modified", frontmatter_changed=False)]
    fm_change = [FileChange("agents/x/agent.md", "modified", frontmatter_changed=True)]
    assert classify_tier(body_only) == TIER_DIRECT
    assert classify_tier(fm_change) == TIER_PR


def test_compute_changes_flags_frontmatter(tmp_path):
    from agentplatform.gitservice import compute_changes
    git(tmp_path, "init", "-q", "-b", "main", str(tmp_path))
    git(tmp_path, "config", "user.email", "s@s"); git(tmp_path, "config", "user.name", "s")
    p = tmp_path / "agents" / "x" / "agent.md"
    p.parent.mkdir(parents=True)
    p.write_text("---\nname: x\ntools: Bash\n---\nYou are x.\n")
    git(tmp_path, "add", "-A"); git(tmp_path, "commit", "-qm", "init")
    # Body-only edit: not a frontmatter change.
    p.write_text("---\nname: x\ntools: Bash\n---\nYou are x. Improved.\n")
    assert compute_changes(tmp_path)[0].frontmatter_changed is False
    # Widen tools: a frontmatter change.
    p.write_text("---\nname: x\ntools: Bash, Read\n---\nYou are x. Improved.\n")
    assert compute_changes(tmp_path)[0].frontmatter_changed is True


# --- API: create + config edit (against a local bare origin) ----------------

@pytest.fixture
def selfhost(tmp_path):
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", "-q", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    git(seed, "config", "user.email", "s@s"); git(seed, "config", "user.name", "s")
    d = seed / "agents" / "demo"
    d.mkdir(parents=True)
    (d / "agent.md").write_text("---\nname: demo\ndescription: demo\ntools: Bash\n---\nYou are demo.\n")
    (d / "manifest.yaml").write_text("description: demo\nrole: operator\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "init")
    git(seed, "branch", "-M", "main"); git(seed, "push", "-q", "origin", "main")
    # A skills tree so skill validation has something to accept.
    skills = tmp_path / "skills" / "git"
    skills.mkdir(parents=True)
    (skills / "SKILL.md").write_text("---\nname: git\nicon: 🔀\n---\n# git\n")
    return {"bare": bare, "agents_root": seed / "agents", "skills_root": tmp_path / "skills"}


@pytest.fixture
async def sh_client(sf, selfhost):
    settings = Settings(agents_root=str(selfhost["agents_root"]),
                        skills_root=str(selfhost["skills_root"]),
                        git_remote_url=str(selfhost["bare"]), default_branch="main")
    app = create_app(settings, sf, FakeProducer(),
                     secret_store=InMemorySecretStore(),
                     agent_store=AgentStore(selfhost["agents_root"]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/setup", json={"password": "pw12345678"})
        await c.post("/api/login", json={"password": "pw12345678"})
        yield c, selfhost


def _show(bare, ref, path):
    return subprocess.run(["git", "-C", str(bare), "show", f"{ref}:{path}"],
                          capture_output=True, text=True, check=True).stdout


async def test_agent_tools_lists_canonical(sh_client):
    c, _ = sh_client
    r = await c.get("/api/agent-tools")
    assert r.status_code == 200 and r.json()["tools"] == AVAILABLE_TOOLS


async def test_create_agent_opens_pr_branch(sh_client):
    c, sh = sh_client
    r = await c.post("/api/agents", json={
        "name": "newbie", "description": "a new one", "skills": ["git"],
        "tools": ["Bash", "Read"], "prompt": "You are newbie."})
    assert r.status_code == 201
    body = r.json()
    assert body["tier"] == TIER_PR and body["branch"] == "coder/agent-newbie"
    md = _show(sh["bare"], "coder/agent-newbie", "agents/newbie/agent.md")
    assert parse_agent_tools(md) == ["Bash", "Read"]
    manifest = yaml.safe_load(_show(sh["bare"], "coder/agent-newbie", "agents/newbie/manifest.yaml"))
    assert manifest["skills"] == ["git"] and manifest["description"] == "a new one"


async def test_create_agent_rejects_duplicate(sh_client):
    c, _ = sh_client
    r = await c.post("/api/agents", json={"name": "demo"})
    assert r.status_code == 409


async def test_create_agent_rejects_bad_name(sh_client):
    c, _ = sh_client
    r = await c.post("/api/agents", json={"name": "Bad Name"})
    assert r.status_code == 422


async def test_create_agent_rejects_unknown_skill_and_tool(sh_client):
    c, _ = sh_client
    assert (await c.post("/api/agents", json={"name": "a1", "skills": ["ghost"]})).status_code == 422
    assert (await c.post("/api/agents", json={"name": "a2", "tools": ["Nope"]})).status_code == 422


async def test_edit_config_opens_pr(sh_client):
    c, sh = sh_client
    r = await c.patch("/api/agents/demo/config",
                      json={"skills": ["git"], "tools": ["Bash", "Read", "Edit"]})
    assert r.status_code == 200 and r.json()["tier"] == TIER_PR
    manifest = yaml.safe_load(_show(sh["bare"], "coder/agent-demo", "agents/demo/manifest.yaml"))
    assert manifest["skills"] == ["git"]
    md = _show(sh["bare"], "coder/agent-demo", "agents/demo/agent.md")
    assert set(parse_agent_tools(md)) == {"Bash", "Read", "Edit"}


async def test_edit_config_noop_is_tier0(sh_client):
    c, _ = sh_client
    # demo already has tools: Bash and no skills — re-asserting is a no-op.
    r = await c.patch("/api/agents/demo/config", json={"tools": ["Bash"]})
    assert r.status_code == 200 and r.json()["tier"] == 0


async def test_edit_config_unknown_agent_404(sh_client):
    c, _ = sh_client
    assert (await c.patch("/api/agents/ghost/config", json={"tools": ["Bash"]})).status_code == 404
