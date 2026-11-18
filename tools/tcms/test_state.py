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


def _report_store(monkeypatch, n_runs=30, per_run=4):
    """A fake store whose `results_all` would trip the row cap: the report
    actions must read only the windowed runs through `results_for_run`."""
    runs = [{"id": f"run{i:02d}", "version": 1, "commit_sha": f"{i:040x}"}
            for i in range(n_runs)]
    reads = []

    def rows(_base, view, params=None, **_kwargs):
        reads.append((view, (params or {}).get("run")))
        if view == "runs_recent":
            return runs
        if view == "cases_recent":
            return []
        if view == "results_for_run":
            run = params["run"]
            return [{"id": f"{run}-{k}", "version": 1, "run": run, "ref": f"t::{k}",
                     "case": None, "status": "fail" if (run == "run01" and k == 0) else "pass",
                     "duration_ms": 1000, "layer": "unit"} for k in range(per_run)]
        raise AssertionError(f"report read the unbounded view {view}")

    monkeypatch.setattr(state, "_rows", rows)
    return reads


def test_report_window_reads_only_the_requested_runs(monkeypatch):
    reads = _report_store(monkeypatch)
    runs, results = state._window("proxy", {"runs": 5}, 20)
    assert [r["id"] for r in runs] == [f"run{i:02d}" for i in range(5)]
    assert {r["run"] for r in results} == {r["id"] for r in runs}
    assert len(results) == 5 * 4
    assert [run for view, run in reads if view == "results_for_run"] == [
        r["id"] for r in runs]


def test_flaky_runtime_and_prune_never_scan_every_result(monkeypatch):
    reads = _report_store(monkeypatch)
    flaky = state.read("proxy", {"action": "flaky", "runs": 5})
    assert "t::0" in str(flaky)
    for action in ("runtime_report", "prune_candidates"):
        state.read("proxy", {"action": action})
    assert not any(view == "results_all" for view, _ in reads)
