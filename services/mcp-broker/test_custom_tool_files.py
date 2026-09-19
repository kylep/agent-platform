"""The reserved `files` argument of a custom tool (docs/design/25 "Broker"):
artifact ids in, the executor's `files_in` out.

A file reaches a tool as an ARTIFACT the caller can read: the broker fetches
each id's metadata and bytes through `_request` — the caller's own headers,
so a tool ingests only what its caller may read — and forwards them as
`files_in: [{name, mime, b64}]`, never letting the model carry base64 or a
path. What is worth pinning is the gate (the id shape, the count, the size),
that a refused id never reaches the executor, and that the audit row counts
the bytes without carrying them. The fake API is `test_artifacts_tool.py`'s
(one table for JSON and byte routes); the fake executor records the one
`/run` it is sent.

    cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q test_custom_tool_files.py
"""
import asyncio
import base64
import json

import httpx
import pytest

from test_artifacts_tool import ARTIFACTS, FakeResponse
from test_relay_tool import Calls, broker, caller, published  # noqa: F401

JUNIT_ID = "a1" * 16
PW_ID = "b2" * 16
JUNIT = b"<testsuites/>" + b"j" * 50
PW = b'{"suites": []}' + b"p" * 20


def _row(artifact_id, name, mime, size):
    return {"id": artifact_id, "name": name, "mime": mime, "size": size, "kind": "file",
            "owner": "agent:qa", "run_id": "r1", "source": "upload", "meta": {},
            "tags": ["tcms"], "created_at": "2026-09-19T01:00:00+00:00", "deleted_at": None,
            "thumb_url": None, "content_url": f"{ARTIFACTS}/{artifact_id}/content"}


@pytest.fixture
def api(monkeypatch):
    """The fake platform API: two uploaded result files, readable."""
    recorded = Calls()
    recorded.replies[f"{ARTIFACTS}/{JUNIT_ID}"] = (
        200, json.dumps(_row(JUNIT_ID, "junit.xml", "text/xml", len(JUNIT))))
    recorded.replies[f"{ARTIFACTS}/{JUNIT_ID}/content"] = (200, JUNIT, "text/xml")
    recorded.replies[f"{ARTIFACTS}/{PW_ID}"] = (
        200, json.dumps(_row(PW_ID, "playwright.json", "application/json", len(PW))))
    recorded.replies[f"{ARTIFACTS}/{PW_ID}/content"] = (200, PW, "application/json")

    async def _request(method, path, params=None, json=None, timeout=20):
        recorded.append((method, path, params, json))
        reply = recorded.replies.get(path, (404, '{"detail": "unknown artifact"}'))
        return FakeResponse(*reply)

    monkeypatch.setattr(broker, "_request", _request)
    return recorded


@pytest.fixture
def executor(monkeypatch):
    """The fake tool-executor: records every `/run` body, answers ok."""
    posted = []

    class Client:
        def __init__(self, base_url=None, timeout=None):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, path, json=None):
            posted.append((path, json))
            return httpx.Response(200, json={"ok": True, "output": "recorded 2 files"})

    monkeypatch.setattr(broker.httpx, "AsyncClient", Client)
    return posted


def run(**args):
    tool = broker.CustomTool(name="tcms", description="The test case management tool.",
                             parameters={"type": "object", "properties": {}})
    out = asyncio.run(tool.run(args))
    return out.content


def test_two_ids_are_fetched_and_forwarded_as_files_in(api, executor):
    assert run(action="record_results", files=[JUNIT_ID, PW_ID]) == "recorded 2 files"
    assert [c[1] for c in api] == [
        f"{ARTIFACTS}/{JUNIT_ID}", f"{ARTIFACTS}/{JUNIT_ID}/content",
        f"{ARTIFACTS}/{PW_ID}", f"{ARTIFACTS}/{PW_ID}/content"]
    assert len(executor) == 1
    body = executor[0][1]
    assert body["files_in"] == [
        {"name": "junit.xml", "mime": "text/xml", "b64": base64.b64encode(JUNIT).decode()},
        {"name": "playwright.json", "mime": "application/json",
         "b64": base64.b64encode(PW).decode()}]
    # The executor validates args against the manifest; `files` was the
    # broker's to consume, not the tool's to see.
    assert body["args"] == {"action": "record_results"}
    assert body["tool"] == "tcms"


def test_no_files_argument_sends_no_files_in(api, executor):
    run(action="flaky")
    assert api == []
    assert "files_in" not in executor[0][1]


@pytest.mark.parametrize("bad", ["../etc/passwd", "A1" * 16, "a1" * 15, 7, "",
                                 f"[[artifact:{JUNIT_ID}]]"])
def test_an_id_that_is_not_32_hex_is_refused_before_any_call(api, executor, bad):
    out = run(action="record_results", files=[JUNIT_ID, bad])
    assert out.startswith("error:") and "32 hex" in out
    assert api == [] and executor == []


@pytest.mark.parametrize("files", [JUNIT_ID, {"id": JUNIT_ID}, "junit.xml,playwright.json"])
def test_files_must_be_a_list(api, executor, files):
    out = run(action="record_results", files=files)
    assert out.startswith("error:") and "list" in out
    assert api == [] and executor == []


