"""Custom-tool registration in the broker (docs/design/12, extended by
docs/design/23): what the checkout scan turns into MCP tools, and the one
timing contract the forward carries.

An `internal: true` manifest is the API's tool, not an agent's — the scan must
never hand it to the MCP surface, or "internal" would be a label rather than a
boundary. The executor forward's HTTP timeout tracks the manifest's own
`timeout_seconds` (plus the executor's overhead) instead of a fixed number, so
a 300 s image job is not cut off at 150 by the hop in front of it.

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q test_custom_tools.py

The fastmcp stub and the loaded broker module come from `test_relay_tool.py`."""
import asyncio

import httpx
import pytest
from test_relay_tool import broker, caller  # noqa: F401


def _tool(root, name, extra=""):
    d = root / name
    d.mkdir()
    (d / "tool.yaml").write_text(
        f"name: {name}\ndescription: A test tool with enough description text.\n"
        "params: {type: object, properties: {}}\n" + extra)
    (d / "run.py").write_text("print('ok')\n")


@pytest.fixture
def tools_root(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "_TOOLS_ROOT", tmp_path)
    return tmp_path


def test_scan_skips_internal_tools(tools_root):
    _tool(tools_root, "stocks")
    _tool(tools_root, "image_gen", "internal: true\n")
    _tool(tools_root, "not_hidden", "internal: false\n")
    assert set(broker._scan_custom_tools()) == {"stocks", "not_hidden"}


def test_refresh_never_registers_an_internal_tool(tools_root, monkeypatch):
    _tool(tools_root, "stocks", "timeout_seconds: 200\n")
    _tool(tools_root, "image_gen", "internal: true\n")
    added = []
    monkeypatch.setattr(broker.mcp, "add_tool", added.append)
    monkeypatch.setattr(broker, "_registered", {})
    broker.refresh_custom_tools()
    assert [t.name for t in added] == ["stocks"]
    assert added[0].timeout_seconds == 200


def test_forward_timeout_is_manifest_timeout_plus_30(monkeypatch):
    seen = {}

    class Client:
        def __init__(self, base_url=None, timeout=None):
            seen["timeout"] = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, path, json=None):
            return httpx.Response(200, json={"ok": True, "output": "done"})

    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)

    async def _audit(*a, **k):
        pass

    monkeypatch.setattr(broker, "_audit", _audit)
    tool = broker.CustomTool(name="stocks", description="A test tool with enough text.",
                             parameters={"type": "object", "properties": {}},
                             timeout_seconds=180)
    out = asyncio.run(tool.run({}))
    assert out.content == "done"
    assert seen["timeout"] == 210
    # The default (a manifest that never set timeout_seconds) is the
    # registry's own default, not a second number to keep in sync.
    tool = broker.CustomTool(name="stocks", description="A test tool with enough text.",
                             parameters={"type": "object", "properties": {}})
    asyncio.run(tool.run({}))
    assert seen["timeout"] == 60


def test_refresh_re_registers_when_the_timeout_changes(tools_root, monkeypatch):
    _tool(tools_root, "stocks", "timeout_seconds: 60\n")
    added = []
    monkeypatch.setattr(broker.mcp, "add_tool", added.append)
    monkeypatch.setattr(broker, "_registered", {})
    broker.refresh_custom_tools()
    broker.refresh_custom_tools()          # unchanged → no churn
    assert [t.timeout_seconds for t in added] == [60]
    (tools_root / "stocks" / "tool.yaml").write_text(
        (tools_root / "stocks" / "tool.yaml").read_text().replace("60", "240"))
    broker.refresh_custom_tools()
    assert [t.timeout_seconds for t in added] == [60, 240]


@pytest.mark.parametrize("raw, clamped", [("9999", 300), ("0", 1), ("-5", 1),
                                          ("abc", 30), ("200", 200)])
def test_scan_clamps_the_manifest_timeout(tools_root, raw, clamped):
    """The registry refuses these manifests; the broker reads the raw yaml and
    must not let a bad number become a near-infinite forward timeout."""
    _tool(tools_root, "stocks", f"timeout_seconds: {raw}\n")
    assert broker._scan_custom_tools()["stocks"]["timeout_seconds"] == clamped


def test_scan_skips_a_manifest_that_declares_files(tools_root):
    """`files` is reserved (docs/design/25): the broker resolves it into
    `files_in` and the executor never sees it, so a manifest describing it is
    invalid — the registry says so in the UI; here it is simply not a tool."""
    _tool(tools_root, "stocks")
    _tool(tools_root, "tcms_bad",
          "params: {type: object, properties: {files: {type: array}}}\n")
    assert set(broker._scan_custom_tools()) == {"stocks"}


def test_every_custom_tool_advertises_the_files_argument(tools_root, monkeypatch):
    """The model learns `files` from the schema, not from a description: the
    broker adds it to what it registers, beside the manifest's own params."""
    _tool(tools_root, "stocks", "params: {type: object, properties: {symbol: {type: string}}}\n")
    added = []
    monkeypatch.setattr(broker.mcp, "add_tool", added.append)
    monkeypatch.setattr(broker, "_registered", {})
    broker.refresh_custom_tools()
    props = added[0].parameters["properties"]
    assert props["symbol"] == {"type": "string"}
    assert props["files"]["type"] == "array"
    assert props["files"]["items"] == {"type": "string", "pattern": "^[0-9a-f]{32}$"}
    assert props["files"]["maxItems"] == 4
    assert "ap-upload" in props["files"]["description"]
    # The scan's own dict is not what was mutated.
    assert "files" not in broker._scan_custom_tools()["stocks"]["params"]["properties"]
