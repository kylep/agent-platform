"""Explicit macOS native compatibility probe, outside the ordinary pytest suite.

Run with --expected-version before accepting a new Codex release. Uses real
Codex, the actual helper hooks, and loopback AP/MCP/Responses fixtures. No real
AP enrollment or model provider is used. Keeps private diagnostics in the
printed temporary directory; creates ordinary native rollout files but never
edits global harness configuration.
"""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ap_persona import capture, harness
from ap_persona.cli import make_invocation
from ap_persona.selftest import verify_fence
from ap_persona.storage import secure_dir, read_json

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--expected-version", required=True)
parser.add_argument("--cwd", type=Path)
args = parser.parse_args()
actual = subprocess.run(
    ["codex", "--version"],
    capture_output=True,
    text=True,
    check=True,
    env=harness.clean_environment(),
    timeout=10,
).stdout.strip()
assert actual == "codex-cli " + args.expected_version, (
    "unexpected native version: " + actual
)
cwd = (
    args.cwd.resolve()
    if args.cwd
    else Path(tempfile.mkdtemp(prefix="ap-persona-work-")).resolve()
)
root = secure_dir(Path(tempfile.mkdtemp(prefix="ap-persona-native-probe-")).resolve())
sid = "b" * 32
state = {
    "agent": "probe",
    "harness": "codex",
    "session_id": sid,
    "writer_epoch": 1,
    "conversation_id": "c" * 32,
    "token": "fixture-only",
    "renewal": "fixture-only",
    "expires_at": "2099-01-01T00:00:00+00:00",
}
phase = "FIRST"
requests_seen = []
events_seen = []
model_calls = 0
hook_bootstraps = 0
reject_bootstrap = False
forbidden_network_calls = 0
command = (
    'test -z "${AP_COMPAT_CANARY+x}" && test -z "${PERSONA_COMPAT_CANARY+x}" && test ! -r '
    + str(root / "protected.txt")
    + " && test ! -w "
    + str(root / "protected.txt")
    + " && echo COMPAT_SANDBOX_PASS"
)
(root / "protected.txt").write_text("disposable synthetic canary")


