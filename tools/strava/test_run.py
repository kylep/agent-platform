"""Pure-function tests (no network, no DB). CI runs these against baked deps.

The token cache and HTTP paths need Postgres/Strava, so they're not unit
tested here; what's testable in isolation is the formatting and the action
dispatch (with _get monkeypatched to canned API payloads).
"""
import run


def test_km_and_duration_and_pace():
    assert run._km(5000) == 5.0
    assert run._km(None) is None
    assert run._dur(3661) == "1:01:01"
    assert run._dur(125) == "2:05"
    assert run._dur(None) is None
    # 5 km in 25:00 → 5:00/km
    assert run._pace(5000, 1500) == "5:00/km"
    assert run._pace(0, 100) is None


def test_activity_row_pace_only_for_foot_sports():
    ride = run._activity_row({"type": "Ride", "distance": 20000,
                              "moving_time": 3600, "start_date_local": "2026-08-10T07:00:00Z",
                              "name": "Morning spin"})
    assert ride["distance_km"] == 20.0 and ride["pace"] is None
    runrow = run._activity_row({"type": "Run", "distance": 10000,
                                "moving_time": 3000, "start_date_local": "2026-08-11T06:30:00Z",
                                "name": "Tempo"})
    assert runrow["pace"] == "5:00/km" and runrow["date"] == "2026-08-11"


def test_epoch_parsing_and_validation():
    assert run._epoch(None) is None
    assert isinstance(run._epoch("2026-01-01"), int)
    import pytest
    with pytest.raises(SystemExit):
        run._epoch("nope")


def test_clamp_per_page():
    assert run._clamp_per_page(0) == 1
    assert run._clamp_per_page(999) == 50
    assert run._clamp_per_page("bad") == 30


def test_totals_shape():
    t = run._totals({"count": 3, "distance": 30000, "moving_time": 9000,
                     "elevation_gain": 120})
    assert t == {"count": 3, "distance_km": 30.0, "moving_time": "2:30:00",
                 "elevation_m": 120}
    assert run._totals(None) is None


def test_action_activities_dispatch(monkeypatch):
    payload = [{"id": 1, "type": "Run", "distance": 5000, "moving_time": 1500,
                "start_date_local": "2026-08-11T06:00:00Z", "name": "AM"}]
    monkeypatch.setattr(run, "_get", lambda conn, path, params=None: payload)
    out = run.act(None, {"action": "activities", "per_page": 10})
    assert out["count"] == 1 and out["activities"][0]["pace"] == "5:00/km"


def test_unknown_action_exits():
    import pytest
    with pytest.raises(SystemExit):
        run.act(None, {"action": "bogus"})
