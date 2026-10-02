"""The executor's half of tool-call credentials (docs/design/39).

When the broker sends a credential with a run, the executor opens a per-call
endpoint on localhost and gives the tool only its URL. The endpoint forwards
`/api/app-data/**` and nothing else to the platform API, attaching the
credential and this pod's ServiceAccount token, and it is gone when the call
returns. The credential never reaches the tool's environment.
"""
import json
import socket

import httpx
import pytest
from fastapi.testclient import TestClient

import executor

CRED = {"token": "cred.header.sig", "call_id": "c" * 32}

PROBE = r'''
import json, os, sys, urllib.request, urllib.error
args = json.load(sys.stdin)
base = os.environ["TOOL_APP_DATA_URL"]
root = base[:-len("/api/app-data")]
port = base.split(":")[2].split("/")[0]
def call(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return [r.status, r.read().decode()]
    except urllib.error.HTTPError as e:
        return [e.code, e.read().decode()]
out = {"url": base, "env": dict(os.environ)}
out["ok"] = call(base + "/apps/a1/records/results", "POST", b'{"title": "x"}',
                 {"Content-Type": "application/json", "Authorization": "Bearer stolen",
                  "Cookie": "ap_session=x", "X-AP-Tool-Call": "forged"})
out["query"] = call(base + "/apps/a1/query?limit=2")
out["other"] = call(root + "/api/agents")
out["traverse"] = call(root + "/api/app-data/../agents")
out["encoded"] = call(base + "/%2e%2e/agents")
out["prefix_lookalike"] = call(root + "/api/app-dataX")
out["no_nonce"] = call("http://127.0.0.1:" + port + "/api/app-data/apps")
out["wrong_nonce"] = call("http://127.0.0.1:" + port + "/guess/api/app-data/apps")
if args.get("x") == "fail":
    print(json.dumps(out)); sys.exit(3)
print(json.dumps(out))
'''


@pytest.fixture
def tools_root(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "TOOLS_ROOT", tmp_path / "tools")
    (tmp_path / "tools").mkdir()
    return tmp_path / "tools"


@pytest.fixture
def api(tmp_path, monkeypatch):
    """The platform API, as the proxy sees it."""
    seen = []

    def handle(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"path": request.url.path})

    identity = tmp_path / "token"
    identity.write_text("executor.sa.token\n")
    monkeypatch.setattr(executor, "IDENTITY_FILE", identity)
    monkeypatch.setattr(executor, "_api_transport", httpx.MockTransport(handle))
    return seen


def _probe_tool(root, name="probe"):
    d = root / name
    d.mkdir()
    (d / "tool.yaml").write_text(
        f"name: {name}\ndescription: Probes the per-call app-data endpoint.\n"
        "params:\n  type: object\n  properties:\n    x: {type: string}\n")
    (d / "run.py").write_text(PROBE)


def _run(args=None, credential=CRED):
    body = {"tool": "probe", "args": args or {}, "caller": {"agent": "pai", "run_id": "r1"}}
    if credential:
        body["credential"] = credential
    return TestClient(executor.app).post("/run", json=body).json()


def _refused(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
    except OSError:
        return True
    return False


def test_proxy_forwards_only_app_data_with_the_credential_attached(tools_root, api):
    _probe_tool(tools_root)
    body = _run()
    assert body["ok"], body
    out = json.loads(body["output"])
    assert out["ok"][0] == 200 and json.loads(out["ok"][1]) == {
        "path": "/api/app-data/apps/a1/records/results"}
    assert out["query"][0] == 200
    for probe in ("other", "traverse", "encoded", "prefix_lookalike"):
        assert out[probe][0] == 403, (probe, out[probe])
    assert out["no_nonce"][0] == 404 and out["wrong_nonce"][0] == 404
    # Exactly the two app-data requests reached the API.
    assert [(r.method, r.url.path) for r in api] == [
        ("POST", "/api/app-data/apps/a1/records/results"),
        ("GET", "/api/app-data/apps/a1/query")]
    assert api[1].url.query == b"limit=2"
    first = api[0]
    assert first.headers["authorization"] == "Bearer executor.sa.token"
    assert first.headers["x-ap-tool-call"] == CRED["token"]
    assert first.headers["x-ap-tool-call-id"] == CRED["call_id"]
    assert first.headers["content-type"] == "application/json"
    assert "cookie" not in first.headers
    assert json.loads(first.content) == {"title": "x"}


def test_tool_env_has_the_endpoint_never_the_credential(tools_root, api):
    _probe_tool(tools_root)
    env = json.loads(_run()["output"])["env"]
    assert env["TOOL_APP_DATA_URL"].startswith("http://127.0.0.1:")
    blob = json.dumps(env)
    assert CRED["token"] not in blob and "executor.sa.token" not in blob
    assert CRED["call_id"] not in blob
    # The minimal-env canary (test_executor.py) covers the rest of the set.
    assert {k for k in env if k.startswith(("TOOL_", "AP_"))} == {
        "TOOL_NAME", "TOOL_CALLER_AGENT", "TOOL_RUN_ID", "TOOL_IN_DIR", "TOOL_OUT_DIR",
        "TOOL_APP_DATA_URL"}


def test_no_credential_no_endpoint(tools_root, api):
    d = tools_root / "envdump"
    d.mkdir()
    (d / "tool.yaml").write_text("name: envdump\ndescription: Dumps its environment for tests.\n")
    (d / "run.py").write_text("import json, os\nprint(json.dumps(dict(os.environ)))\n")
    body = TestClient(executor.app).post("/run", json={"tool": "envdump"}).json()
    assert "TOOL_APP_DATA_URL" not in json.loads(body["output"])


@pytest.mark.parametrize("args", [{}, {"x": "fail"}])
def test_endpoint_dies_at_return(tools_root, api, args):
    _probe_tool(tools_root)
    body = _run(args)
    output = body["output"] if body["ok"] else body["error"].split(": ", 1)[1]
    url = json.loads(output)["url"]
    port = int(url.split(":")[2].split("/")[0])
    assert _refused(port)


def test_endpoint_dies_on_timeout(tools_root, api, tmp_path):
    d = tools_root / "probe"
    d.mkdir()
    (d / "tool.yaml").write_text("name: probe\ndescription: Hangs after noting its endpoint.\n"
                                 "timeout_seconds: 1\n")
    note = tmp_path / "url"
    (d / "run.py").write_text(
        f"import os, time\nopen({str(note)!r}, 'w').write(os.environ['TOOL_APP_DATA_URL'])\n"
        "time.sleep(30)\n")
    body = _run()
    assert not body["ok"] and "timed out" in body["error"]
    port = int(note.read_text().split(":")[2].split("/")[0])
    assert _refused(port)


def test_a_closed_endpoint_refuses_a_request_already_in_flight(api):
    """A connection accepted just before return gets 410, never a forward."""
    import asyncio

    async def scenario():
        proxy = executor.AppDataProxy(executor.CallCredential(**CRED))
        await proxy.start()
        reader, writer = await asyncio.open_connection("127.0.0.1", proxy.port)
        proxy.live = False          # the call returned while this request was arriving
        writer.write(f"GET /{proxy.nonce}/api/app-data/apps HTTP/1.1\r\n"
                     "Host: x\r\n\r\n".encode())
        await writer.drain()
        head = await reader.readline()
        writer.close()
        await proxy.close()
        return head

    assert b" 410 " in asyncio.run(scenario())
    assert api == []
