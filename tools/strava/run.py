"""strava tool: read-only Strava API v3 wrapper with a durable token cache.

Ported from the claude-plugins pai-tools/strava skill (a stdlib CLI) into the
platform's tool contract: JSON args on stdin, compact JSON on stdout, non-zero
exit + stderr on failure. Read-only — no endpoint here writes to Strava.

The hard part is auth. Strava access tokens live ~6h and the refresh token
ROTATES on every refresh (the old one dies at once). A file cache (what the
skill uses) doesn't survive an executor subprocess, and two executor replicas
would race and invalidate each other's tokens. So the cache lives in this
tool's own pg schema (infra.database: true → TOOL_DB_URL, schema owned by the
tool role), a single row. This is the ONLY component that refreshes Strava
tokens — the Running app reads activities through THIS tool, never with its own
OAuth — which is what keeps the rotating refresh token from being spent twice.
"""
from __future__ import annotations

import json
import os
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

BASE = "https://www.strava.com/api/v3"
TOKEN_URL = "https://www.strava.com/oauth/token"
UA = "agent-platform-strava-tool"
EXPIRY_BUFFER = 60  # refresh this many seconds before Strava's stated expiry


# --------------------------------------------------------------------------
# Token cache (this tool's private pg schema, single row)
# --------------------------------------------------------------------------

def _connect():
    url = os.environ.get("TOOL_DB_URL", "")
    if not url:
        print("strava token cache unavailable (TOOL_DB_URL missing — the tool "
              "needs infra.database: true to be provisioned)", file=sys.stderr)
        raise SystemExit(2)
    import psycopg
    conn = psycopg.connect(url)
    # Table lives in the tool role's own schema; search_path's "$user" entry
    # (the schema is named after and owned by the role) resolves it unqualified.
    with conn.cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS oauth_token ("
            "  id int PRIMARY KEY DEFAULT 1,"
            "  access_token text NOT NULL,"
            "  refresh_token text NOT NULL,"
            "  expires_at bigint NOT NULL DEFAULT 0,"
            "  updated_at timestamptz NOT NULL DEFAULT now(),"
            "  CONSTRAINT oauth_token_singleton CHECK (id = 1))")
    conn.commit()
    return conn


def _load_tokens(conn) -> dict:
    """The cached tokens, or a seed from the secret env (marked expired so the
    first call refreshes once and takes ownership of the rotating token)."""
    with conn.cursor() as cur:
        cur.execute("SELECT access_token, refresh_token, expires_at "
                    "FROM oauth_token WHERE id = 1")
        row = cur.fetchone()
    if row:
        return {"access_token": row[0], "refresh_token": row[1],
                "expires_at": row[2]}
    for k in ("STRAVA_ACCESS_TOKEN", "STRAVA_REFRESH_TOKEN"):
        if not os.environ.get(k, "").strip():
            print(f"{k} is not set and no cached token exists — the strava "
                  "secret must be configured", file=sys.stderr)
            raise SystemExit(2)
    return {"access_token": os.environ["STRAVA_ACCESS_TOKEN"].strip(),
            "refresh_token": os.environ["STRAVA_REFRESH_TOKEN"].strip(),
            "expires_at": 0}


def _save_tokens(conn, tok: dict) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO oauth_token (id, access_token, refresh_token, "
            "  expires_at, updated_at) VALUES (1, %s, %s, %s, now()) "
            "ON CONFLICT (id) DO UPDATE SET access_token = EXCLUDED.access_token, "
            "  refresh_token = EXCLUDED.refresh_token, "
            "  expires_at = EXCLUDED.expires_at, updated_at = now()",
            (tok["access_token"], tok["refresh_token"], tok["expires_at"]))
    conn.commit()


def _refresh(conn, refresh_token: str) -> dict:
    cid = os.environ.get("STRAVA_CLIENT_ID", "").strip()
    secret = os.environ.get("STRAVA_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        print("STRAVA_CLIENT_ID/STRAVA_CLIENT_SECRET not set — cannot refresh",
              file=sys.stderr)
        raise SystemExit(2)
    body = urlencode({"client_id": cid, "client_secret": secret,
                      "grant_type": "refresh_token",
                      "refresh_token": refresh_token}).encode()
    req = Request(TOKEN_URL, data=body, method="POST",
                  headers={"Content-Type": "application/x-www-form-urlencoded",
                           "User-Agent": UA})
    try:
        with urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        print(f"strava token refresh failed (HTTP {e.code}): {detail}",
              file=sys.stderr)
        raise SystemExit(2)
    tok = {"access_token": payload["access_token"],
           "refresh_token": payload["refresh_token"],
           "expires_at": int(payload["expires_at"])}
    _save_tokens(conn, tok)
    return tok


def _access_token(conn, force: bool = False) -> str:
    tok = _load_tokens(conn)
    if force or tok["expires_at"] <= time.time() + EXPIRY_BUFFER:
        tok = _refresh(conn, tok["refresh_token"])
    return tok["access_token"]


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def _get(conn, path: str, params: dict | None = None):
    """GET a JSON endpoint; refresh reactively on 401 and retry once."""
    url = BASE + path
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url = f"{url}?{urlencode(clean)}"

    def _once(token: str):
        req = Request(url, method="GET",
                      headers={"Authorization": f"Bearer {token}",
                               "User-Agent": UA})
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())

    token = _access_token(conn)
    try:
        return _once(token)
    except HTTPError as e:
        if e.code == 401:
            return _once(_access_token(conn, force=True))
        if e.code == 429:
            print("strava rate limit hit (HTTP 429) — 200 req/15min, "
                  "2000/day. Wait and retry.", file=sys.stderr)
            raise SystemExit(2)
        detail = e.read().decode("utf-8", "replace")[:200]
        print(f"strava API error {e.code}: {detail}", file=sys.stderr)
        raise SystemExit(2)