def test_a_fifth_id_is_refused_before_any_call(api, executor):
    five = [f"{i:02x}" * 16 for i in range(5)]
    out = run(action="record_results", files=five)
    assert out.startswith("error:") and "4" in out
    assert api == [] and executor == []


def test_a_repeated_id_is_fetched_once(api, executor):
    run(action="record_results", files=[JUNIT_ID, JUNIT_ID])
    assert [c[1] for c in api] == [f"{ARTIFACTS}/{JUNIT_ID}", f"{ARTIFACTS}/{JUNIT_ID}/content"]
    assert [f["name"] for f in executor[0][1]["files_in"]] == ["junit.xml"]


def test_an_unreadable_id_is_the_404_in_words_and_no_executor_call(api, executor):
    ghost = "ee" * 16
    out = run(action="record_results", files=[JUNIT_ID, ghost])
    assert out == f"error: artifact {ghost} not found or not readable"
    assert executor == []


def test_an_artifact_over_the_cap_is_refused_by_its_row_before_the_bytes(api, executor):
    api.replies[f"{ARTIFACTS}/{PW_ID}"] = (
        200, json.dumps(_row(PW_ID, "huge.json", "application/json", 8 * 1024 * 1024 + 1)))
    out = run(action="record_results", files=[PW_ID])
    assert out.startswith("error:") and PW_ID in out and "8" in out
    assert [c[1] for c in api] == [f"{ARTIFACTS}/{PW_ID}"]
    assert executor == []


def test_the_bytes_that_arrive_are_judged_too(api, executor, monkeypatch):
    """The row's `size` is what the store claims; the cap is on what came."""
    monkeypatch.setattr(broker, "FILE_BYTES_MAX", 40)
    out = run(action="record_results", files=[JUNIT_ID])
    assert out.startswith("error:") and JUNIT_ID in out
    assert executor == []


def test_two_artifacts_with_one_name_would_overwrite_each_other_so_it_is_an_error(api, executor):
    api.replies[f"{ARTIFACTS}/{PW_ID}"] = (
        200, json.dumps(_row(PW_ID, "junit.xml", "text/xml", len(PW))))
    out = run(action="record_results", files=[JUNIT_ID, PW_ID])
    assert out.startswith("error:") and "junit.xml" in out
    assert executor == []


@pytest.mark.parametrize("name, want", [("../../etc/passwd", "passwd"),
                                        ("reports/junit.xml", "junit.xml"),
                                        ("", JUNIT_ID), ("..", JUNIT_ID),
                                        ("a\x00b", JUNIT_ID), ("x" * 201, JUNIT_ID)])
def test_a_name_reaches_the_executor_as_a_plain_filename(api, executor, name, want):
    """The executor refuses a name with a separator in it; a row's name is a
    label, so it goes in as the basename, and the id stands in for none."""
    api.replies[f"{ARTIFACTS}/{JUNIT_ID}"] = (
        200, json.dumps(_row(JUNIT_ID, name, "text/xml", len(JUNIT))))
    run(action="record_results", files=[JUNIT_ID])
    assert executor[0][1]["files_in"][0]["name"] == want


def test_another_refusal_is_passed_back_in_plain_words(api, executor):
    api.replies[f"{ARTIFACTS}/{JUNIT_ID}"] = (
        403, '{"detail": "this agent is not granted the artifacts tool"}')
    out = run(action="record_results", files=[JUNIT_ID])
    assert out == "error: this agent is not granted the artifacts tool"
    assert executor == []


def test_the_audit_row_carries_the_byte_total_and_never_the_bytes(api, executor, published):
    run(action="record_results", files=[JUNIT_ID, PW_ID])
    assert len(published) == 1
    envelope = published[0][1]
    data = envelope["data"]
    assert (data["tool"], data["decision"]) == ("tcms", "allow")
    assert data["files_bytes"] == len(JUNIT) + len(PW)
    text = json.dumps(envelope)
    assert base64.b64encode(JUNIT).decode() not in text
    assert JUNIT.decode() not in text


def test_a_refused_files_argument_is_audited_as_an_error(api, executor, published):
    run(action="record_results", files=["nope"])
    assert published[-1][1]["data"]["decision"] == "error:files"
    assert published[-1][1]["data"]["files_bytes"] == 0


def test_a_call_without_files_records_zero_bytes(api, executor, published):
    run(action="flaky")
    assert published[-1][1]["data"]["files_bytes"] == 0


def test_an_api_outage_during_a_fetch_is_words_and_an_audit_row(api, executor, published,
                                                                 monkeypatch):
    """The same hole `_guarded` closes: the API pod restarting must not escape
    as a raw MCP exception with no record of the attempt."""
    async def _request(method, path, params=None, json=None, timeout=20):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(broker, "_request", _request)
    out = run(action="record_results", files=[JUNIT_ID])
    assert out.startswith("error: the platform API is unreachable")
    assert executor == []
    assert published[-1][1]["data"]["decision"] == "error:api-unreachable"
