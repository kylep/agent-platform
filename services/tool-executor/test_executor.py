"""Executor invariants (docs/design/12): the subprocess sees a minimal env
(never this process's), schema violations are rejected before any execution,
timeouts kill, and failures come back structured for the model."""
import json
import os

import pytest
from fastapi.testclient import TestClient

import executor


@pytest.fixture
def tools_root(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "TOOLS_ROOT", tmp_path)
    return tmp_path


def make_tool(root, name="envdump", run_py=None, yaml_extra=""):
    d = root / name
    d.mkdir()
    (d / "tool.yaml").write_text(
        f"name: {name}\n"
        "description: Test tool that dumps its environment for the canary test.\n"
        "params:\n  type: object\n  properties:\n    x: {type: string}\n"
        + yaml_extra)
    (d / "run.py").write_text(run_py or
        "import json, os, sys\n"
        "args = json.load(sys.stdin)\n"
        "print(json.dumps({'env': dict(os.environ), 'args': args}))\n")
    return d


def test_env_minimalism_canary(tools_root):
    """The one test that guards the whole security story: a tool subprocess
    must see ONLY the explicit allow-list, never the executor's own env."""
    make_tool(tools_root)
    os.environ["LEAKY_PARENT_SECRET"] = "supersecret"
    try:
        c = TestClient(executor.app)
        r = c.post("/run", json={"tool": "envdump", "args": {"x": "1"},
                                 "caller": {"agent": "tester", "run_id": "r1"}})
        body = r.json()
        assert body["ok"], body
        env = json.loads(body["output"])["env"]
        assert "LEAKY_PARENT_SECRET" not in env
        assert env["TOOL_CALLER_AGENT"] == "tester"
        assert env["TOOL_RUN_ID"] == "r1"
        assert set(env) <= {"PATH", "HOME", "LANG", "TOOL_NAME",
                            "TOOL_CALLER_AGENT", "TOOL_RUN_ID", "LC_CTYPE", "PWD"}
    finally:
        del os.environ["LEAKY_PARENT_SECRET"]


def test_schema_rejection_before_execution(tools_root):
    make_tool(tools_root, run_py="import sys; sys.exit(99)\n",
              yaml_extra="  required: [x]\n")
    c = TestClient(executor.app)
    r = c.post("/run", json={"tool": "envdump", "args": {}})
    body = r.json()
    # Rejected by schema — run.py (which would exit 99) never ran.
    assert body["ok"] is False and "schema" in body["error"]


def test_nonzero_exit_is_structured_error(tools_root):
    make_tool(tools_root, run_py="import sys; print('boom', file=sys.stderr); sys.exit(3)\n")
    c = TestClient(executor.app)
    body = c.post("/run", json={"tool": "envdump", "args": {}}).json()
    assert body["ok"] is False and "exited 3" in body["error"] and "boom" in body["error"]


def test_timeout_kills(tools_root):
    make_tool(tools_root, run_py="import time; time.sleep(60)\n",
              yaml_extra="timeout_seconds: 1\n")
    c = TestClient(executor.app)
    body = c.post("/run", json={"tool": "envdump", "args": {}}).json()
    assert body["ok"] is False and "timed out" in body["error"]


def test_output_cap(tools_root):
    make_tool(tools_root, run_py="print('x' * (300 * 1024))\n")
    c = TestClient(executor.app)
    body = c.post("/run", json={"tool": "envdump", "args": {}}).json()
    assert body["ok"] and "truncated" in body["output"]
    assert len(body["output"]) < 300 * 1024


def test_unknown_and_traversal_names_404(tools_root):
    c = TestClient(executor.app)
    assert c.post("/run", json={"tool": "nope", "args": {}}).status_code == 404
    assert c.post("/run", json={"tool": "../etc", "args": {}}).status_code in (400, 404, 422)