# --------------------------------------------------------------------------
# Formatting — small, model-friendly shapes (km / pace / H:MM:SS)
# --------------------------------------------------------------------------

def _km(meters) -> float | None:
    try:
        return round(float(meters) / 1000, 2)
    except (TypeError, ValueError):
        return None


def _dur(seconds) -> str | None:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return None
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def _pace(distance_m, moving_s) -> str | None:
    """min/km from distance + moving time, for foot sports."""
    try:
        km = float(distance_m) / 1000
        if km <= 0:
            return None
        sec_per_km = float(moving_s) / km
        m, s = divmod(int(round(sec_per_km)), 60)
        return f"{m}:{s:02d}/km"
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _activity_row(a: dict) -> dict:
    dist = a.get("distance")
    moving = a.get("moving_time")
    is_foot = (a.get("type") or a.get("sport_type") or "").lower() in (
        "run", "trailrun", "walk", "hike", "virtualrun")
    def _int(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return None
    return {
        "id": a.get("id"),
        "date": (a.get("start_date_local") or "")[:10],
        "name": a.get("name"),
        "type": a.get("type"),
        "distance_km": _km(dist),
        "moving_time": _dur(moving),
        "pace": _pace(dist, moving) if is_foot else None,
        "elevation_m": a.get("total_elevation_gain"),
        "avg_hr": a.get("average_heartrate"),
        "max_hr": a.get("max_heartrate"),
        # Raw numerics: unambiguous for a downstream store to transcribe and do
        # math on (the friendly fields above are for humans / chat).
        "distance_m": _int(dist),
        "moving_time_s": _int(moving),
    }


def _totals(t: dict | None) -> dict | None:
    if not t:
        return None
    return {"count": t.get("count", 0), "distance_km": _km(t.get("distance")),
            "moving_time": _dur(t.get("moving_time")),
            "elevation_m": t.get("elevation_gain")}


# --------------------------------------------------------------------------
# Actions
# --------------------------------------------------------------------------

def _clamp_per_page(n) -> int:
    try:
        return max(1, min(int(n), 50))
    except (TypeError, ValueError):
        return 30


def _epoch(day: str | None):
    """YYYY-MM-DD → epoch seconds (UTC midnight), or None."""
    if not day:
        return None
    from datetime import datetime, timezone
    try:
        return int(datetime.strptime(day.strip(), "%Y-%m-%d")
                   .replace(tzinfo=timezone.utc).timestamp())
    except ValueError:
        print(f"date {day!r} must be YYYY-MM-DD", file=sys.stderr)
        raise SystemExit(2)


def act(conn, args: dict) -> dict:
    action = args["action"]
    if action == "athlete":
        a = _get(conn, "/athlete")
        return {"id": a.get("id"),
                "name": f"{a.get('firstname', '')} {a.get('lastname', '')}".strip(),
                "username": a.get("username"), "city": a.get("city"),
                "state": a.get("state"), "country": a.get("country"),
                "weight_kg": a.get("weight")}
    if action == "stats":
        aid = _get(conn, "/athlete")["id"]
        s = _get(conn, f"/athletes/{aid}/stats")
        return {
            "recent_runs": _totals(s.get("recent_run_totals")),
            "ytd_runs": _totals(s.get("ytd_run_totals")),
            "all_runs": _totals(s.get("all_run_totals")),
            "recent_rides": _totals(s.get("recent_ride_totals")),
            "ytd_rides": _totals(s.get("ytd_ride_totals")),
            "all_rides": _totals(s.get("all_ride_totals")),
            "biggest_run_km": _km(s.get("biggest_run_distance")),
        }
    if action == "activities":
        rows = _get(conn, "/athlete/activities", {
            "per_page": _clamp_per_page(args.get("per_page") or 30),
            "after": _epoch(args.get("after")),
            "before": _epoch(args.get("before")), "page": 1})
        return {"count": len(rows), "activities": [_activity_row(a) for a in rows]}
    if action == "activity":
        aid = (args.get("id") or "").strip()
        if not aid:
            print("action=activity needs id", file=sys.stderr)
            raise SystemExit(2)
        a = _get(conn, f"/activities/{aid}")
        out = _activity_row(a)
        out.update({
            "elapsed_time": _dur(a.get("elapsed_time")),
            "description": a.get("description"),
            "gear_id": a.get("gear_id"),
            "kudos": a.get("kudos_count"),
            "splits_km": [
                {"km": i + 1, "time": _dur(sp.get("moving_time")),
                 "pace": _pace(sp.get("distance"), sp.get("moving_time")),
                 "avg_hr": sp.get("average_heartrate")}
                for i, sp in enumerate(a.get("splits_metric") or [])],
        })
        return out
    if action == "gear":
        gid = (args.get("id") or "").strip()
        if not gid:
            print("action=gear needs id", file=sys.stderr)
            raise SystemExit(2)
        g = _get(conn, f"/gear/{gid}")
        return {"id": g.get("id"), "name": g.get("name"),
                "brand": g.get("brand_name"), "model": g.get("model_name"),
                "distance_km": _km(g.get("distance"))}
    print(f"unknown action {action!r}", file=sys.stderr)
    raise SystemExit(2)


def main() -> int:
    args = json.load(sys.stdin)
    conn = _connect()
    try:
        print(json.dumps(act(conn, args)))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
