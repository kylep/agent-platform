"""Running's chart and coach reads stay bounded and agree with the old math."""
from datetime import date
from pathlib import Path

import jsonschema
import pytest
import yaml

import running_views as run


SOURCE = [
    {"day": "2026-09-29", "name": "Morning 5K", "type": "Run",
     "distance_m": 5000, "moving_time_s": 1800, "elevation_m": 20,
     "avg_hr": 145, "max_hr": 161},
    {"day": "2026-09-30", "name": "Evening walk", "type": "Walk",
     "distance_m": 2000, "moving_time_s": 1600, "elevation_m": 5,
     "avg_hr": 100, "max_hr": 120},
]


@pytest.fixture
def view_source(monkeypatch):
    monkeypatch.setattr(run, "activities", lambda args: SOURCE)
    return {"_app_data": {"url": "http://127.0.0.1/api/app-data"}}


@pytest.mark.parametrize("action", ["dashboard", "calendar", "weekly"])
def test_view_outputs_match_reviewed_schema(view_source, action):
    result = run.answer({**view_source, "action": action}, today=date(2026, 10, 1))
    manifest = yaml.safe_load(Path(__file__).with_name("tool.yaml").read_text())
    jsonschema.validate(result, manifest["view_actions"][action]["output_schema"])
    if action == "dashboard":
        assert result["rows"][0]["total_km"] == 7
        assert result["rows"][0]["runs"] == 1
    if action == "calendar":
        assert len(result["rows"]) <= 190


def test_scan_refuses_truncated_history(monkeypatch):
    monkeypatch.setattr(run, "MAX_ACTIVITIES", 1)
    monkeypatch.setattr(run, "_scan", lambda url, cursor: {
        "rows": [{"values": item, "restricted": []} for item in SOURCE],
        "next_cursor": None})
    with pytest.raises(ValueError, match="bounded scan"):
        run.activities({"_app_data": {"url": "http://127.0.0.1/api/app-data"}})


def test_scan_refuses_redacted_values(monkeypatch):
    monkeypatch.setattr(run, "_scan", lambda url, cursor: {
        "rows": [{"values": SOURCE[0], "restricted": ["distance_m"]}],
        "next_cursor": None})
    with pytest.raises(ValueError, match="need all"):
        run.activities({"_app_data": {"url": "http://127.0.0.1/api/app-data"}})
