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
    with pytest.raises(ValueError, match="UPPER_SNAKE"):
        ToolManifest(name="ok_tool", description="A perfectly valid description here.",
                     infra={"secrets": [{"name": "k", "env": "lower"}]})


def test_manifest_infra_defaults():
    m = ToolManifest(name="ok_tool", description="A perfectly valid description here.")
    assert m.infra.database is False and m.infra.secrets == []
    assert m.mcp_name == "mcp__platform__ok_tool"


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
