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