def bootstrap():
    data = {
        k: state[k]
        for k in ("agent", "harness", "session_id", "writer_epoch", "conversation_id")
    }
    data.update(
        prompt="Fixture persona marker PERSONA_COMPAT_" + phase,
        runtime_instructions="Synthetic data only.",
        memories=[],
        skills=[],
    )
    data["sha256"] = hashlib.sha256(
        json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
    ).hexdigest()
    return data


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, data, status=200):
        raw = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        global hook_bootstraps, forbidden_network_calls
        if self.path == "/forbidden-network":
            forbidden_network_calls += 1
            self.send({"unexpected_network_access": True})
            return
        if self.path.endswith("/bootstrap"):
            hook_bootstraps += 1
            self.send(
                {"detail": "probe rejects bootstrap"}
                if reject_bootstrap
                else bootstrap(),
                403 if reject_bootstrap else 200,
            )
        else:
            self.send({"detail": "no fixture route"}, 404)

    def do_POST(self):
        global model_calls
        body = json.loads(
            self.rfile.read(int(self.headers.get("Content-Length", 0))) or "{}"
        )
        if self.path.startswith("/api/local-persona/"):
            requests_seen.append(self.path.rsplit("/", 1)[-1])
            if self.path.endswith("/events"):
                events_seen.extend(body["events"])
                self.send(
                    {"accepted_sequence": max(e["sequence"] for e in body["events"])}
                )
            else:
                self.send({})
        elif self.path == "/persona/mcp":
            method = body.get("method")
            if "id" not in body:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            value = (
                {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fixture-only", "version": "1"},
                }
                if method == "initialize"
                else {"tools": []}
            )
            self.send({"jsonrpc": "2.0", "id": body["id"], "result": value})
        elif self.path == "/v1/responses":
            model_calls += 1
            encoded = json.dumps(body)
            assert "PERSONA_COMPAT_" + phase in encoded, (
                "current bootstrap absent from model input"
            )
            tool_result = "COMPAT_SANDBOX_PASS" in encoded
            if phase == "FIRST" and not tool_result:
                tools = {
                    t.get("name") for t in body.get("tools", []) if isinstance(t, dict)
                }
                assert "exec_command" in tools, tools
                item = {
                    "id": "fc_fixture",
                    "type": "function_call",
                    "call_id": "call_fixture",
                    "name": "exec_command",
                    "arguments": json.dumps({"cmd": command, "max_output_tokens": 200}),
                }
            else:
                item = {
                    "id": "msg_fixture",
                    "type": "message",
                    "role": "assistant",
                    "phase": "final_answer",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "COMPAT_" + phase + "_DONE",
                            "annotations": [],
                        }
                    ],
                }
            response = {
                "id": "resp_fixture_" + str(model_calls),
                "object": "response",
                "created_at": 1,
                "status": "completed",
                "output": [item],
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
            chunks = [
                (
                    "response.created",
                    {"response": {**response, "status": "in_progress", "output": []}},
                ),
                ("response.output_item.added", {"output_index": 0, "item": item}),
                ("response.output_item.done", {"output_index": 0, "item": item}),
                ("response.completed", {"response": response}),
            ]
            raw = "".join(
                "event: "
                + name
                + "\ndata: "
                + json.dumps({"type": name, **value})
                + "\n\n"
                for name, value in chunks
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self.send({"detail": "no fixture route"}, 404)


server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
origin = "http://127.0.0.1:" + str(server.server_port)
command = command.replace(
    " && echo COMPAT_SANDBOX_PASS",
    " && test -x /usr/bin/curl && ! /usr/bin/curl --max-time 2 -fsS "
    + origin
    + "/forbidden-network >/dev/null 2>&1 && echo COMPAT_SANDBOX_PASS",
)
enrollment = {"origin": origin}
harness.check_project("codex", cwd)
servers = harness.ambient_mcp("codex")
paths = harness.deny_paths(root, cwd)
config = Path.home() / ".codex/config.toml"


def config_fingerprint():
    return (
        (hashlib.sha256(config.read_bytes()).hexdigest(), config.stat().st_mtime_ns)
        if config.exists()
        else None
    )


config_before = config_fingerprint()
env = harness.clean_environment()
env.update(AP_COMPAT_CANARY="FAKE", PERSONA_COMPAT_CANARY="FAKE")


def run(native=None):
    invocation = make_invocation(root, enrollment, state, cwd)
    initial = bootstrap()
    initial["prompt"] = "Static creation-only fixture prompt."
    argv = harness.build("codex", invocation, initial, paths, cwd, servers=servers)
    harness.verify_mcp(argv)
    verify_fence("codex", argv, paths, cwd, invocation)
    argv += [
        "-c",
        'model_provider="persona_probe"',
        "-c",
        'model="fixture-model"',
        "-c",
        "model_providers.persona_probe="
        + harness._toml(
            {
                "name": "Compatibility fixture",
                "base_url": origin + "/v1",
                "wire_api": "responses",
                "requires_openai_auth": False,
                "supports_websockets": False,
            }
        ),
        "-c",
        'model_reasoning_effort="low"',
    ]
    argv += ["exec", "--skip-git-repo-check", "--json"]
    if native:
        argv += ["resume", native]
    argv += ["Synthetic compatibility fixture: perform the fixture checks and finish."]
    result = subprocess.run(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=90,
    )
    (root / ("native-" + phase + ".log")).write_text(
        result.stdout + "\n" + result.stderr
    )
    assert result.returncode == 0, "native execution failed; diagnostic log " + str(
        root / ("native-" + phase + ".log")
    )
    binding = read_json(Path(read_json(invocation)["state_dir"]) / "binding.json")
    capture.refresh_sources(
        read_json(invocation)["state_dir"], "codex", state["writer_epoch"]
    )
    remaining = capture.pending(read_json(invocation)["state_dir"])
    all_events = events_seen + remaining
    if not reject_bootstrap:
        assert any(
            e["kind"] == "assistant_message"
            and e["payload"].get("text") == "COMPAT_" + phase + "_DONE"
            and e["payload"].get("final")
            for e in all_events
        ), "final message not captured"
        assert not any(e["kind"] == "adapter_gap" for e in all_events), (
            "unsupported transcript records"
        )
    return binding["native_session_id"], all_events


try:
    native, captured = run()
    assert any(
        e["kind"] == "tool_result" and "COMPAT_SANDBOX_PASS" in str(e["payload"])
        for e in captured
    ), "sandbox result absent"
    assert any(e["kind"] == "tool_call" for e in captured)
    assert any(e["kind"] == "user_message" for e in captured)
    phase = "RESUME"
    state["writer_epoch"] = 2
    resumed, captured = run(native)
    assert resumed == native
    assert hook_bootstraps >= 3
    phase = "REJECT"
    state["writer_epoch"] = 1
    state["session_id"] = "d" * 32
    reject_bootstrap = True
    prior_calls = model_calls
    run()
    assert model_calls == prior_calls, "rejected bootstrap still reached the model"
    assert forbidden_network_calls == 0, "sandbox shell reached the network"
    assert config_before == config_fingerprint(), "global config was modified"
    print(
        json.dumps(
            {
                "codex": args.expected_version,
                "fixture_model_calls": model_calls,
                "sandbox_env_canary_and_network": "passed",
                "bootstrap_failure_blocks_model": "passed",
                "hooks_and_capture": "passed",
                "resume_bootstrap_refresh": "passed",
                "adapter_gaps": 0,
                "global_config_unchanged": True,
                "real_AP_or_model_contacted": False,
                "diagnostics": str(root),
            }
        )
    )
finally:
    server.shutdown()
