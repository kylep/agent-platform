"""Custom platform tools (docs/design/12): registry parsing/validation and the
/api/tools surface. Tools are trusted code + model-controlled args; these tests
guard the registry-side invariants (a tool cannot shadow a core tool, cannot
exist without an entrypoint, and self-documents into help/agent-tools)."""
from pathlib import Path

import httpx
import pytest

from agentplatform.agentspec import PLATFORM_MCP_TOOLS
from agentplatform.api.app import create_app
from agentplatform.config import Settings
from agentplatform.toolregistry import CORE_TOOL_SUFFIXES, ToolManifest, ToolRegistry

from .conftest import REPO_APPS, REPO_REPORTS, REPO_SECRETS, REPO_SKILLS


GOOD_YAML = """\
name: echo
description: Echo the given text back, for tests that need a working tool.
params:
  type: object
  properties:
    text: {type: string}
  required: [text]
timeout_seconds: 10
"""


def make_tool(root: Path, name="echo", yaml_text=GOOD_YAML, entrypoint=True,
              requirements=False):
    d = root / name
    d.mkdir(parents=True)
    (d / "tool.yaml").write_text(yaml_text)
    if entrypoint:
        (d / "run.py").write_text("import json,sys; print(json.dumps(json.load(sys.stdin)))\n")
    if requirements:
        (d / "requirements.txt").write_text("yfinance==0.2.65\n")
    return d


# --- registry ----------------------------------------------------------------

def test_core_suffixes_lockstep_with_agentspec():
    """toolregistry's shadow-list and agentspec's broker tool list must agree,
    or a custom tool could silently collide with a core one."""
    assert {t.removeprefix("mcp__platform__") for t in PLATFORM_MCP_TOOLS} == set(CORE_TOOL_SUFFIXES)


def test_registry_loads_valid_tool(tmp_path):
    make_tool(tmp_path, requirements=True)
    reg = ToolRegistry(tmp_path)
    t = reg.get("echo")
    assert t.manifest is not None and t.error is None
    assert t.has_requirements
    assert reg.mcp_names() == ["mcp__platform__echo"]


def test_registry_missing_entrypoint_is_error(tmp_path):
    make_tool(tmp_path, entrypoint=False)
    t = ToolRegistry(tmp_path).get("echo")
    assert t.manifest is None and "run.py" in t.error
    assert ToolRegistry(tmp_path).mcp_names() == []


def test_registry_name_dir_mismatch_is_error(tmp_path):
    make_tool(tmp_path, name="other", yaml_text=GOOD_YAML)
    t = ToolRegistry(tmp_path).get("other")
    assert t.manifest is None and "must match directory" in t.error


def test_registry_bad_yaml_surfaces_error(tmp_path):
    make_tool(tmp_path, yaml_text="name: echo\ndescription: [unclosed")
    t = ToolRegistry(tmp_path).get("echo")
    assert t.manifest is None and t.error


def test_manifest_rejects_core_shadow():
    with pytest.raises(ValueError, match="shadows a core"):
        ToolManifest(name="metrics", description="Sneaky shadow of a core tool.")


def test_manifest_rejects_bad_names_and_bounds():
    with pytest.raises(ValueError):
        ToolManifest(name="Bad-Name", description="Name style must be snake_case here.")
    with pytest.raises(ValueError, match="20 chars"):
        ToolManifest(name="ok_tool", description="too short")
    with pytest.raises(ValueError, match="timeout"):
        ToolManifest(name="ok_tool", description="A perfectly valid description here.",
                     timeout_seconds=600)
    with pytest.raises(ValueError, match="type: object"):
        ToolManifest(name="ok_tool", description="A perfectly valid description here.",
                     params={"type": "string"})


def test_manifest_infra_defaults_and_secret_coercion():
    m = ToolManifest(name="ok_tool", description="A perfectly valid description here.")
    assert m.infra.database is False and m.infra.secrets == []
    assert m.mcp_name == "mcp__platform__ok_tool"
    m = ToolManifest(name="ok_tool", description="A perfectly valid description here.",
                     infra={"secrets": ["linear-api-key", {"name": "discord-bot"}]})
    assert m.infra.secrets == ["linear-api-key", "discord-bot"]


# --- API surface -------------------------------------------------------------

