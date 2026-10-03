"""The Running App's brief and report actions, scoped to one tool call."""
from __future__ import annotations

import html
import json
import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

import brief as bf
import stats

PAGE_SIZE = 100
MAX_ACTIVITIES = 10_000
APP = "running"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("the platform endpoint redirected")


def _base(args: dict) -> str:
    raw = (args.get("_app_data") or {}).get("url", "")
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise ValueError("Running App data is unavailable")
    return raw.rstrip("/")


def _post(url: str, payload: dict) -> dict:
    request = Request(url, data=json.dumps(payload).encode(), method="POST",
                      headers={"Content-Type": "application/json"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ValueError("platform answer is too large")
            return json.loads(raw)
    except HTTPError as exc:
        raise ValueError(f"Running action failed (HTTP {exc.code})") from None


def _query(base: str, view: str, params: dict | None = None) -> list[dict]:
    rows, cursor = [], None
    while True:
        data = _post(base + "/agent/records/query", {
            "app": APP, "view": view, "params": params or {},
            "limit": PAGE_SIZE, "cursor": cursor})
        batch = data.get("rows")
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise ValueError("invalid Running query")
        rows.extend(batch)
        if len(rows) > MAX_ACTIVITIES:
            raise ValueError("Running history exceeds its bounded read")
        next_cursor = data.get("next_cursor")
        if next_cursor is None:
            return rows
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise ValueError("invalid Running cursor")
        cursor = next_cursor


def _transaction(base: str, operations: list[dict]) -> dict:
    return _post(base + "/agent/records/transaction", {
        "app": APP, "request_id": uuid.uuid4().hex, "operations": operations})


def _completed_week(today: date) -> tuple[str, datetime]:
    monday = stats.completed_week_start(today)
    finished = datetime.combine(monday + timedelta(days=7), time.min,
                                ZoneInfo("America/Toronto")).astimezone(timezone.utc)
    return monday.isoformat(), finished


def _fresh_sync(base: str, week_end: datetime) -> None:
    rows = _query(base, "sync_status")
    if not rows:
        raise ValueError("sync Strava before writing this completed week's brief")
    completed = (rows[0].get("values") or {}).get("completed_at")
    try:
        at = datetime.fromisoformat(str(completed).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError("Running sync receipt is invalid") from None
    if at.tzinfo is None or at.astimezone(timezone.utc) < week_end:
        raise ValueError("Strava sync predates the completed week; sync again first")


def _week_stats(base: str, week: str) -> tuple[int, int]:
    end = (date.fromisoformat(week) + timedelta(days=6)).isoformat()
    rows = _query(base, "activities_recent")
    activity = [row["values"] for row in rows
                if week <= str(row["values"].get("day", "")) <= end]
    runs = [item for item in activity if item.get("type") in stats.RUN_TYPES]
    return sum(int(item.get("distance_m") or 0) for item in runs), len(runs)


def _render_report(values: dict) -> tuple[str, dict]:
    def esc(value):
        return html.escape(str(value), quote=True)
    week = values["week_start"]
    km = round((values.get("distance_m") or 0) / 1000, 1)
    parts = ['<header class="rk-header">',
             f'<h1 class="rk-title">Running week — {esc(week)}</h1>',
             f'<p class="rk-meta">{esc(" · ".join(values.get("tags") or [])) or "weekly recap"}'
             ' · by Running Coach</p>', '</header>', '<div class="rk-stat-row">',
             f'<div class="rk-stat"><span class="rk-stat-value">{km:.1f} km</span>'
             '<span class="rk-stat-label">distance</span></div>',
             f'<div class="rk-stat"><span class="rk-stat-value">{values.get("runs") or 0}</span>'
             '<span class="rk-stat-label">runs</span></div>', '</div>']
    if values.get("body"):
        parts.append('<section class="rk-section"><h2>The week</h2>'
                     f'<p>{esc(values["body"])}</p></section>')
    highlights = values.get("highlights") or []
    if highlights:
        parts.append('<section class="rk-section"><h2>Highlights</h2><ul class="rk-list">')
        parts.extend(f'<li>{esc(item)}</li>' for item in highlights)
        parts.append('</ul></section>')
    parts.append(f'<footer class="rk-footer">running app · weekly-running · {esc(week)}</footer>')
    return "".join(parts), {"runs": values.get("runs") or 0,
                            "distance_km": km, "run_id": values.get("run_id")}


def _save_report(base: str, values: dict) -> None:
    body, meta = _render_report(values)
    report_url = base[:-len("/api/app-data")] + "/api/reports"
    _post(report_url, {"type": "weekly-running", "date": values["week_start"],
                       "title": f"Running week — {values['week_start']}",
                       "meta": meta, "html": body})


def execute(args: dict, *, today: date | None = None) -> dict:
    action = args.get("action")
    if action not in ("brief", "report", "recover_reports"):
        raise ValueError("unknown Running write action")
    today = today or datetime.now(ZoneInfo("America/Toronto")).date()
    week, week_end = _completed_week(today)
    if action == "recover_reports":
        base = _base(args)
        candidates = []
        for row in _query(base, "briefs_recent"):
            values = row.get("values") or {}
            try:
                day = date.fromisoformat(values["week_start"])
            except (KeyError, TypeError, ValueError):
                continue
            if (not values.get("posted") and day.weekday() == 0
                    and today - timedelta(days=366) <= day <= date.fromisoformat(week)):
                candidates.append(row)
        recovered = []
        for row in sorted(candidates, key=lambda item: item["values"]["week_start"])[:2]:
            values = row["values"]
            _save_report(base, values)
            _transaction(base, [{"op": "update", "collection": "briefs",
                                 "id": row["id"], "expected_version": values["version"],
                                 "values": {"posted": True}}])
            recovered.append(values["week_start"])
        return {"recovered": recovered, "remaining": max(0, len(candidates)-len(recovered))}
    if action == "report" and args.get("week_start") is not None:
        try:
            requested = date.fromisoformat(args["week_start"])
        except (TypeError, ValueError):
            raise ValueError("week_start must be a date") from None
        if (requested.weekday() != 0 or requested > date.fromisoformat(week)
                or requested < today - timedelta(days=366)):
            raise ValueError("report week_start must be a completed Monday within one year")
        week = requested.isoformat()
    base = _base(args)
    existing = _query(base, "brief_by_week", {"week_start": week})
    if action == "brief":
        _fresh_sync(base, week_end)
        cleaned = bf.clean_brief({"body": args.get("body"),
                                  "highlights": args.get("highlights"),
                                  "tags": args.get("tags")})
        if cleaned is None:
            raise ValueError("brief needs a body or highlight")
        distance, runs = _week_stats(base, week)
        values = {**cleaned, "week_start": week, "distance_m": distance,
                  "runs": runs, "run_id": os.environ.get("TOOL_RUN_ID") or None,
                  "posted": bool(existing and existing[0]["values"].get("posted"))}
        if existing:
            old = existing[0]
            operation = {"op": "update", "collection": "briefs", "id": old["id"],
                         "expected_version": old["values"]["version"], "values": values}
        else:
            operation = {"op": "create", "collection": "briefs",
                         "id": uuid.uuid5(uuid.NAMESPACE_URL, f"running:brief:{week}").hex,
                         "values": values}
        receipt = _transaction(base, [operation])
        if values["posted"]:
            return {"week_start": week, "stored": True, "report": "already_posted"}
        version = receipt["results"][0]["version"]
        record_id = receipt["results"][0]["id"]
    else:
        if not existing:
            raise ValueError("there is no completed-week brief to report")
        row = existing[0]
        values = row["values"]
        version = values["version"]
        record_id = row["id"]
    _save_report(base, values)
    if not values["posted"]:
        _transaction(base, [{"op": "update", "collection": "briefs",
                             "id": record_id, "expected_version": version,
                             "values": {"posted": True}}])
    return {"week_start": week, "stored": True, "report": "saved"}
