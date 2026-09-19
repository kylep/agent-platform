"""The Help surface: concept topics from the synced docs, tool help in
lockstep with the grantable-tool list."""
from agentplatform.agentspec import AVAILABLE_TOOLS, TOOL_HELP


def test_tool_help_covers_available_tools_exactly():
    # The backpressure: adding a tool without explaining it fails here.
    assert [t["name"] for t in TOOL_HELP] == AVAILABLE_TOOLS
    assert all(len(t["description"]) >= 20 for t in TOOL_HELP)
    # the runner's always-denied set is marked so the help page can say so
    sensitive = {t["name"] for t in TOOL_HELP if t.get("sensitive")}
    assert sensitive == {"Bash", "Read", "Write", "Edit", "NotebookEdit"}


async def test_help_tools_endpoint(admin_client):
    r = await admin_client.get("/api/help/tools")
    assert r.status_code == 200
    tools = r.json()
    # Every static tool documented, plus one entry per valid registry tool
    # (their manifests self-document — see api/help.py).
    assert {t["name"] for t in tools} >= set(AVAILABLE_TOOLS)
    assert all(t["description"] for t in tools)
    bash = next(t for t in tools if t["name"] == "Bash")
    assert bash["sensitive"] is True and bash["kind"] == "claude"
    assert bash["dev_only"] is False
    # The picker's "dev runs only" note (docs/design/25) reads this flag, so
    # the schema must not drop it on the way out.
    pw = next(t for t in tools if t["name"] == "PlaywrightMCP")
    assert pw["dev_only"] is True and pw["display_name"] == "Playwright browser"


async def test_help_topics_from_synced_docs(admin_client, tmp_checkout):
    docs = tmp_checkout / "docs" / "building-blocks"
    docs.mkdir(parents=True)
    (docs / "agents.md").write_text("# Agents\n\nWho runs.")
    (docs / "README.md").write_text("# index — not a topic")
    r = await admin_client.get("/api/help/topics")
    assert r.json() == [{"slug": "agents", "title": "Agents"}]
    r = await admin_client.get("/api/help/topics/agents")
    assert r.json()["markdown"].startswith("# Agents")
    assert (await admin_client.get("/api/help/topics/nope")).status_code == 404
    assert (await admin_client.get("/api/help/topics/..%2Fsecret")).status_code in (400, 404)


async def test_help_tools_hides_internal_tools(admin_client, tmp_path):
    """An `internal: true` tool (docs/design/23) is the API's to run and never
    on the MCP surface, so it must not document itself as a grant — and the
    core `image_gen` entry must not appear beside a registry twin."""
    from agentplatform.toolregistry import ToolRegistry

    from .test_toolregistry import GOOD_YAML, make_tool
    make_tool(tmp_path, name="echo")
    make_tool(tmp_path, name="image_gen",
              yaml_text=GOOD_YAML.replace("name: echo", "name: image_gen") + "internal: true\n")
    admin_client._transport.app.state.tool_registry = ToolRegistry(tmp_path)
    names = [t["name"] for t in (await admin_client.get("/api/help/tools")).json()]
    assert "mcp__platform__echo" in names
    assert names.count("mcp__platform__image_gen") == 1
