"""Bounded Running App reads through the executor's scoped data proxy."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

import stats
import brief
import running_actions

PAGE_SIZE = 1000
MAX_ACTIVITIES = 10_000
FIELDS = ["day", "name", "type", "distance_m", "moving_time_s",
          "elevation_m", "avg_hr", "max_hr"]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("the App data endpoint redirected")


def _endpoint(raw: str) -> str:
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise ValueError("invalid App data endpoint")
    return raw.rstrip("/") + "/scan"


def _scan(url: str, cursor: str | None) -> dict:
    payload = json.dumps({"role": "activities", "fields": FIELDS,
                          "limit": PAGE_SIZE, "cursor": cursor}).encode()
    request = Request(url, data=payload, headers={"Content-Type": "application/json"},
                      method="POST")
    with build_opener(_NoRedirect()).open(request, timeout=25) as response:
        if response.status != 200:
            raise ValueError(f"App data scan returned {response.status}")
        raw = response.read(262_145)
        if len(raw) > 262_144:
            raise ValueError("App data scan page is too large")
    data = json.loads(raw)
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        raise ValueError("invalid App data scan response")
    return data


def activities(args: dict) -> list[dict]:
    url = _endpoint((args.get("_app_data") or {}).get("url", ""))
    rows, cursor = [], None
    while True:
        data = _scan(url, cursor)
        batch = data["rows"]
        if len(batch) > PAGE_SIZE or len(rows) + len(batch) > MAX_ACTIVITIES:
            raise ValueError("Running history exceeds the bounded scan")
        for row in batch:
            if not isinstance(row, dict) or not isinstance(row.get("values"), dict):
                raise ValueError("invalid Running activity row")
            if row.get("restricted"):
                raise ValueError("Running stats need all activity fields")
            values = row["values"]
            if any(key not in values for key in FIELDS):
                raise ValueError("Running activity row is missing a field")
            rows.append(values)
        next_cursor = data.get("next_cursor")
        if next_cursor is None:
            break
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise ValueError("invalid Running scan cursor")
        cursor = next_cursor
    return rows


def answer(args: dict, *, today: date | None = None) -> dict:
    action = args.get("action")
    if action not in {"dashboard", "calendar", "weekly", "coach_context"}:
        raise ValueError("unknown Running action")
    today = today or datetime.now(ZoneInfo("America/Toronto")).date()
    acts = ([row["values"] for row in running_actions._query(
                running_actions._base(args), "activities_recent")]
            if action == "coach_context" else activities(args))
    if action == "calendar":
        return {"rows": stats.heatmap(acts, today, 26)}
    if action == "weekly":
        return {"rows": stats.weekly(acts, today, 12)}
    if action == "dashboard":
        total = stats.totals(acts)
        prs = stats.prs(acts, today)
        return {"rows": [{
            "total_km": total["total_km"], "activities": total["activities"],
            "runs": total["runs"], "total_elevation_m": total["total_elevation_m"],
            "longest_run_km": (prs["longest_run"] or {}).get("km", 0),
            "fastest_5k": (prs["fastest_5k"] or {}).get("pace") or "—",
            "fastest_10k": (prs["fastest_10k"] or {}).get("pace") or "—",
            "longest_streak": prs["longest_streak"],
            "current_streak": prs["current_streak"],
        }]}
    latest = max((a["day"] for a in acts), default=None)
    floor = today - timedelta(days=90)
    sync_after = max(date.fromisoformat(latest) - timedelta(days=3), floor) \
        if latest else floor
    week_start = stats.completed_week_start(today)
    week_end = week_start + timedelta(days=6)
    week = [a for a in acts if week_start.isoformat() <= a["day"] <= week_end.isoformat()
            and a["type"] in stats.RUN_TYPES]
    def run_summary(row, *, elevation=False):
        value = {"day": row["day"], "name": row["name"], "type": row["type"],
                 "distance_km": round((row.get("distance_m") or 0) / 1000, 2),
                 "moving_time_s": row.get("moving_time_s") or 0,
                 "avg_hr": row.get("avg_hr")}
        if elevation:
            value["elevation_m"] = row.get("elevation_m")
        return value
    recent = sorted(acts, key=lambda row: row["day"], reverse=True)[:12]
    return {"today": today.isoformat(), "sync_after": sync_after.isoformat(),
            "completed_week": {"week_start": week_start.isoformat(),
                               "week_end": week_end.isoformat(),
                               "totals": stats.totals(week),
                               "runs": [run_summary(row) for row in week[:30]]},
            "totals": stats.totals(acts), "weeks": stats.weekly(acts, today, 8),
            "records": stats.prs(acts, today),
            "recent_runs": [run_summary(row, elevation=True) for row in recent
                            if row["type"] in stats.RUN_TYPES],
            "allowed_tags": brief.TAGS}


def main() -> int:
    try:
        print(json.dumps(answer(json.load(sys.stdin))))
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
