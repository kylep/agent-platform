"""Copy one Strava sync into a published Running state App through its call proxy.

The old Kafka conduit remains the fallback only while the App does not exist.
Once published, a missing grant or failed write is an error, never a silent
return to the legacy writer. A sync receipt is recorded only after every
activity chunk commits, so a partial failure is visibly stale and retryable.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_ACTIVITIES = 500
PAGE_SIZE = 100
CHUNK = 99  # leave one operation for a future safety receipt if needed
KNOWN_TYPES = {"Run", "TrailRun", "VirtualRun", "Walk", "Hike", "Ride",
               "VirtualRide", "Swim", "Workout", "WeightTraining"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ValueError("the App data endpoint redirected")


def _base(raw: str) -> str:
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise ValueError("invalid App data endpoint")
    return raw.rstrip("/")


def _post(base: str, route: str, payload: dict, *, allow_missing: bool = False) -> dict | None:
    raw = json.dumps(payload).encode()
    request = Request(base + route, data=raw, method="POST",
                      headers={"Content-Type": "application/json"})
    try:
        with build_opener(_NoRedirect()).open(request, timeout=30) as response:
            data = response.read(1_048_577)
            if len(data) > 1_048_576:
                raise ValueError("App data response is too large")
            return json.loads(data)
    except HTTPError as exc:
        if allow_missing and exc.code == 404:
            return None
        raise ValueError(f"Running App data request failed (HTTP {exc.code})") from None


def _id(kind: str, key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"running:{kind}:{key}").hex


def _clean(row: dict) -> dict:
    sid = str(row.get("id", ""))
    if not sid.isdigit() or len(sid) > 24:
        raise ValueError("Strava activity has an invalid ID")
    day = str(row.get("date", ""))
    try:
        date.fromisoformat(day)
    except ValueError:
        raise ValueError("Strava activity has an invalid local date") from None
    kind = row.get("type")
    if kind not in KNOWN_TYPES:
        kind = "Run"
    name = re.sub(r"[\x00-\x1f\x7f]", " ", str(row.get("name") or ""))[:200]
    def number(field: str, maximum: float, integer: bool = False):
        value = row.get(field)
        if value is None:
            return None
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            return None
        if not 0 <= numeric <= maximum:
            return None
        return int(round(numeric)) if integer else numeric
    return {"strava_id": sid, "day": day, "name": name, "type": kind,
            "distance_m": number("distance_m", 500_000, True) or 0,
            "moving_time_s": number("moving_time_s", 86_400, True) or 0,
            "elevation_m": number("elevation_m", 30_000),
            "avg_hr": number("avg_hr", 260), "max_hr": number("max_hr", 260)}


def _all(base: str, view: str) -> list[dict]:
    rows, cursor = [], None
    while True:
        data = _post(base, "/agent/records/query", {
            "app": "running", "view": view, "limit": PAGE_SIZE, "cursor": cursor})
        batch = data.get("rows") if isinstance(data, dict) else None
        if not isinstance(batch, list) or len(batch) > PAGE_SIZE:
            raise ValueError("invalid Running App query")
        rows.extend(batch)
        if len(rows) > 10_000:
            raise ValueError("Running App history exceeds sync lookup limit")
        following = data.get("next_cursor")
        if following is None:
            return rows
        if not isinstance(following, str) or not following or following == cursor:
            raise ValueError("invalid Running App cursor")
        cursor = following


def sync(args: dict, rows: list[dict], after: str | None) -> bool:
    """Return False only when no Running state App exists yet."""
    if len(rows) > MAX_ACTIVITIES:
        raise ValueError("Strava sync exceeds its bounded activity batch")
    base = _base((args.get("_app_data") or {}).get("url", ""))
    description = _post(base, "/agent/records/describe", {"app": "running"},
                        allow_missing=True)
    if description is None:
        return False
    if description.get("status") != "active":
        raise ValueError("Running state App is not active")
    existing = {}
    for row in _all(base, "activities_recent"):
        value = row.get("values") or {}
        sid = value.get("strava_id")
        if sid is not None:
            existing[str(sid)] = row
    operations = []
    for raw in rows:
        values = _clean(raw)
        old = existing.get(values["strava_id"])
        if old:
            operations.append({"op": "update", "collection": "activities",
                               "id": old["id"],
                               "expected_version": old["values"]["version"],
                               "values": values})
        else:
            operations.append({"op": "create", "collection": "activities",
                               "id": _id("activity", values["strava_id"]),
                               "values": values})
    for start in range(0, len(operations), CHUNK):
        _post(base, "/agent/records/transaction", {
            "app": "running", "request_id": uuid.uuid4().hex,
            "operations": operations[start:start + CHUNK]})
    sync_rows = _all(base, "sync_status")
    now = datetime.now(timezone.utc).isoformat()
    values = {"source": "strava", "completed_at": now,
              "after": after, "count": len(rows)}
    old = next((row for row in sync_rows if row.get("values", {}).get("source") == "strava"), None)
    operation = ({"op": "update", "collection": "sync_state", "id": old["id"],
                  "expected_version": old["values"]["version"], "values": values}
                 if old else {"op": "create", "collection": "sync_state",
                              "id": _id("sync", "strava"), "values": values})
    _post(base, "/agent/records/transaction", {
        "app": "running", "request_id": uuid.uuid4().hex,
        "operations": [operation]})
    return True
