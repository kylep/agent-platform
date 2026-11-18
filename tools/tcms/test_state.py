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


def _case_store(monkeypatch, n_runs=30, linked_in=None):
    """A fake store whose `results_all` would trip the row cap: case reads go
    run by run through `results_for_run`. `linked_in` maps run id -> case ids
    that run has a result for."""
    runs = [{"id": f"run{i:02d}", "version": 1} for i in range(n_runs)]
    cases = [{"id": cid, "version": 1, "key": f"area.{cid}", "title": cid,
              "layer": layer, "priority": "p2", "status": "active",
              "automation": [f"t::{cid}"]}
             for cid, layer in (("c1", "unit"), ("c2", "api"), ("c3", "e2e"),
                                ("m1", "manual"))]
    linked_in = linked_in or {}
    reads = []

    def rows(_base, view, params=None, **_kwargs):
        reads.append((view, (params or {}).get("run")))
        if view == "runs_recent":
            return runs
        if view == "cases_recent":
            return cases
        if view == "results_for_run":
            run = params["run"]
            return [{"id": f"{run}-{cid}", "version": 1, "run": run, "case": cid,
                     "ref": f"t::{cid}", "status": "pass", "duration_ms": 5,
                     "layer": "unit"} for cid in linked_in.get(run, ())]
        raise AssertionError(f"case read the unbounded view {view}")

    monkeypatch.setattr(state, "_rows", rows)
    return reads


def _run_reads(reads):
    return [run for view, run in reads if view == "results_for_run"]


def test_case_shows_its_last_ten_results_and_stops_reading(monkeypatch):
    reads = _case_store(monkeypatch, linked_in={
        f"run{i:02d}": ("c1",) for i in range(0, 30, 2)})
    out = str(state.read("proxy", {"action": "case", "key": "area.c1"}))
    assert out.count("pass: t::c1") == 10
    # The 10th result is in run18, so run19 onward is never read.
    assert _run_reads(reads) == [f"run{i:02d}" for i in range(19)]


def test_case_without_results_reads_each_run_once(monkeypatch):
    reads = _case_store(monkeypatch)
    out = str(state.read("proxy", {"action": "case", "key": "area.c2"}))
    assert "area.c2" in out and "pass:" not in out
    assert _run_reads(reads) == [f"run{i:02d}" for i in range(30)]


def test_unlinked_cases_scan_run_by_run(monkeypatch):
    reads = _case_store(monkeypatch, linked_in={"run03": ("c1",), "run20": ("c3",)})
    out = str(state.read("proxy", {"action": "cases", "unlinked": True}))
    assert "area.c2" in out
    assert "area.c1" not in out and "area.c3" not in out
    assert "area.m1" not in out  # manual cases are never "unlinked"
    assert len(_run_reads(reads)) == 30


def test_unlinked_cases_stop_once_every_case_is_linked(monkeypatch):
    reads = _case_store(monkeypatch, linked_in={
        "run01": ("c1", "c2"), "run04": ("c3",)})
    out = str(state.read("proxy", {"action": "cases", "unlinked": True}))
    assert "area." not in out
    assert _run_reads(reads) == [f"run{i:02d}" for i in range(5)]
