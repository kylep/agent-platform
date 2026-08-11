"""Derived running stats — computed here, in trusted deterministic code, never
by the agent. Everything is a pure function over a list of activity dicts
(keys: day, type, distance_m, moving_time_s, elevation_m, avg_hr, max_hr), so
the API can hand in ORM rows as dicts and tests can hand in literals.

Pace is seconds-per-km; distances surface as km. "A run" for pace/PR purposes
is a foot RUN_TYPE — a ride still counts toward the distance heatmap and totals
but never sets a running PR.
"""
from __future__ import annotations

from datetime import date, timedelta

from runningapp.db import FOOT_TYPES, RUN_TYPES

# Whimsical yardsticks for the cumulative-distance stat. Distances in km.
LANDMARKS = [
    ("the Bruce Trail end to end", 890),
    ("Toronto to Vancouver", 4400),
    ("the width of Lake Ontario", 311),
    ("a lap of Manhattan", 51),
]
EVEREST_M = 8849
MARATHON_M = 42195


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _pace_s_per_km(a: dict) -> float | None:
    if a["distance_m"] <= 0 or a["moving_time_s"] <= 0:
        return None
    return a["moving_time_s"] / (a["distance_m"] / 1000)


def fmt_pace(sec_per_km: float | None) -> str | None:
    if sec_per_km is None:
        return None
    m, s = divmod(int(round(sec_per_km)), 60)
    return f"{m}:{s:02d}/km"


def heatmap(acts: list[dict], end_day: date, weeks: int = 26) -> list[dict]:
    """A dense day-by-day grid (GitHub-contributions style) for the last `weeks`
    weeks, starting on a Monday so the frontend can lay it out in clean columns.
    Every day in range is present, zeros included."""
    start = _monday(end_day) - timedelta(weeks=weeks - 1)
    by_day: dict[str, list[int]] = {}
    for a in acts:
        b = by_day.setdefault(a["day"], [0, 0])
        b[0] += a["distance_m"]
        b[1] += 1
    out, cur = [], start
    while cur <= end_day:
        iso = cur.isoformat()
        dm, c = by_day.get(iso, (0, 0))
        out.append({"day": iso, "distance_km": round(dm / 1000, 2), "count": c})
        cur += timedelta(days=1)
    return out


def weekly(acts: list[dict], end_day: date, weeks: int = 12) -> list[dict]:
    """Distance + run count per ISO week for the last `weeks` weeks."""
    end_monday = _monday(end_day)
    start_monday = end_monday - timedelta(weeks=weeks - 1)
    buckets: dict[date, dict] = {}
    for a in acts:
        wk = _monday(date.fromisoformat(a["day"]))
        if wk < start_monday or wk > end_monday:
            continue
        b = buckets.setdefault(wk, {"distance_m": 0, "runs": 0, "moving_time_s": 0})
        b["distance_m"] += a["distance_m"]
        b["moving_time_s"] += a["moving_time_s"]
        if a["type"] in RUN_TYPES:
            b["runs"] += 1
    out, wk = [], start_monday
    while wk <= end_monday:
        b = buckets.get(wk, {"distance_m": 0, "runs": 0, "moving_time_s": 0})
        out.append({"week_start": wk.isoformat(),
                    "distance_km": round(b["distance_m"] / 1000, 2),
                    "runs": b["runs"], "moving_time_s": b["moving_time_s"]})
        wk += timedelta(weeks=1)
    return out


def _streaks(run_days: list[date], today: date) -> tuple[int, int]:
    """(longest, current) consecutive-day streaks over the days that had a run.
    The current streak counts back from today; a gap of more than one day (you
    haven't run today or yesterday) means the current streak is 0."""
    if not run_days:
        return 0, 0
    days = sorted(set(run_days))
    longest = run = 1
    for prev, cur in zip(days, days[1:]):
        run = run + 1 if (cur - prev).days == 1 else 1
        longest = max(longest, run)
    last = days[-1]
    current = 0
    if (today - last).days <= 1:
        current, cur = 1, last
        idx = len(days) - 1
        while idx > 0 and (days[idx] - days[idx - 1]).days == 1:
            current += 1
            idx -= 1
    return longest, current


def prs(acts: list[dict], today: date) -> dict:
    runs = [a for a in acts if a["type"] in RUN_TYPES
            and a["distance_m"] > 0 and a["moving_time_s"] > 0]
    out: dict = {"longest_run": None, "fastest_5k": None, "fastest_10k": None,
                 "biggest_week": None, "longest_streak": 0, "current_streak": 0}
    if runs:
        lr = max(runs, key=lambda a: a["distance_m"])
        out["longest_run"] = {"km": round(lr["distance_m"] / 1000, 2), "day": lr["day"]}
        for dist, key in ((5000, "fastest_5k"), (10000, "fastest_10k")):
            elig = [a for a in runs if a["distance_m"] >= dist]
            if elig:
                best = min(elig, key=_pace_s_per_km)
                out[key] = {"pace": fmt_pace(_pace_s_per_km(best)), "day": best["day"]}
    wk: dict[date, int] = {}
    for a in acts:
        w = _monday(date.fromisoformat(a["day"]))
        wk[w] = wk.get(w, 0) + a["distance_m"]
    if wk:
        bw = max(wk, key=lambda k: wk[k])
        out["biggest_week"] = {"km": round(wk[bw] / 1000, 2), "week_start": bw.isoformat()}
    foot_days = [date.fromisoformat(a["day"]) for a in acts if a["type"] in FOOT_TYPES]
    out["longest_streak"], out["current_streak"] = _streaks(foot_days, today)
    return out


def _comparison(total_m: int) -> str | None:
    km = total_m / 1000
    if km <= 0:
        return None
    marathons = km / (MARATHON_M / 1000)
    for name, dist in LANDMARKS:
        if km >= dist * 0.6:
            times = km / dist
            phrase = (f"{times:.1f}× {name}" if times >= 1
                      else f"{times * 100:.0f}% of {name}")
            return f"≈ {marathons:.1f} marathons · {phrase}"
    return f"≈ {marathons:.1f} marathons"


def totals(acts: list[dict]) -> dict:
    dm = sum(a["distance_m"] for a in acts)
    el = sum(a["elevation_m"] or 0 for a in acts)
    mt = sum(a["moving_time_s"] for a in acts)
    return {"total_km": round(dm / 1000, 1),
            "total_elevation_m": round(el),
            "total_moving_time_s": mt,
            "activities": len(acts),
            "runs": sum(1 for a in acts if a["type"] in RUN_TYPES),
            "everests": round(el / EVEREST_M, 2) if el else 0,
            "comparison": _comparison(dm)}
