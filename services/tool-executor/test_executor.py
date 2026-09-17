"""Executor invariants (docs/design/12): the subprocess sees a minimal env
(never this process's), schema violations are rejected before any execution,
timeouts kill, and failures come back structured for the model.

The file sink (docs/design/23): a per-call scratch dir with `in/` (the
caller's `files_in`) and `out/` (whatever the tool writes comes back as
`files`), capped, mime-sniffed, and gone again after the call."""
import base64
import json
import os

import pytest
from fastapi.testclient import TestClient

import executor


@pytest.fixture
def tools_root(tmp_path, monkeypatch):
    monkeypatch.setattr(executor, "TOOLS_ROOT", tmp_path)
    return tmp_path


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Where per-call dirs are made — a fresh base so a test can assert that
    nothing survives the call."""
    base = tmp_path / "scratch"
    base.mkdir()
    monkeypatch.setattr(executor, "SCRATCH_DIR", base)
    return base


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
                            "TOOL_CALLER_AGENT", "TOOL_RUN_ID", "LC_CTYPE", "PWD",
                            "TOOL_IN_DIR", "TOOL_OUT_DIR"}
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


# --- file sink (docs/design/23) ----------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 16
B64_PNG = base64.b64encode(PNG).decode()

# Reads every file in TOOL_IN_DIR and echoes name → text on stdout.
READ_IN = (
    "import json, os, sys\n"
    "d = os.environ['TOOL_IN_DIR']\n"
    "print(json.dumps({n: open(os.path.join(d, n), 'rb').read().decode()\n"
    "                  for n in sorted(os.listdir(d))}))\n")


def _run(root, run_py, files_in=None, yaml_extra=""):
    if not (root / "envdump").is_dir():
        make_tool(root, run_py=run_py, yaml_extra=yaml_extra)
    body = {"tool": "envdump", "args": {}}
    if files_in is not None:
        body["files_in"] = files_in
    return TestClient(executor.app).post("/run", json=body)


def test_files_in_are_readable_by_the_tool(tools_root, scratch):
    r = _run(tools_root, READ_IN, files_in=[
        {"name": "a.txt", "mime": "text/plain", "b64": base64.b64encode(b"hello").decode()},
        {"name": "b.txt", "mime": "text/plain", "b64": base64.b64encode(b"world").decode()}])
    body = r.json()
    assert body["ok"], body
    assert json.loads(body["output"]) == {"a.txt": "hello", "b.txt": "world"}


def test_files_in_names_are_basenames_only(tools_root, scratch):
    for bad in ("../x", "sub/x", "", "..", "/etc/passwd"):
        r = _run(tools_root, READ_IN, files_in=[{"name": bad, "mime": "", "b64": "aGk="}])
        assert r.status_code == 400, (bad, r.text)
    assert scratch.exists() and not list(scratch.iterdir())


def test_files_in_caps(tools_root, scratch):
    five = [{"name": f"{i}.txt", "mime": "", "b64": "aGk="} for i in range(5)]
    assert _run(tools_root, READ_IN, files_in=five).status_code == 400
    big = base64.b64encode(b"\0" * (8 * 1024 * 1024 + 1)).decode()
    r = _run(tools_root, READ_IN, files_in=[{"name": "big.bin", "mime": "", "b64": big}])
    assert r.status_code == 400
    assert not list(scratch.iterdir())


def test_files_out_come_back_sniffed_with_merged_sidecar(tools_root, scratch):
    body = _run(tools_root,
        "import os, json\n"
        "d = os.environ['TOOL_OUT_DIR']\n"
        "open(os.path.join(d, 'pic.png'), 'wb').write(%r)\n"
        "json.dump({'prompt': 'a cat', 'seed': 7},"
        " open(os.path.join(d, 'pic.png.meta.json'), 'w'))\n"
        "open(os.path.join(d, 'raw.bin'), 'wb').write(b'\\x00\\x01')\n"
        "print('done')\n" % PNG).json()
    assert body["ok"], body
    assert body["output"].strip() == "done"
    assert body["warnings"] == []
    files = {f["name"]: f for f in body["files"]}
    # The sidecar is folded into its file's meta, never returned on its own.
    assert set(files) == {"pic.png", "raw.bin"}
    assert files["pic.png"]["mime"] == "image/png"
    assert files["pic.png"]["b64"] == B64_PNG
    assert files["pic.png"]["meta"] == {"prompt": "a cat", "seed": 7}
    assert files["raw.bin"]["mime"] == "application/octet-stream"
    assert files["raw.bin"]["meta"] == {}


def test_files_out_caps_skip_with_warning(tools_root, scratch):
    body = _run(tools_root,
        "import os\n"
        "d = os.environ['TOOL_OUT_DIR']\n"
        "open(os.path.join(d, 'big.bin'), 'wb').write(b'x' * (8 * 1024 * 1024 + 1))\n"
        "for i in range(9): open(os.path.join(d, 'f%02d.txt' % i), 'w').write('ok')\n"
        "os.mkdir(os.path.join(d, 'sub'))\n").json()
    assert body["ok"], body
    names = [f["name"] for f in body["files"]]
    assert "big.bin" not in names
    assert len(names) == 8 and names == [f"f{i:02d}.txt" for i in range(8)]
    assert any("big.bin" in w for w in body["warnings"])
    assert any("f08.txt" in w for w in body["warnings"])
    assert any("sub" in w for w in body["warnings"])


def test_tool_that_writes_nothing_returns_empty_files(tools_root, scratch):
    body = _run(tools_root, "print('hi')\n").json()
    assert body["ok"] and body["files"] == [] and body["warnings"] == []


def test_scratch_dir_is_removed_after_every_outcome(tools_root, scratch):
    _run(tools_root, "import os; open(os.path.join(os.environ['TOOL_OUT_DIR'], 'a'), 'w')\n",
         files_in=[{"name": "in.txt", "mime": "", "b64": "aGk="}])
    assert not list(scratch.iterdir())
    make_tool(tools_root, name="slow", run_py="import time; time.sleep(60)\n",
              yaml_extra="timeout_seconds: 1\n")
    make_tool(tools_root, name="bad", run_py="import sys; sys.exit(2)\n")
    c = TestClient(executor.app)
    assert "timed out" in c.post("/run", json={"tool": "slow", "args": {}}).json()["error"]
    assert "exited 2" in c.post("/run", json={"tool": "bad", "args": {}}).json()["error"]
    assert not list(scratch.iterdir())


def test_timeout_ceiling_is_300s():
    assert executor.effective_timeout({"timeout_seconds": 900}) == 300
    assert executor.effective_timeout({"timeout_seconds": 180}) == 180
    assert executor.effective_timeout({}) == 30


def test_mime_sniffing_by_magic_bytes():
    assert executor.sniff_mime(PNG) == "image/png"
    assert executor.sniff_mime(b"\xff\xd8\xff\xe0rest") == "image/jpeg"
    assert executor.sniff_mime(b"GIF89a....") == "image/gif"
    assert executor.sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert executor.sniff_mime(b"RIFF\x00\x00\x00\x00WAVE") == "application/octet-stream"
    assert executor.sniff_mime(b"") == "application/octet-stream"


# --- review round: hostile tools and hostile callers --------------------------

def _write_out(script_body):
    return ("import os, json, sys\n"
            "d = os.environ['TOOL_OUT_DIR']\n" + script_body)


def test_symlinked_sidecar_is_not_read(tools_root, scratch, tmp_path):
    secret = tmp_path / "secret.json"
    secret.write_text('{"leak": "yes"}')
    body = _run(tools_root, _write_out(
        "open(os.path.join(d, 'pic.png'), 'wb').write(b'\\x89PNG')\n"
        f"os.symlink({str(secret)!r}, os.path.join(d, 'pic.png.meta.json'))\n")).json()
    assert body["ok"], body
    files = {f["name"]: f for f in body["files"]}
    assert files["pic.png"]["meta"] == {}
    assert "leak" not in json.dumps(body)
    assert any("pic.png.meta.json" in w for w in body["warnings"])


def test_oversized_sidecar_is_dropped_with_warning(tools_root, scratch):
    body = _run(tools_root, _write_out(
        "open(os.path.join(d, 'pic.png'), 'wb').write(b'\\x89PNG')\n"
        "open(os.path.join(d, 'pic.png.meta.json'), 'w').write("
        "'{\"pad\": \"' + 'x' * (8 * 1024 * 1024 + 1) + '\"}')\n")).json()
    assert body["ok"], body
    files = {f["name"]: f for f in body["files"]}
    assert files["pic.png"]["meta"] == {}
    assert any("pic.png.meta.json" in w for w in body["warnings"])


def test_files_in_oversized_b64_is_rejected_before_decoding(tools_root, scratch, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("decoded an over-cap payload")
    monkeypatch.setattr(executor.base64, "b64decode", boom)
    big = "A" * (executor.FILE_CAP * 4 // 3 + 5)
    r = _run(tools_root, READ_IN, files_in=[{"name": "big.bin", "mime": "", "b64": big}])
    assert r.status_code == 400
    assert not list(scratch.iterdir())


def test_request_body_ceiling(tools_root, scratch, monkeypatch):
    # Shrink the ceiling so the test does not have to build a 44 MiB body.
    monkeypatch.setattr(executor, "BODY_CAP", 4096)
    make_tool(tools_root, run_py="print('ok')\n")
    c = TestClient(executor.app)
    payload = json.dumps({"tool": "envdump", "args": {},
                          "files_in": [{"name": "a", "mime": "", "b64": "A" * 8000}]})
    r = c.post("/run", content=payload, headers={"content-type": "application/json"})
    assert r.status_code == 413

    def chunks():
        for i in range(0, len(payload), 1000):
            yield payload[i:i + 1000].encode()
    r = c.post("/run", content=chunks(), headers={"content-type": "application/json",
                                                  "transfer-encoding": "chunked"})
    assert r.status_code == 413
    small = json.dumps({"tool": "envdump", "args": {}})
    assert c.post("/run", content=small, headers={"content-type": "application/json"}).json()["ok"]
    assert not list(scratch.iterdir())


def test_timeout_kills_the_whole_process_group(tools_root, scratch, tmp_path):
    import time
    pid_file = tmp_path / "grandchild.pid"
    make_tool(tools_root, run_py=(
        "import os, sys, time\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    while True:\n"
        "        open(os.path.join(os.environ['TOOL_OUT_DIR'], 'late'), 'a').write('x')\n"
        "        time.sleep(0.05)\n"
        f"open({str(pid_file)!r}, 'w').write(str(pid))\n"
        "time.sleep(60)\n"), yaml_extra="timeout_seconds: 1\n")
    body = TestClient(executor.app).post("/run", json={"tool": "envdump", "args": {}}).json()
    assert "timed out" in body["error"]
    pid = int(pid_file.read_text())
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert not list(scratch.iterdir())


def test_files_in_rejects_nul_and_overlong_names(tools_root, scratch):
    for bad in ("a\x00b", "x" * 201):
        r = _run(tools_root, READ_IN, files_in=[{"name": bad, "mime": "", "b64": "aGk="}])
        assert r.status_code == 400, (bad[:10], r.status_code)
    assert not list(scratch.iterdir())


def test_body_cap_counts_chunks_without_a_content_length(monkeypatch):
    """Driven at the ASGI layer so no client can quietly add a Content-Length:
    the middleware must stop a chunked stream on the byte count alone, and hand
    a within-cap stream to the app intact."""
    import asyncio
    monkeypatch.setattr(executor, "BODY_CAP", 100)
    seen = {}

    async def app(scope, receive, send):
        seen["body"] = (await receive())["body"]
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    def drive(chunks):
        sent = []
        msgs = [{"type": "http.request", "body": c, "more_body": i < len(chunks) - 1}
                for i, c in enumerate(chunks)] + [{"type": "http.disconnect"}]

        async def receive():
            return msgs.pop(0)

        async def send(m):
            sent.append(m)
        asyncio.run(executor.BodyCap(app)({"type": "http", "headers": []}, receive, send))
        return sent[0]["status"]

    assert drive([b"x" * 60, b"y" * 60]) == 413
    assert "body" not in seen
    assert drive([b"x" * 30, b"y" * 30]) == 200
    assert seen["body"] == b"x" * 30 + b"y" * 30
