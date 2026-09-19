"""The browse API, served under /apps/tcms/api/.

Auth as the news app: nginx's auth_request has vetted the session or key and
stamps X-AP-User; the header is required as the marker that the request came
through the guarded route. Every route is a GET with scalar params, so the QA
reads them through `query_app` exactly as the UI does. There are no writes:
the `tcms` tool is the only writer, and the reads here are the statements in
`schema.py` the tool's own read actions run — the UI and the tool agree by
construction.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request

from tcmsapp import schema
from tcmsapp.db import query

router = APIRouter(prefix="/apps/tcms/api")

LAYERS = ("unit", "integration", "e2e", "manual")
PYRAMID = ("e2e", "integration", "unit")
RUNS_WINDOW = 30
MAX_RUNS = 200
MAX_LIMIT = 500
HISTORY_N = 30
# The tool's `cases` action accepts the same three; a key is `<suite>.<slug>`.
_KEY_RE = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+$")


def require_gateway(x_ap_user: str = Header(default="")) -> str:
    if not x_ap_user:
        raise HTTPException(401, "missing gateway identity")
    return x_ap_user


def _sf(request: Request):
    return request.app.state.sf


# --- row shaping --------------------------------------------------------------
# Raw rows differ by driver: asyncpg hands back datetimes, decoded jsonb and
# bools, sqlite the ISO text, JSON text and ints the app stored. Normalise once.

def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _json(v) -> list:
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _bool(v) -> bool | None:
    return None if v is None else bool(v)


def _split(ref: str) -> tuple[str, str, str]:
    """`kind:path::name` → (kind, path, name), split on the FIRST `::` as the
    tool does; a ref that is not one at all stays whole in `name`."""
    kind, _, rest = ref.partition(":")
    path, sep, name = rest.partition("::")
    if not sep:
        return "", "", ref
    return kind, path, name


def _totals(row: dict) -> dict:
    return {k: int(row[k] or 0) for k in schema.RESULT_STATUSES}


def run_view(row: tuple) -> dict:
    r = dict(zip(schema.RUN_COLUMNS, row))
    suites = _json(r["suites"])
    return {
        "id": r["id"], "commit_sha": r["commit_sha"], "branch": r["branch"],
        "run_id": r["run_id"], "agent": r["agent"],
        "started_at": _iso(r["started_at"]), "finished_at": _iso(r["finished_at"]),
        "verify_ok": _bool(r["verify_ok"]), "suites": suites,
        "published_at": _iso(r["published_at"]),
        "totals": _totals(r), "n": int(r["n"] or 0), "unlinked": int(r["unlinked"] or 0),
        "seconds": round(sum(float(s.get("seconds") or 0) for s in suites
                             if isinstance(s, dict)), 2),
    }


def _result(row: tuple) -> dict:
    r = dict(zip(schema.COLUMNS["results"], row))
    _, path, name = _split(r["ref"])
    return {"id": r["id"], "ref": r["ref"], "path": path, "name": name,
            "case_key": r["case_key"], "status": r["status"],
            "duration_ms": int(r["duration_ms"] or 0), "message": r["message"] or "",
            "layer": r["layer"]}


def _pct(covered, total) -> float:
    return round(100.0 * covered / total, 1) if total else 0.0


def _like_literal(q: str) -> str:
    """`q` as a LIKE pattern that matches it literally: the wildcards and the
    escape itself are escaped (the statement says `ESCAPE '\\'`)."""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _window(runs: int) -> int:
    return max(1, min(runs, MAX_RUNS))


# --- routes ---------------------------------------------------------------------

async def _pass_rate(s, runs: int) -> list[dict]:
    return [{"test_run_id": rid, "commit_sha": sha, "started_at": _iso(started),
             "pass": int(p or 0), "fail": int(f or 0), "total": int(n or 0),
             "rate": round((p or 0) / n, 4) if n else None}
            for rid, sha, started, p, f, n in await query(s, schema.PASS_RATE, (runs,))]


async def _coverage(s, runs: int) -> dict:
    latest = await query(s, schema.COVERAGE_LATEST)
    covered = sum(int(r[2]) for r in latest)
    total = sum(int(r[3]) for r in latest)
    trend = [{"test_run_id": rid, "commit_sha": sha, "started_at": _iso(started),
              "lines_covered": int(c or 0), "lines_total": int(t or 0),
              "pct": _pct(int(c or 0), int(t or 0))}
             for rid, sha, started, c, t in await query(s, schema.COVERAGE_TOTALS, (runs,))]
    return {"test_run_id": latest[0][0] if latest else None,
            "lines_covered": covered, "lines_total": total, "pct": _pct(covered, total),
            "packages": [{"package": pkg, "lines_covered": int(c), "lines_total": int(t),
                          "branch_rate": br, "pct": _pct(int(c), int(t))}
                         for _, pkg, c, t, br in latest],
            "trend": trend}


@router.get("/overview")
async def overview(request: Request, user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        latest = await query(s, schema.RUNS_WITH_TOTALS, (1,))
        by_layer = await query(s, schema.RUNTIME_BY_LAYER, (1,))
        rate = await _pass_rate(s, RUNS_WINDOW)
        coverage = await _coverage(s, RUNS_WINDOW)
        failing = (await query(s, schema.FAILING_NOW))[0][0]
        flaky = len(await query(s, schema.FLAKY, (RUNS_WINDOW,)))
        unlinked = (await query(s, schema.COUNT_UNLINKED_CASES))[0][0]
        prune = len(await query(s, schema.PRUNE_CANDIDATES, (RUNS_WINDOW,)))
    layers = {layer: (int(total_ms or 0), int(n or 0))
              for _rid, _sha, _started, layer, total_ms, n in by_layer}
    coverage.pop("packages")
    return {
        "latest_run": run_view(latest[0]) if latest else None,
        "pyramid": [{"layer": layer, "count": layers.get(layer, (0, 0))[1],
                     "seconds": round(layers.get(layer, (0, 0))[0] / 1000, 2)}
                    for layer in PYRAMID],
        "pass_rate": rate,
        "coverage": coverage,
        "attention": {"failing": int(failing or 0), "flaky": flaky,
                      "unlinked": int(unlinked or 0), "prune_candidates": prune},
    }


@router.get("/runs")
async def runs(request: Request, limit: int = Query(default=RUNS_WINDOW, ge=1, le=MAX_LIMIT),
               user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        rows = await query(s, schema.RUNS_WITH_TOTALS, (limit,))
    return [run_view(r) for r in rows]


@router.get("/runs/{run_id}")
async def run_detail(request: Request, run_id: int, user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        rows = await query(s, schema.RUN_BY_ID, (run_id,))
        if not rows:
            raise HTTPException(404, "unknown run")
        results = [_result(r) for r in await query(s, schema.RESULTS_FOR_RUN, (run_id,))]
    # Grouped by file (the closest thing a ref has to a suite), failing files
    # first, and inside a file the failures first — the page reads top-down.
    failed = ("fail", "error")
    groups: dict[str, list] = {}
    for r in results:
        groups.setdefault(r["path"], []).append(r)
    out = []
    for path, items in groups.items():
        items.sort(key=lambda r: (r["status"] not in failed, r["ref"]))
        out.append({"file": path, "failures": sum(r["status"] in failed for r in items),
                    "results": items})
    out.sort(key=lambda g: (g["failures"] == 0, g["file"]))
    return {**run_view(rows[0]), "groups": out}


def _case_row(cols: list[str], row: tuple) -> dict:
    c = dict(zip(cols, row))
    for k in ("preconditions", "steps", "automation", "tags", "tickets"):
        if k in c:
            c[k] = _json(c[k])
    if "synced_at" in c:
        c["synced_at"] = _iso(c["synced_at"])
    return c


@router.get("/cases")
async def cases(request: Request, layer: str | None = None, area: str | None = None,
                status: str | None = None, automation: str | None = None,
                unlinked: bool = False, q: str | None = None,
                limit: int = Query(default=100, ge=1, le=MAX_LIMIT),
                user: str = Depends(require_gateway)):
    where, params = [], []
    if layer:
        if layer not in LAYERS:
            raise HTTPException(422, f"layer must be one of {', '.join(LAYERS)}")
        where.append("layer = %s")
        params.append(layer)
    if status:
        if status not in schema.CASE_STATUSES:
            raise HTTPException(422, "status must be active or retired")
        where.append("status = %s")
        params.append(status)
    if area:
        where.append("area = %s")
        params.append(area)
    if automation:
        if automation not in ("automated", "manual"):
            raise HTTPException(422, "automation must be automated or manual")
        where.append("layer <> 'manual'" if automation == "automated" else "layer = 'manual'")
    if q:
        needle = f"%{_like_literal(q[:200])}%"
        where.append("(key ILIKE %s ESCAPE '\\' OR title ILIKE %s ESCAPE '\\')")
        params += [needle, needle]
    if unlinked:
        where.append(schema.CASES_UNLINKED_WHERE)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    cols = ["key", "suite", "area", "title", "layer", "priority", "status", "automation", "tags"]
    async with _sf(request)() as s:
        if area and area not in {r[0] for r in await query(s, schema.AREAS)}:
            # The same answer the other filters give an unknown value; the
            # areas are the catalogue's, so the list is read, not hard-coded.
            raise HTTPException(422, "unknown area")
        total = (await query(s, "SELECT COUNT(*) FROM cases" + clause, params))[0][0]
        rows = await query(s, "SELECT " + ", ".join(cols) + " FROM cases" + clause
                           + " ORDER BY key LIMIT %s", params + [limit])
        last = dict(await query(s, schema.LAST_STATUS_BY_CASE))
    out = []
    for r in rows:
        c = _case_row(cols, r)
        c["last_status"] = last.get(c["key"])
        out.append(c)
    return {"total": int(total or 0), "cases": out}


@router.get("/cases/{key}")
async def case_detail(request: Request, key: str, user: str = Depends(require_gateway)):
    if not _KEY_RE.match(key):
        raise HTTPException(422, "a case key is <suite>.<slug>")
    async with _sf(request)() as s:
        rows = await query(s, schema.CASE_BY_KEY, (key,))
        if not rows:
            raise HTTPException(404, "unknown case")
        c = _case_row(schema.COLUMNS["cases"], rows[0])
        refs = []
        for ref in c["automation"]:
            if not isinstance(ref, str):
                continue
            _, path, name = _split(ref)
            hist = await query(s, schema.RESULTS_FOR_REF, (ref, HISTORY_N))
            refs.append({"ref": ref, "path": path, "name": name, "results": [
                {"status": st, "duration_ms": int(ms or 0), "message": msg or "",
                 "commit_sha": sha, "started_at": _iso(started)}
                for st, ms, msg, sha, started in hist]})
    c["refs"] = refs
    # Where the case lives in git: the file is the suite, by the schema's rule.
    c["file"] = f"tcms/cases/{c['suite']}.yaml"
    return c


@router.get("/flaky")
async def flaky(request: Request, runs: int = Query(default=RUNS_WINDOW, ge=1, le=MAX_RUNS),
                user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        rows = await query(s, schema.FLAKY, (_window(runs),))
    out = []
    for ref, fails, passes, flaky_n, commits, flips in rows:
        _, path, name = _split(ref)
        out.append({"ref": ref, "fails": int(fails or 0), "passes": int(passes or 0),
                    "flaky": int(flaky_n or 0), "failing_commits": int(commits or 0),
                    "same_commit_flips": int(flips or 0), "path": path, "name": name})
    return out


@router.get("/slowest")
async def slowest(request: Request, runs: int = Query(default=RUNS_WINDOW, ge=1, le=MAX_RUNS),
                  limit: int = Query(default=15, ge=1, le=100),
                  user: str = Depends(require_gateway)):
    runs = _window(runs)
    async with _sf(request)() as s:
        rows = await query(s, schema.SLOWEST, (runs, limit))
        out = []
        for ref, layer, avg_ms, max_ms, n in rows:
            _, path, name = _split(ref)
            hist = await query(s, schema.RESULTS_FOR_REF, (ref, runs))
            out.append({"ref": ref, "path": path, "name": name, "layer": layer,
                        "avg_ms": int(avg_ms or 0), "max_ms": int(max_ms or 0), "n": int(n or 0),
                        # Oldest first, so the page draws left to right.
                        "history": [{"status": st, "duration_ms": int(ms or 0),
                                     "commit_sha": sha, "started_at": _iso(started)}
                                    for st, ms, _msg, sha, started in reversed(hist)]})
    return out


@router.get("/prune-candidates")
async def prune_candidates(request: Request,
                           runs: int = Query(default=RUNS_WINDOW, ge=1, le=MAX_RUNS),
                           user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        rows = await query(s, schema.PRUNE_CANDIDATES, (_window(runs),))
    out = []
    for ref, case_key, avg_ms, n, fails, reason in rows:
        _, path, name = _split(ref)
        out.append({"ref": ref, "path": path, "name": name, "case_key": case_key,
                    "avg_ms": int(avg_ms or 0), "n": int(n or 0), "fails": int(fails or 0),
                    "reason": reason})
    return out


@router.get("/coverage")
async def coverage(request: Request, runs: int = Query(default=RUNS_WINDOW, ge=1, le=MAX_RUNS),
                   user: str = Depends(require_gateway)):
    async with _sf(request)() as s:
        return await _coverage(s, _window(runs))
