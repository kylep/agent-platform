"""A partial Strava copy never claims a successful completed sync."""
import pytest

import state_sync


URL = {"_app_data": {"url": "http://127.0.0.1:9000/nonce/api/app-data"}}
ACTIVITY = {"id": 123456, "date": "2026-09-30", "name": "Morning run",
            "type": "Run", "distance_m": 5000, "moving_time_s": 1800,
            "elevation_m": 5, "avg_hr": 140, "max_hr": 165}


def test_state_sync_creates_activity_before_freshness_receipt(monkeypatch):
    writes = []
    def fake_post(base, route, body, **kwargs):
        if route.endswith("describe"):
            return {"status": "active"}
        if route.endswith("query"):
            return {"rows": [], "next_cursor": None}
        writes.append(body["operations"])
        return {"results": []}
    monkeypatch.setattr(state_sync, "_post", fake_post)
    assert state_sync.sync(URL, [ACTIVITY], "2026-09-27")
    assert writes[0][0]["collection"] == "activities"
    assert writes[0][0]["values"]["strava_id"] == "123456"
    assert writes[1][0]["collection"] == "sync_state"
    assert writes[1][0]["values"]["count"] == 1


def test_failure_does_not_record_completed_sync(monkeypatch):
    writes = []
    def fake_post(base, route, body, **kwargs):
        if route.endswith("describe"):
            return {"status": "active"}
        if route.endswith("query"):
            return {"rows": [], "next_cursor": None}
        writes.append(body["operations"])
        raise ValueError("write failed")
    monkeypatch.setattr(state_sync, "_post", fake_post)
    with pytest.raises(ValueError, match="write failed"):
        state_sync.sync(URL, [ACTIVITY], None)
    assert len(writes) == 1
    assert writes[0][0]["collection"] == "activities"


def test_only_missing_state_app_uses_legacy_conduit(monkeypatch):
    monkeypatch.setattr(state_sync, "_post", lambda *args, **kwargs: None)
    assert state_sync.sync(URL, [ACTIVITY], None) is False