@pytest.fixture
async def tool_client(sf, producer, secret_store, agent_store, tmp_path):
    tools_root = tmp_path / "tools"
    make_tool(tools_root, requirements=True)
    make_tool(tools_root, name="broken", yaml_text="name: broken\ndescription: x\n")
    # An agent that declares the custom tool, for used_by.
    d = agent_store.root / "echo-user"
    d.mkdir()
    (d / "agent.md").write_text("---\nname: echo-user\ntools: mcp__platform__echo\n---\nbody")
    (d / "manifest.yaml").write_text("description: t\n")
    app = create_app(Settings(agents_root=str(agent_store.root),
                              secrets_root=str(REPO_SECRETS),
                              skills_root=str(REPO_SKILLS),
                              reports_root=str(REPO_REPORTS),
                              apps_root=str(REPO_APPS),
                              tools_root=str(tools_root)), sf, producer,
                     secret_store=secret_store, agent_store=agent_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/setup", json={"password": "pw12345678"})
        await c.post("/api/login", json={"password": "pw12345678"})
        yield c


async def test_list_tools(tool_client):
    r = await tool_client.get("/api/tools")
    assert r.status_code == 200
    by_name = {t["name"]: t for t in r.json()}
    assert by_name["echo"]["error"] is None
    assert by_name["echo"]["has_requirements"] is True
    assert by_name["echo"]["used_by"] == ["echo-user"]
    assert by_name["broken"]["error"]  # short description → validation error


async def test_get_tool_returns_files(tool_client):
    r = await tool_client.get("/api/tools/echo")
    assert r.status_code == 200
    d = r.json()
    assert d["params"]["required"] == ["text"]
    assert "run.py" in d["files"] and "tool.yaml" in d["files"]
    assert (await tool_client.get("/api/tools/nope")).status_code == 404


async def test_custom_tool_is_grantable_and_documented(tool_client):
    tools = (await tool_client.get("/api/agent-tools")).json()["tools"]
    assert "mcp__platform__echo" in tools
    help_names = {t["name"]: t for t in (await tool_client.get("/api/help/tools")).json()}
    assert "mcp__platform__echo" in help_names
    assert "Echo the given text" in help_names["mcp__platform__echo"]["description"]
    # Broken tools are not grantable.
    assert "mcp__platform__broken" not in tools


async def test_agent_edit_rejects_unknown_but_not_custom_tools(tool_client):
    """Validation must know registry tools: an unknown name 422s, a declared
    custom tool passes validation (whatever the git layer then does)."""
    r = await tool_client.patch("/api/agents/echo-user/config",
                                json={"tools": ["mcp__platform__not_a_tool"]})
    assert r.status_code == 422
    r = await tool_client.patch("/api/agents/echo-user/config",
                                json={"tools": ["mcp__platform__echo"]})
    assert r.status_code != 422, r.text


# --- whoami + tools-role scoping (docs/design/12) ----------------------------

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey


async def _tools_key(sf, agent="echo-user", role="tools", run_id="run-1") -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=f"{role}:{agent}", role=role, agent=agent, run_id=run_id,
                     key_hash=hash_token(token), prefix=token_prefix(token)))
        await s.commit()
    return token


async def test_whoami_resolves_agent_and_declared_tools(tool_client, sf):
    token = await _tools_key(sf)
    tool_client.cookies.clear()  # session cookie outranks the bearer in authenticate()
    r = await tool_client.get("/api/whoami", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    d = r.json()
    assert d["agent"] == "echo-user" and d["run_id"] == "run-1" and d["role"] == "tools"
    assert d["tools"] == ["mcp__platform__echo"]


async def test_whoami_admin_session_has_no_agent(tool_client):
    d = (await tool_client.get("/api/whoami")).json()
    assert d["principal"] == "admin" and d["agent"] is None and d["tools"] is None


async def test_tools_role_reaches_nothing_else(tool_client, sf):
    """The tools role is whoami-only: every data surface 403s."""
    token = await _tools_key(sf)
    tool_client.cookies.clear()
    h = {"Authorization": f"Bearer {token}"}
    for path in ("/api/runs", "/api/memories", "/api/metrics/overview",
                 "/api/agents", "/api/tools"):
        r = await tool_client.get(path, headers=h)
        assert r.status_code == 403, f"{path} → {r.status_code}"


async def test_whoami_requires_auth(client):
    assert (await client.get("/api/whoami")).status_code == 401


# --- quick-edit + wizard validation (docs/design/12 P7) ----------------------

async def test_tool_quick_edit_validates_manifest(tool_client):
    r = await tool_client.post("/api/tools/echo/quick-edit", json={
        "files": {"tool.yaml": "name: echo\ndescription: short\n"}})
    assert r.status_code == 422 and "invalid tool.yaml" in r.text
    r = await tool_client.post("/api/tools/echo/quick-edit", json={
        "files": {"../evil.py": "x"}})
    assert r.status_code == 422 and "not editable" in r.text
    r = await tool_client.post("/api/tools/echo/quick-edit", json={"files": {}})
    assert r.status_code == 422
    assert (await tool_client.post("/api/tools/nope/quick-edit",
                                   json={"files": {"run.py": "x"}})).status_code == 404


async def test_tool_wizard_validates(tool_client):
    r = await tool_client.post("/api/tools/new", json={"name": "Bad Name!", "purpose": "x"})
    assert r.status_code == 422
    r = await tool_client.post("/api/tools/new", json={"name": "echo", "purpose": "dup"})
    assert r.status_code == 409
