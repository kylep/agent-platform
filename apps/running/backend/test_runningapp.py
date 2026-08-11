"""Backend unit tests: payload validation (brief.py) and the stats engine
(stats.py). Pure functions — no DB, no Kafka."""
from datetime import date

from runningapp import brief as bf
from runningapp import stats as st


# --------------------------------------------------------------------------
# brief.py — untrusted payload validation
# --------------------------------------------------------------------------

def test_parse_payload_tolerates_fences_and_prose():
    txt = 'here you go:\n```json\n{"activities": [], "brief": {"body": "hi"}}\n```'
    p = bf.parse_payload(txt)
    assert p and p["brief"]["body"] == "hi"
    assert bf.parse_payload("no json here") is None


def test_clean_activities_clamps_and_dedupes():
    raw = [
        {"id": 1, "date": "2026-08-10", "type": "Run", "name": "AM",
         "distance_m": 10000, "moving_time_s": 3000, "elevation_m": 40,
         "avg_hr": 150, "max_hr": 175},
        {"id": 1, "date": "2026-08-10", "type": "Run", "distance_m": 9999},  # dup id
        {"id": 2, "date": "not-a-date", "type": "Run"},                      # bad date
        {"id": 3, "date": "2026-08-11", "type": "Bogus",                     # unknown type
         "distance_m": 999_999_999, "moving_time_s": -5, "max_hr": 9000},
    ]
    out = bf.clean_activities(raw)
    ids = [a["id"] for a in out]
    assert ids == [1, 3]                       # dup + bad-date dropped
    assert out[1]["type"] == "Run"             # unknown type coerced
    assert out[1]["distance_m"] == 0           # 999M > 500km cap → None → 0
    assert out[1]["moving_time_s"] == 0        # negative → None → 0
    assert out[1]["max_hr"] is None            # 9000 > 260 cap → None


def test_clean_brief_requires_content_and_filters_tags():
    assert bf.clean_brief({"body": "", "highlights": []}) is None
    c = bf.clean_brief({"body": "Great week!", "highlights": ["Longest run 15k", ""],
                        "tags": ["pr", "not-a-tag", "long-run", "pr"]})
    assert c["body"] == "Great week!"
    assert c["highlights"] == ["Longest run 15k"]
    assert c["tags"] == ["pr", "long-run"]     # unknown dropped, deduped


def test_sanitize_defangs_mentions():
    assert "@everyone" not in bf.sanitize("hi @everyone <@123> now")


# --------------------------------------------------------------------------
# stats.py — deterministic rollups
# --------------------------------------------------------------------------

def _run(day, km, minutes, type="Run"):
    return {"day": day, "type": type, "distance_m": int(km * 1000),
            "moving_time_s": int(minutes * 60), "elevation_m": 10,
            "avg_hr": 150, "max_hr": 170}


def test_heatmap_is_dense_and_starts_on_monday():
    acts = [_run("2026-08-10", 5, 25)]   # 2026-08-10 is a Monday
    grid = st.heatmap(acts, date(2026, 8, 11), weeks=2)
    # weeks=2 back from Monday 2026-08-10 → grid opens on Monday 2026-08-03.
    assert grid[0]["day"] == date(2026, 8, 3).isoformat()
    hit = [d for d in grid if d["day"] == "2026-08-10"][0]
    assert hit["distance_km"] == 5.0 and hit["count"] == 1
    assert all("distance_km" in d for d in grid)            # zeros included


def test_weekly_buckets_by_iso_week():
    acts = [_run("2026-08-10", 5, 25), _run("2026-08-12", 8, 40),
            _run("2026-08-03", 10, 50)]
    wk = st.weekly(acts, date(2026, 8, 12), weeks=3)
    byweek = {w["week_start"]: w for w in wk}
    assert byweek["2026-08-10"]["distance_km"] == 13.0
    assert byweek["2026-08-10"]["runs"] == 2
    assert byweek["2026-08-03"]["distance_km"] == 10.0


def test_prs_pace_and_streaks():
    acts = [
        _run("2026-08-08", 5, 25),    # 5:00/km
        _run("2026-08-09", 6, 27),    # 4:30/km, eligible for 5k PR
        _run("2026-08-10", 12, 66),   # longest run, 10k-eligible 5:30/km
    ]
    p = st.prs(acts, date(2026, 8, 10))
    assert p["longest_run"]["km"] == 12.0
    assert p["fastest_5k"]["pace"] == "4:30/km"
    assert p["fastest_10k"]["pace"] == "5:30/km"
    assert p["longest_streak"] == 3 and p["current_streak"] == 3


def test_current_streak_breaks_after_gap():
    acts = [_run("2026-08-01", 5, 25), _run("2026-08-02", 5, 25)]
    p = st.prs(acts, date(2026, 8, 10))   # last run 8 days ago
    assert p["longest_streak"] == 2 and p["current_streak"] == 0


def test_totals_and_comparison():
    acts = [_run("2026-08-10", 42.2, 240)]
    t = st.totals(acts)
    assert t["total_km"] == 42.2 and t["runs"] == 1
    assert "marathon" in t["comparison"]


def test_fmt_pace():
    assert st.fmt_pace(300) == "5:00/km"
    assert st.fmt_pace(None) is None
