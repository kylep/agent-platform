"""Unit-test the hand-written SDK's request construction and error handling
with an injected transport. The live end-to-end exercise (list agents + trigger
a run with a real key) runs against the deployed platform."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "sdk"))
from agent_platform_sdk import ApiError, Client  # noqa: E402


class FakeFetch:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else []
        self.calls = []

    def __call__(self, method, url, headers, body):
        self.calls.append({"method": method, "url": url, "headers": headers,
                           "body": json.loads(body) if body else None})
        return self.status, json.dumps(self.payload).encode()


def test_list_agents_builds_authed_get():
    f = FakeFetch(payload=[{"name": "echo"}])
    out = Client("http://h/", "ap_tok", fetch=f).list_agents()
    assert out == [{"name": "echo"}]
    call = f.calls[0]
    assert call["method"] == "GET" and call["url"] == "http://h/api/agents"
    assert call["headers"]["Authorization"] == "Bearer ap_tok"


def test_create_run_posts_body():
    f = FakeFetch(payload={"id": "r1", "state": "queued"})
    out = Client("http://h", "ap_tok", fetch=f).create_run("echo", "hi")
    assert out["id"] == "r1"
    call = f.calls[0]
    assert call["method"] == "POST" and call["url"] == "http://h/api/runs"
    assert call["body"] == {"agent": "echo", "prompt": "hi"}


def test_list_runs_encodes_params():
    f = FakeFetch(payload=[])
    Client("http://h", "ap_tok", fetch=f).list_runs(limit=10, tag="smoke")
    assert f.calls[0]["url"] == "http://h/api/runs?limit=10&tag=smoke"


def test_search_memories_omits_none_params():
    f = FakeFetch(payload=[])
    Client("http://h", "ap_tok", fetch=f).search_memories(q="sky")
    # agent is None → dropped from the query string.
    assert f.calls[0]["url"] == "http://h/api/memories?q=sky&limit=50"


def test_error_status_raises():
    f = FakeFetch(status=403, payload={"detail": "nope"})
    with pytest.raises(ApiError) as ei:
        Client("http://h", "ap_tok", fetch=f).create_run("echo", "hi")
    assert ei.value.status == 403


def _matches(concrete: str, template: str) -> bool:
    """A concrete path matches an OpenAPI template if segment counts agree and
    each template segment is either a `{param}` or an exact match."""
    cs, ts = concrete.strip("/").split("/"), template.strip("/").split("/")
    return len(cs) == len(ts) and all(t.startswith("{") or t == c for c, t in zip(cs, ts))


def test_sdk_paths_exist_in_live_openapi():
    """Drift guard: every endpoint the hand-written SDK calls must exist (same
    method + path) in the app's OpenAPI. Rename/remove an API route and this
    fails, so the SDK can't silently go stale."""
    from agentplatform.api.app import create_app
    from agentplatform.config import Settings
    from agentplatform.events import FakeProducer
    spec = create_app(Settings(), None, FakeProducer()).openapi()["paths"]

    f = FakeFetch()
    c = Client("http://h", "ap_tok", fetch=f)
    c.list_agents(); c.get_agent("echo"); c.create_run("echo", "hi"); c.get_run("r1")
    c.list_runs(); c.save_memory("m"); c.search_memories(q="x"); c.kafka_health()

    for call in f.calls:
        path = call["url"][len("http://h"):].split("?")[0]
        method = call["method"].lower()
        assert any(_matches(path, tmpl) and method in ops
                   for tmpl, ops in spec.items()), \
            f"SDK calls {call['method']} {path} — not found in the live OpenAPI (drift)"
