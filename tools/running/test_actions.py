"""A weekly note needs fresh Strava data and retries an unfinished report."""
from datetime import date

import pytest

import running_actions as actions


ARGS = {"action": "brief", "body": "Good week", "highlights": ["Consistent"],
        "tags": ["consistency"],
        "_app_data": {"url": "http://127.0.0.1:1234/nonce/api/app-data"}}
TODAY = date(2026, 10, 1)


def test_brief_requires_sync_after_completed_week(monkeypatch):
    def query(base, view, params=None):
        if view == "brief_by_week":
            return []
        if view == "sync_status":
            return [{"values": {"completed_at": "2026-09-28T03:59:00Z"}}]
        raise AssertionError(view)
    monkeypatch.setattr(actions, "_query", query)
    with pytest.raises(ValueError, match="predates the completed week"):
        actions.execute(ARGS, today=TODAY)


def test_new_brief_writes_unposted_then_reports_then_marks_posted(monkeypatch):
    events = []
    def query(base, view, params=None):
        if view == "brief_by_week":
            return []
        if view == "sync_status":
            return [{"values": {"completed_at": "2026-09-29T00:00:00Z"}}]
        if view == "activities_recent":
            return [{"values": {"day": "2026-09-24", "type": "Run",
                                "distance_m": 5000}}]
        raise AssertionError(view)
    def transaction(base, operations):
        events.append(("write", operations[0]))
        return {"results": [{"id": "a" * 32, "version": 1}]}
    monkeypatch.setattr(actions, "_query", query)
    monkeypatch.setattr(actions, "_transaction", transaction)
    monkeypatch.setattr(actions, "_save_report",
                        lambda base, values: events.append(("report", values)))
    result = actions.execute(ARGS, today=TODAY)
    assert result["report"] == "saved"
    assert [kind for kind, _ in events] == ["write", "report", "write"]
    assert events[0][1]["values"]["posted"] is False
    assert events[0][1]["values"]["runs"] == 1
    assert events[2][1]["values"] == {"posted": True}


def test_report_failure_leaves_retryable_unposted_record(monkeypatch):
    events = []
    old = {"id": "a" * 32, "values": {"week_start": "2026-09-21",
           "body": "Good week", "tags": [], "highlights": [],
           "distance_m": 5000, "runs": 1, "posted": False, "version": 1}}
    monkeypatch.setattr(actions, "_query", lambda base, view, params=None: [old])
    monkeypatch.setattr(actions, "_save_report",
                        lambda base, values: (_ for _ in ()).throw(ValueError("report failed")))
    monkeypatch.setattr(actions, "_transaction",
                        lambda base, ops: events.append(ops))
    with pytest.raises(ValueError, match="report failed"):
        actions.execute({**ARGS, "action": "report"}, today=TODAY)
    assert events == []


def test_report_escapes_agent_text():
    body, _ = actions._render_report({"week_start": "2026-09-21",
                                      "body": "<script>alert(1)</script>",
                                      "highlights": ["<img src=x>"],
                                      "tags": [], "distance_m": 1000, "runs": 1})
    assert "<script>" not in body and "<img" not in body
    assert "&lt;script&gt;" in body


def test_recovery_retries_only_two_old_unposted_briefs(monkeypatch):
    rows = [{"id": f"{i:032x}", "values": {"week_start": week, "version": 1,
             "posted": posted}} for i, (week, posted) in enumerate([
                 ("2026-09-07", False), ("2026-09-14", False),
                 ("2026-09-21", False), ("2026-09-28", True)])]
    reports, writes = [], []
    monkeypatch.setattr(actions, "_query", lambda base, view, params=None: rows)
    monkeypatch.setattr(actions, "_save_report",
                        lambda base, values: reports.append(values["week_start"]))
    monkeypatch.setattr(actions, "_transaction",
                        lambda base, ops: writes.append(ops[0]))
    result = actions.execute({**ARGS, "action": "recover_reports"}, today=TODAY)
    assert result == {"recovered": ["2026-09-07", "2026-09-14"], "remaining": 1}
    assert reports == result["recovered"]
    assert len(writes) == 2 and all(op["values"] == {"posted": True} for op in writes)


def test_report_can_retry_an_older_unposted_week(monkeypatch):
    queried = []
    old = {"id": "a" * 32, "values": {"week_start": "2026-09-14",
           "body": "Older week", "tags": [], "highlights": [],
           "distance_m": 5000, "runs": 1, "posted": False, "version": 1}}
    def query(base, view, params=None):
        queried.append((view, params))
        return [old]
    reports = []
    monkeypatch.setattr(actions, "_query", query)
    monkeypatch.setattr(actions, "_save_report",
                        lambda base, values: reports.append(values["week_start"]))
    monkeypatch.setattr(actions, "_transaction",
                        lambda base, ops: {"results": [{"id": old["id"], "version": 2}]})
    result = actions.execute({**ARGS, "action": "report", "week_start": "2026-09-14"},
                             today=TODAY)
    assert result["week_start"] == "2026-09-14"
    assert queried == [("brief_by_week", {"week_start": "2026-09-14"})]
    assert reports == ["2026-09-14"]


@pytest.mark.parametrize("week", ["2026-09-15", "2027-01-04", "2024-01-01", "not-a-date"])
def test_report_rejects_invalid_retry_week(week):
    with pytest.raises(ValueError, match="week_start"):
        actions.execute({**ARGS, "action": "report", "week_start": week}, today=TODAY)
