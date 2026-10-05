"""The state writer keeps machine-derived results and stages before completion."""
from pathlib import Path

import ingest
import state


def test_record_results_stages_parsed_rows_before_marking_run_complete(monkeypatch):
    fixture = Path(__file__).parent / "fixtures" / "junit-backend.xml"
    parsed, skipped = ingest.parse_junit_report(fixture.read_bytes())
    monkeypatch.setenv("TOOL_CALLER_AGENT", "qa")
    monkeypatch.setenv("TOOL_RUN_ID", "run-test-state")
    monkeypatch.setattr(state.legacy, "_read_files", lambda *_: [
        (fixture.name, "junit", parsed, skipped)])
    monkeypatch.setattr(state.legacy, "_resolve", lambda rows, repo: (rows, False))
    run = {}
    calls = []

    def rows(_base, view, params=None, **_kwargs):
        if view == "cases_recent":
            return []
        if view == "run_by_runid":
            return [run] if run else []
        if view in ("results_for_run", "coverage_for_run"):
            return []
        raise AssertionError(view)

    def call(_base, action, **body):
        calls.append((action, body))
        if action == "create":
            run.update({"id": "a" * 32, "version": 1, **body["values"]})
        return {}

    staged = []
    monkeypatch.setattr(state, "_rows", rows)
    monkeypatch.setattr(state, "_call", call)
    monkeypatch.setattr(state, "_stage", lambda _base, cols, batches: staged.extend(batches))
    result = state.record_results("proxy", {"commit_sha": "abcdef0"})
    assert result["already_recorded"] is False
    assert result["totals"]["pass"] == len(parsed)
    assert len(staged[0][2]) == len(parsed)
    assert staged[0][:2] == ("results", "insert")
    assert calls[-1] == ("update", {
        "request_id": "tcms-complete:run-test-state", "collection": "runs",
        "id": "a" * 32, "expected_version": 1,
        "values": {"ingest_state": "complete"}})
