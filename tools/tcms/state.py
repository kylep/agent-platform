"""TCMS over database-owned App records; reports remain derived from machine data.

The executor gives this process only a loopback, one-call App-data proxy. A
staging set commits a run's results and coverage together; an incomplete run
is hidden until that commit has succeeded, so interrupted calls can resume.
"""
from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import ingest
import legacy
from cases import load_suites

APP = "tcms"
MAX_RESPONSE = 16 * 1024 * 1024


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise legacy.ToolError("TCMS App-data proxy redirected")


def _base(args):
    raw = (args.get("_app_data") or {}).get("url", "")
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise legacy.ToolError("TCMS App-data proxy is unavailable")
    return raw.rstrip("/") + "/agent/records/"


def _call(base, action, **body):
    if not action.replace("/", "").replace("_", "").isalpha():
        raise legacy.ToolError("invalid App-data operation")
    req = Request(base + action, method="POST", data=json.dumps(
        {"app": APP, **body}, separators=(",", ":"), default=str).encode(),
        headers={"Content-Type": "application/json"})
    try:
        response = build_opener(_NoRedirect()).open(req, timeout=60)
    except HTTPError as exc:
        raw = exc.read(4096)
        try:
            detail = json.loads(raw).get("detail", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except (ValueError, AttributeError):
            message = None
        raise legacy.ToolError(message or f"TCMS App data returned {exc.code}") from None
    with response:
        raw = response.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise legacy.ToolError("TCMS App-data response exceeded the read limit")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise legacy.ToolError("invalid TCMS App-data response")
    return result


def _rows(base, view, params=None, *, limit=200, max_rows=100_000):
    rows, cursor = [], None
    while True:
        out = _call(base, "query", view=view, params=params or {}, limit=limit,
                    **({"cursor": cursor} if cursor else {}))
        rows.extend({"id": row["id"], "version": row["values"]["version"],
                     **(row.get("values") or {})} for row in out.get("rows", []))
        if len(rows) > max_rows:
            raise legacy.ToolError(f"TCMS {view} exceeded {max_rows} rows")
        cursor = out.get("next_cursor")
        if not cursor:
            return rows


def _record(base, collection, record_id):
    out = _call(base, "get", collection=collection, id=record_id)
    return {"id": out["id"], "version": out["values"]["version"], **(out.get("values") or {})}


def _stage(base, collections, batches):
    opened = _call(base, "staging/open", collections=collections)
    set_id = opened["set_id"]
    try:
        for collection, mode, records in batches:
            for start in range(0, len(records), 500):
                _call(base, "staging/stage", set_id=set_id, collection=collection,
                      mode=mode, records=records[start:start + 500])
        return _call(base, "staging/commit", set_id=set_id)
    except Exception:
        try:
            _call(base, "staging/abandon", set_id=set_id)
        except Exception:  # noqa: BLE001, S110 - retain the staging failure
            pass
        raise


def sync_cases(base, args):
    cases_dir = legacy.REPO / "tcms" / "cases"
    if not cases_dir.is_dir():
        raise legacy.ToolError(f"no case tree at {cases_dir}")
    suites, errors = load_suites(cases_dir)
    if not suites:
        raise legacy.ToolError("no case file loaded")
    sha, now = legacy.source_sha(legacy.REPO), datetime.now(timezone.utc).isoformat()
    wanted = {}
    for suite in suites:
        for case in suite.cases:
            wanted[case.key] = {
                "key": case.key, "suite": suite.name, "area": suite.area,
                "title": case.title, "layer": case.layer, "priority": case.priority,
                "preconditions": list(case.preconditions), "steps": list(case.steps),
                "expected": case.expected, "automation": list(case.automation),
                "tags": list(case.tags), "tickets": list(case.tickets),
                "status": "active", "synced_at": now, "source_sha": sha,
            }
    errored = {f.rsplit(".", 1)[0] for f, _, _ in errors}
    old = _rows(base, "cases_recent")
    retired = 0
    for row in old:
        if (row["key"] not in wanted and row["status"] == "active"
                and row["key"].split(".", 1)[0] not in errored):
            wanted[row["key"]] = {k: v for k, v in row.items() if k not in (
                "id", "version", "created_at", "updated_at", "author", "via",
                "collection_version")}
            wanted[row["key"]].update(status="retired", synced_at=now)
            retired += 1
    _stage(base, ["cases"], [("cases", "upsert", list(wanted.values()))])
    return {"synced": len(wanted) - retired, "retired": retired,
            "suites": len(suites), "source_sha": sha,
            "errors": [{"file": f, "key": k, "message": m} for f, k, m in errors]}


def _case_refs(base):
    refs = {}
    for case in _rows(base, "cases_recent"):
        if case["status"] != "active":
            continue
        for ref in case.get("automation") or []:
            refs.setdefault(ref, (case["id"], case["key"], case["layer"]))
    return refs


def record_results(base, args):
    agent, run_id = legacy._identity()
    sha = str(args.get("commit_sha") or "").strip().lower()
    if not legacy.SHA_RE.fullmatch(sha):
        raise legacy.ToolError("commit_sha must be the 7–40 hex sha the reports were produced on")
    branch = legacy._one_line(args.get("branch"), 200) or None
    verify_ok, suites = legacy._verify_rows(args.get("verify"))
    files = legacy._read_files(legacy.Path(os.environ.get("TOOL_IN_DIR") or "/nonexistent"))
    refs = _case_refs(base)
    results, coverage, out_files, unresolved_files = [], [], [], []
    totals, skipped = Counter(), 0
    for name, kind, items, skipped_here in files:
        if kind == "cobertura":
            coverage.extend({"package": r.package, "lines_covered": r.lines_covered,
                             "lines_total": r.lines_total, "branch_rate": r.branch_rate}
                            for r in items)
            out_files.append({"name": name, "kind": kind, "packages": len(items)})
            continue
        skipped += skipped_here
        parsed, unresolved = legacy._resolve(items, legacy.REPO)
        if unresolved:
            unresolved_files.append(name)
        for item in parsed:
            case = refs.get(item.ref)
            layer = "unit" if case and case[2] == "unit" else ingest.layer_of(item.ref)
            results.append({"ref": item.ref, "case": case[0] if case else None,
                            "status": item.status, "duration_ms": item.duration_ms,
                            "message": item.message, "layer": layer})
            totals[item.status] += 1
        out_files.append({"name": name, "kind": kind, "results": len(parsed)})
    existing = _rows(base, "run_by_runid", {"run_id": run_id}, limit=1)
    if existing and existing[0]["ingest_state"] == "complete":
        return {"test_run_id": existing[0]["id"], "already_recorded": True,
                "commit_sha": sha, "files": out_files, "totals": dict(totals)}
    if not existing:
        finished = datetime.now(timezone.utc)
        started = finished - timedelta(seconds=sum(s["seconds"] for s in suites))
        values = {"commit_sha": sha, "branch": branch, "run_id": run_id,
                  "agent": agent, "started_at": started.isoformat(),
                  "finished_at": finished.isoformat(), "verify_ok": verify_ok,
                  "suites": suites, "ingest_state": "ingesting",
                  "result_count": len(results),
                  **{status + "_count": totals[status] for status in (
                      "pass", "fail", "skip", "flaky", "error")}}
        _call(base, "create", request_id="tcms-run:" + run_id,
              collection="runs", values=values)
        existing = _rows(base, "run_by_runid", {"run_id": run_id}, limit=1)
    run = existing[0]
    if run["commit_sha"] != sha or run["result_count"] != len(results):
        raise legacy.ToolError("this platform run already has a different TCMS report")
    for ordinal, item in enumerate(results):
        item.update(run=run["id"], ordinal=ordinal)
    for ordinal, item in enumerate(coverage):
        item.update(run=run["id"], ordinal=ordinal)
    previous_results = _rows(base, "results_for_run", {"run": run["id"]})
    previous_coverage = _rows(base, "coverage_for_run", {"run": run["id"]})
    if previous_results or previous_coverage:
        # Staging commits both collections in one database transaction. An
        # interrupted call can see all or none; a partial set is not guessed
        # complete. The run stays hidden for a human to inspect.
        if len(previous_results) != len(results) or len(previous_coverage) != len(coverage):
            raise legacy.ToolError("incomplete TCMS result batch; inspect the run before retry")
    else:
        batches = []
        if results:
            batches.append(("results", "insert", results))
        if coverage:
            batches.append(("coverage", "insert", coverage))
        if batches:
            _stage(base, [name for name, _, _ in batches], batches)
    _call(base, "update", request_id="tcms-complete:" + run_id,
          collection="runs", id=run["id"], expected_version=run["version"],
          values={"ingest_state": "complete"})
    linked = sum(bool(item.get("case")) for item in results)
    return {"test_run_id": run["id"], "already_recorded": False,
            "commit_sha": sha, "files": out_files, "totals": dict(totals),
            "linked": linked, "unlinked_refs": len(results) - linked,
            "coverage_packages": len(coverage), "skipped_testcases": skipped,
            "unresolved_files": unresolved_files}


def _window(base, args, default):
    n = legacy._int_arg(args, "runs", default, 1, legacy.MAX_RUNS)
    runs = _rows(base, "runs_recent")[:n]
    ids = {run["id"] for run in runs}
    results = [row for row in _rows(base, "results_all") if row["run"] in ids]
    return runs, results


def read(base, args):
    action = args["action"]
    if action == "cases":
        rows = _rows(base, "cases_recent")
        if args.get("unlinked"):
            linked = {row.get("case") for row in _rows(base, "results_all")}
            rows = [row for row in rows if row["layer"] != "manual" and row["id"] not in linked]
        for key in ("layer", "status", "area"):
            if args.get(key):
                rows = [row for row in rows if row[key] == args[key]]
        if args.get("q"):
            q = legacy._one_line(args["q"], 200).lower()
            rows = [row for row in rows if q in row["key"].lower() or q in row["title"].lower()]
        total = len(rows)
        rows = rows[:legacy._int_arg(args, "limit", 40, 1, legacy.MAX_LIMIT)]
        return legacy.page([f'{r["key"]}  [{r["layer"]} {r["priority"]} {r["status"]}]  '
                            f'{legacy._one_line(r["title"])}  ({len(r.get("automation") or [])} refs)'
                            for r in rows], total, "cases")
    if action == "case":
        key = args.get("key")
        matches = [row for row in _rows(base, "cases_recent") if row["key"] == key]
        if not matches:
            return f"no case {key}"
        case = matches[0]
        results = [row for row in _rows(base, "results_all") if row.get("case") == case["id"]]
        lines = [f'{case["key"]}: {case["title"]}', f'layer={case["layer"]} status={case["status"]}',
                 f'expected: {case.get("expected") or "-"}']
        lines += [f'{r["status"]}: {r["ref"]} ({r["duration_ms"]} ms)' for r in results[:10]]
        return legacy.page(lines, len(lines), "lines")
    if action == "coverage_gaps":
        threshold = float(args.get("threshold_pct") or 70)
        runs = _rows(base, "runs_recent")
        coverage = _rows(base, "coverage_all")
        latest = next((run["id"] for run in runs if any(c["run"] == run["id"] for c in coverage)), None)
        rows = [c for c in coverage if c["run"] == latest and c["lines_total"] > 0
                and 100 * c["lines_covered"] / c["lines_total"] < threshold]
        rows.sort(key=lambda c: c["lines_covered"] / c["lines_total"])
        return legacy.page([f'{100*c["lines_covered"]/c["lines_total"]:.1f}%  {c["package"]}'
                            for c in rows], len(rows), "packages")
    runs, results = _window(base, args, {"runtime_report": 10, "flaky": 20,
                                        "prune_candidates": 50}[action])
    if action == "runtime_report":
        totals = defaultdict(lambda: Counter())
        for r in results:
            totals[r["run"]][r["layer"]] += r["duration_ms"]
        lines = [f'{run["commit_sha"][:10]}  ' + "  ".join(
            f'{layer}={ms/1000:.1f}s' for layer, ms in sorted(totals[run["id"]].items()))
            for run in runs]
        return legacy.page(lines, len(lines), "runs")
    by_ref = defaultdict(list)
    for r in results:
        by_ref[r["ref"]].append(r)
    if action == "flaky":
        found = []
        for ref, values in by_ref.items():
            tally = Counter(r["status"] for r in values)
            if (tally["pass"] and tally["fail"] + tally["error"]) or tally["flaky"]:
                found.append((tally["fail"] + tally["error"] + tally["flaky"], ref, tally))
        found.sort(reverse=True)
        lines = [f'{ref}: {t["fail"] + t["error"]} fail, {t["pass"]} pass, '
                 f'{t["flaky"]} flaky' for _, ref, t in found]
        return legacy.page(lines, len(lines), "refs")
    cases = {c["id"]: c for c in _rows(base, "cases_recent")}
    averages = sorted(((sum(r["duration_ms"] for r in values)/len(values), ref, values)
                       for ref, values in by_ref.items()), reverse=True)
    cutoff = averages[max(0, int(len(averages) * 0.1) - 1)][0] if averages else 0
    lines = []
    for avg, ref, values in averages:
        retired = any(cases.get(r.get("case"), {}).get("status") == "retired" for r in values)
        never_failed = all(r["status"] == "pass" for r in values)
        if retired or (never_failed and len(values) > 1 and avg >= cutoff):
            lines.append(f'{"retired-case" if retired else "never-failed-slow"}  '
                         f'{ref}  {avg/1000:.1f}s mean over {len(values)} runs')
    return legacy.page(lines, len(lines), "lines")


def run(args):
    base = _base(args)
    action = args.get("action")
    if action == "sync_cases":
        return sync_cases(base, args)
    if action == "record_results":
        return record_results(base, args)
    if action in {"cases", "case", "coverage_gaps", "runtime_report", "flaky",
                  "prune_candidates"}:
        return read(base, args)
    raise legacy.ToolError(f"unknown action {action!r}")
