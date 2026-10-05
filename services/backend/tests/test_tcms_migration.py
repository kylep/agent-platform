from datetime import datetime, timezone

from agentplatform.appdata.definitions import _fits_field, validate_app
from agentplatform.appdata.tcms_migration import bundle, convert_snapshot, record_id


def test_reviewed_tcms_definition_validates():
    app = validate_app(bundle())
    assert app.collections["results"].fields["run"].collection == "runs"
    suites = app.collections["runs"]
    assert _fits_field(suites, "suites", [{"name": "api", "exit": None,
                                          "seconds": 1.0}], bounds=True)
    assert not _fits_field(suites, "suites", [{"name": "api", "exit": "bad",
                                              "seconds": 1.0}], bounds=True)


def test_snapshot_preserves_case_run_result_links_and_totals():
    now = datetime.now(timezone.utc)
    source = {
        "cases": [{"key": "QA-1", "suite": "api", "area": "auth", "title": "logs in",
                   "layer": "integration", "priority": "P1", "preconditions": [],
                   "steps": ["submit"], "expected": "ok", "automation": ["test_login"],
                   "tags": [], "tickets": [], "status": "active", "synced_at": now,
                   "source_sha": "abc"}],
        "test_runs": [{"id": 3, "commit_sha": "abcdef0", "branch": "main",
                       "run_id": "run-3", "agent": "qa", "started_at": now,
                       "finished_at": now, "verify_ok": True,
                       "suites": [{"name": "api", "exit": 0, "seconds": 1.2}],
                       "published_at": None}],
        "results": [{"id": 9, "test_run_id": 3, "ref": "test_login", "case_key": "QA-1",
                     "status": "pass", "duration_ms": 100, "message": "",
                     "layer": "integration"}],
        "coverage_snapshots": [{"id": 5, "test_run_id": 3, "package": "api",
                                "lines_covered": 9, "lines_total": 10,
                                "branch_rate": 0.5}],
    }
    out = convert_snapshot(source)
    run = out["runs"][0]
    assert run.id == record_id("run", 3)
    assert run.doc["result_count"] == run.doc["pass_count"] == 1
    assert out["results"][0].doc["run"] == run.id
    assert out["results"][0].doc["case"] == out["cases"][0].id
    assert out["coverage"][0].doc["run"] == run.id
