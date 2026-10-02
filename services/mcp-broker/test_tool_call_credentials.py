"""The broker's half of tool-call credentials (docs/design/39).

For a custom tool whose manifest declares `app_access`, the broker exchanges
the caller's identity for a credential bound to this call (presenting its own
workload token, never the caller's as its own), hands it to the executor in
the run request, and revokes it by jti when the call returns, whatever the
outcome. A tool without `app_access` never causes an exchange, and an
exchange the API refuses means the tool does not run.

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q test_tool_call_credentials.py
"""
import asyncio

import httpx
import pytest
from test_relay_tool import broker, caller  # noqa: F401

MINTED = {"credential": "h.p.s", "call_id": "c" * 32, "jti": "j" * 32,
          "app_scope": [], "expires_at": "x"}


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """Every HTTP hop the broker makes, recorded; answers set per test."""
    calls = []
    answers = {"mint": httpx.Response(200, json=MINTED),
               "run": httpx.Response(200, json={"ok": True, "output": "done"}),
               "revoke": httpx.Response(200, json={"ok": True})}

    class Client:
        def __init__(self, base_url=None, timeout=None):
            self.base = base_url

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, path, json=None, headers=None):
            calls.append(("POST", self.base, path, json, headers))
            answer = answers["mint" if path == "/api/tool-calls" else "run"]
            if isinstance(answer, Exception):
                raise answer
            return answer

        async def delete(self, path, headers=None):
            calls.append(("DELETE", self.base, path, None, headers))
            return answers["revoke"]

    async def audit(*a, **k):
        calls.append(("AUDIT", a[5]))

    token = tmp_path / "token"
    token.write_text("broker.sa.token\n")
    monkeypatch.setattr(broker, "_IDENTITY_FILE", token)
    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    monkeypatch.setattr(broker, "_audit", audit)
    monkeypatch.setattr(broker, "_caller_headers", lambda: {
        "Authorization": "Bearer agent.sa.token", "X-AP-Run-Token": "run.jwt.x"})
    return calls, answers, token


def _tool(app_access=True):
    return broker.CustomTool(name="ledger", description="A test tool with enough text.",
                             parameters={"type": "object", "properties": {}},
                             app_access=app_access)


def test_exchange_hands_the_executor_the_credential_and_revokes_at_return(wire):
    calls, _, _ = wire
    out = asyncio.run(_tool().run({"action": "record"}))
    assert out.content == "done"
    mint, run, revoke = [c for c in calls if c[0] != "AUDIT"]
    # The exchange: the broker's own identity as the bearer, the caller's
    # bearer and run JWT as what is being exchanged.
    assert mint[1] == broker._API and mint[2] == "/api/tool-calls"
    assert mint[4] == {"Authorization": "Bearer broker.sa.token"}
    assert mint[3] == {"caller": {"authorization": "Bearer agent.sa.token",
                                  "run_token": "run.jwt.x"},
                       "tool": "ledger", "action": "record"}
    # The executor gets the credential in the run request, never a header
    # of the caller's.
    assert run[1] == broker._EXECUTOR and run[2] == "/run"
    assert run[3]["credential"] == {"token": "h.p.s", "call_id": "c" * 32}
    assert run[4] is None
    assert revoke[:3] == ("DELETE", broker._API, f"/api/tool-calls/{'j' * 32}")
    assert revoke[4] == {"Authorization": "Bearer broker.sa.token"}


@pytest.mark.parametrize("outcome", [
    httpx.Response(200, json={"ok": False, "error": "boom"}),
    httpx.Response(500, text="executor fell over"),
    httpx.ConnectError("down"),
])
def test_revoked_whatever_the_outcome(wire, outcome):
    calls, answers, _ = wire
    answers["run"] = outcome
    out = asyncio.run(_tool().run({}))
    assert out.content.startswith("error:")
    assert [c[:3] for c in calls if c[0] == "DELETE"] == [
        ("DELETE", broker._API, f"/api/tool-calls/{'j' * 32}")]


def test_a_refused_exchange_means_the_tool_does_not_run(wire):
    calls, answers, _ = wire
    answers["mint"] = httpx.Response(403, json={"detail": "no run: ..."})
    out = asyncio.run(_tool().run({}))
    assert out.content.startswith("error: no App access for this call: 403")
    assert [c[2] for c in calls if c[0] in ("POST", "DELETE")] == ["/api/tool-calls"]
    assert ("AUDIT", "deny:no-credential") in calls


def test_no_workload_identity_fails_closed(wire):
    calls, _, token = wire
    token.unlink()
    out = asyncio.run(_tool().run({}))
    assert out.content.startswith("error:") and "workload identity" in out.content
    assert [c for c in calls if c[0] in ("POST", "DELETE")] == []


def test_a_tool_without_app_access_never_exchanges(wire):
    calls, _, _ = wire
    out = asyncio.run(_tool(app_access=False).run({}))
    assert out.content == "done"
    posts = [c for c in calls if c[0] in ("POST", "DELETE")]
    assert [c[2] for c in posts] == ["/run"]
    assert "credential" not in posts[0][3]


def test_scan_carries_app_access_and_refresh_re_registers_on_change(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "_TOOLS_ROOT", tmp_path)
    d = tmp_path / "ledger"
    d.mkdir()
    yml = d / "tool.yaml"
    yml.write_text("name: ledger\ndescription: A test tool with enough description.\n"
                   "params: {type: object, properties: {}}\n")
    (d / "run.py").write_text("print('ok')\n")
    added = []
    monkeypatch.setattr(broker.mcp, "add_tool", added.append)
    monkeypatch.setattr(broker.mcp.local_provider, "remove_tool", lambda name: None)
    monkeypatch.setattr(broker, "_registered", {})
    broker.refresh_custom_tools()
    yml.write_text(yml.read_text() + "app_access: {roles: [results], verbs: [read]}\n")
    broker.refresh_custom_tools()
    broker.refresh_custom_tools()          # unchanged → no churn
    assert [t.app_access for t in added] == [False, True]
