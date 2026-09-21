"""tcms tool: the only writer of the TCMS app's `test_runs`, `results` and
`coverage_snapshots`, and the reader the QA agent asks its questions through.

Results are machine-ingested, never agent-asserted (docs/design/25): the QA
pod uploads `ap-verify`'s report files as artifacts, the broker resolves the
`files` argument into the executor's `TOOL_IN_DIR`, and `record_results`
parses what is there. A status never comes from an argument, the run's
`agent` and `run_id` come from the executor's env (broker-verified), and a
file that is none of JUnit / Playwright JSON / Cobertura is refused by name.
`sync_cases` reads `tcms/cases/*.yaml` from the synced checkout this file
lives in, so the DB's cases are what `main` says, stamped with its sha.

The app owns the tables (DDL at boot, `apps/tcms/backend`); this tool
creates nothing and says so when they are missing. Column lists and every
SQL statement come from the app's `tcmsapp/schema.py`, loaded by path from
the same checkout, so the two writers cannot drift.

Executor contract: JSON args on stdin; writes answer with JSON, reads with
lines (≤ 4 KiB, "showing N of M"); non-zero exit + one stderr line on
failure. Env comes from the app's provisioned DB secret (`APP_DB_*`).
"""
import glob
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ingest
from cases import KEY_RE, LAYERS, load_suites

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CASES_DIR = REPO / "tcms" / "cases"
SCHEMA_PATH = REPO / "apps" / "tcms" / "backend" / "tcmsapp" / "schema.py"
SCHEMA = "app_tcms"

LINE_CAP = 4096
DEFAULT_LIMIT = 40
MAX_LIMIT = 200
MAX_RUNS = 500
SLOWEST_N = 15
HISTORY_N = 10
MAX_SUITES = 50
# One suite row can claim a day, no more: `started_at` is derived from the
# sum, and an absurd figure must not overflow the datetime maths.
MAX_SUITE_SECONDS = 86400.0
MAX_EXIT = 1 << 15

SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Directories a checkout root is never inside of: a vendored copy of a test
# file is not the test.
_NEVER_ROOT = {"node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__"}


class ToolError(Exception):
    """An argument or input the model can correct; printed to stderr as is."""


def _load_schema():
    spec = importlib.util.spec_from_file_location("tcmsapp_schema", SCHEMA_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


schema = _load_schema()


def connect():
    """Connect as the app's role, from the secret's components (APP_DB_URL
    carries SQLAlchemy's `+asyncpg` suffix), confined to the app's schema."""
    missing = [k for k in ("APP_DB_HOST", "APP_DB_USER", "APP_DB_PASSWORD",
                           "APP_DB_NAME") if not os.environ.get(k)]
    if missing:
        raise RuntimeError(
            f"missing {', '.join(missing)} — the app-tcms-db secret is not "
            f"bound; is the tcms app provisioned?")
    import psycopg
    return psycopg.connect(
        host=os.environ["APP_DB_HOST"], port=int(os.environ.get("APP_DB_PORT", "5432")),
        user=os.environ["APP_DB_USER"], password=os.environ["APP_DB_PASSWORD"],
        dbname=os.environ["APP_DB_NAME"], connect_timeout=10,
        options=f"-c search_path={SCHEMA}")


# --- small shared helpers ------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonish(v) -> list:
    """A json column as psycopg hands it back (a list) or as a JSON string."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return []
    return v if isinstance(v, list) else []


def _one_line(s, limit: int = 160) -> str:
    return _CONTROL_RE.sub(" ", str(s or "")).strip()[:limit]


def _when(ts) -> str:
    if isinstance(ts, datetime):
        return ts.strftime("%Y-%m-%d %H:%M")
    return str(ts or "")[:16]


def _int_arg(args: dict, name: str, default: int, lo: int, hi: int) -> int:
    v = args.get(name)
    if v in (None, "", 0, False):
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ToolError(f"{name} must be an integer") from None
    return max(lo, min(hi, v))


def page(lines: list, total: int, label: str, empty: str | None = None) -> str:
    """The broker's line shape: as many lines as fit in 4 KiB and a trailer
    that says how many of the total are shown, so a cut list is never
    mistaken for a full one."""
    if not lines:
        return f"no {empty or label} (showing 0 of 0 {label})"
    kept, size = [], 0
    trailer_room = len(f"\nshowing {total} of {total} {label}") + 1
    for line in lines:
        n = len(line.encode("utf-8")) + 1
        if size + n > LINE_CAP - trailer_room:
            break
        kept.append(line)
        size += n
    return "\n".join(kept + [f"showing {len(kept)} of {total} {label}"])


# --- the checkout ------------------------------------------------------------

def resolve_root(repo: Path, rel_file: str, probes: list) -> str | None:
    """The checkout directory a reporter ran from, for one of its files.

    Neither reporter records where it ran: pytest's classname is rootdir-
    relative and Playwright's `file` is testDir-relative, while a case names
    `services/backend/tests/test_config.py`. The root is the unique directory
    under the checkout where `rel_file` exists; when several do (every tool
    has a `test_run.py`), the one whose file defines a probed test wins. None
    when nothing or several fit — a ref is then left as the reporter wrote
    it, never guessed."""
    repo = Path(repo)
    if not rel_file or rel_file.startswith("/") or ".." in rel_file.split("/"):
        return None
    found = []
    # The file name is reporter text, not a pattern: `test_[x].py` is itself.
    for depth in ("*/*/", "*/*/*/", "*/*/*/*/"):
        for p in repo.glob(depth + glob.escape(rel_file)):
            if p.is_file() and not _NEVER_ROOT & set(p.relative_to(repo).parts):
                found.append(p)
    if len(found) > 1 and probes:
        bare = [str(x).split("[", 1)[0].strip() for x in probes if x]
        keep = []
        for p in found:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if any(b and b in text for b in bare):
                keep.append(p)
        found = keep
    if len(found) != 1:
        return None
    root = str(found[0].relative_to(repo))[: -len(rel_file)].rstrip("/")
    return root or None


def source_sha(root: Path) -> str:
    """HEAD of the checkout: `git rev-parse` when git is on PATH, else the
    `.git` files by hand (the executor image has no git), else `unknown`."""
    root = Path(root)
    git_dir = root / ".git"
    if not git_dir.exists():
        return "unknown"
    if shutil.which("git"):
        try:
            r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root), check=False,
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0 and SHA_RE.match(r.stdout.strip()):
                return r.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if not git_dir.is_dir():
        return "unknown"
    try:
        head = (git_dir / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head if SHA_RE.match(head) else "unknown"
        ref = head[5:].strip()
        loose = git_dir / ref
        if loose.is_file():
            sha = loose.read_text().strip()
            return sha if SHA_RE.match(sha) else "unknown"
        packed = git_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text().splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1] == ref and SHA_RE.match(parts[0]):
                    return parts[0]
    except OSError:
        pass
    return "unknown"


# --- writes ------------------------------------------------------------------

def sync_cases(conn, args: dict, repo: Path = REPO) -> dict:
    cases_dir = Path(repo) / "tcms" / "cases"
    if not cases_dir.is_dir():
        raise ToolError(f"no case tree at {cases_dir}")
    suites, errors = load_suites(cases_dir)
    if not suites:
        # Retiring on an empty load would turn a broken mount into a wiped
        # catalogue; nothing is written until at least one file loads.
        raise ToolError("no case file loaded" + (
            ": " + "; ".join(f"{f}: {m}" for f, _, m in errors[:5]) if errors else ""))
    sha = source_sha(repo)
    now = _now()
    keys = []
    with conn.cursor() as cur:
        for suite in suites:
            for c in suite.cases:
                cur.execute(schema.UPSERT_CASE, (
                    c.key, suite.name, suite.area, c.title, c.layer, c.priority,
                    json.dumps(list(c.preconditions)), json.dumps(list(c.steps)),
                    c.expected, json.dumps(list(c.automation)), json.dumps(list(c.tags)),
                    json.dumps(list(c.tickets)), "active", now, sha))
                keys.append(c.key)
        errored = sorted({f.rsplit(".", 1)[0] for f, _, _ in errors})
        cur.execute(schema.RETIRE_CASES, (now, keys, errored))
        retired = cur.rowcount
    conn.commit()
    return {"synced": len(keys), "retired": retired, "suites": len(suites),
            "source_sha": sha,
            "errors": [{"file": f, "key": k, "message": m} for f, k, m in errors]}


def _identity() -> tuple:
    agent = os.environ.get("TOOL_CALLER_AGENT", "").strip()
    run_id = os.environ.get("TOOL_RUN_ID", "").strip()
    if not agent or not run_id:
        raise ToolError("no verified caller identity (TOOL_CALLER_AGENT / TOOL_RUN_ID "
                        "unset) — a run is recorded under the executor's identity, "
                        "never an argument")
    return agent, run_id


def _verify_rows(verify) -> tuple:
    """(verify_ok, suites) from ap-verify's verify.json object: only the
    three fields the app shows, capped — the tails stay in the PR body."""
    if not isinstance(verify, dict):
        return None, []
    ok = verify.get("ok")
    rows = []
    for s in (verify.get("suites") or [])[:MAX_SUITES]:
        if not isinstance(s, dict):
            continue
        try:
            seconds = float(s.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0.0
        if math.isnan(seconds):
            seconds = 0.0
        seconds = round(max(0.0, min(MAX_SUITE_SECONDS, seconds)), 2)
        exit_code = s.get("exit")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool) \
                or not -MAX_EXIT <= exit_code <= MAX_EXIT:
            exit_code = None
        rows.append({"name": _one_line(s.get("name"), 100), "exit": exit_code,
                     "seconds": seconds})
    return (ok if isinstance(ok, bool) else None), rows


def _read_files(in_dir: Path) -> list:
    """[(name, kind, items, skipped)] for every file the broker staged, or the one
    error that names the file that is not a report."""
    files = sorted(p for p in in_dir.iterdir() if p.is_file()) if in_dir.is_dir() else []
    if not files:
        raise ToolError("record_results needs files: upload ap-verify's report files "
                        "(junit XML, Playwright JSON, coverage.xml) with bin/ap-upload "
                        "and pass their artifact ids in `files`")
    out = []
    for p in files:
        data = p.read_bytes()
        kind = ingest.sniff(p.name, data)
        if kind is None:
            raise ToolError(f"{p.name}: not a JUnit XML, Playwright JSON or Cobertura "
                            f"coverage file — record_results ingests only report files")
        try:
            items, skipped = (ingest.parse_junit_report(data) if kind == "junit"
                              else (ingest.parse(kind, data), 0))
        except ingest.Refused as e:
            raise ToolError(f"{p.name}: {e}") from None
        out.append((p.name, kind, items, skipped))
    return out


def _case_refs(conn) -> dict:
    with conn.cursor() as cur:
        cur.execute(schema.ACTIVE_CASE_REFS)
        rows = cur.fetchall()
    refs = {}
    for key, layer, automation in rows:
        for ref in _jsonish(automation):
            refs.setdefault(ref, (key, layer))
    return refs


def _resolve(results: list, repo: Path) -> tuple:
    """Results with their paths rewritten to checkout paths, and whether any
    path in this file could not be placed. One report is one reporter run,
    so a path the checkout cannot place adopts the root its siblings agreed
    on."""
    by_path = {}
    for r in results:
        by_path.setdefault(r.path, []).append(r.name)
    roots = {path: resolve_root(repo, path, names) for path, names in by_path.items()}
    agreed = {r for r in roots.values() if r}
    if len(agreed) == 1:
        roots = {p: (r or next(iter(agreed))) for p, r in roots.items()}
    unresolved = any(r is None for r in roots.values())
    return [r.with_root(roots[r.path] or "") for r in results], unresolved


def record_results(conn, args: dict, repo: Path = REPO) -> dict:
    agent, run_id = _identity()
    sha = str(args.get("commit_sha") or "").strip()
    if not SHA_RE.match(sha):
        raise ToolError("commit_sha must be the 7–40 hex sha the reports were produced on")
    sha = sha.lower()
    branch = _one_line(args.get("branch"), 200) or None
    verify_ok, suites = _verify_rows(args.get("verify"))

    files = _read_files(Path(os.environ.get("TOOL_IN_DIR") or "/nonexistent"))
    refs = _case_refs(conn)

    result_rows, coverage_rows, out_files, unresolved_files = [], [], [], []
    totals, skipped = {}, 0
    for name, kind, items, skipped_here in files:
        if kind == "cobertura":
            coverage_rows += [(r.package, r.lines_covered, r.lines_total, r.branch_rate)
                              for r in items]
            out_files.append({"name": name, "kind": kind, "packages": len(items)})
            continue
        skipped += skipped_here
        results, unresolved = _resolve(items, repo)
        if unresolved:
            unresolved_files.append(name)
        for r in results:
            case = refs.get(r.ref)
            layer = "unit" if case and case[1] == "unit" else ingest.layer_of(r.ref)
            result_rows.append((r.ref, case[0] if case else None, r.status,
                                r.duration_ms, r.message, layer))
            totals[r.status] = totals.get(r.status, 0) + 1
        out_files.append({"name": name, "kind": kind, "results": len(results)})

    finished = _now()
    started = finished - timedelta(seconds=sum(s["seconds"] for s in suites))
    try:
        with conn.cursor() as cur:
            cur.execute(schema.INSERT_RUN, (sha, branch, run_id, agent, started, finished,
                                            verify_ok, json.dumps(suites)))
            inserted = cur.fetchone()
            if inserted is None:
                # The same platform run already recorded (a retried tool
                # call): answer with that run and write nothing twice.
                cur.execute(schema.RUN_BY_RUN_ID, (run_id,))
                existing = cur.fetchone()
                conn.rollback()
                return {"test_run_id": existing[0] if existing else None,
                        "already_recorded": True, "commit_sha": sha,
                        "files": out_files, "totals": totals}
            test_run_id = inserted[0]
            if result_rows:
                cur.executemany(schema.INSERT_RESULT,
                                [(test_run_id, *row) for row in result_rows])
            if coverage_rows:
                cur.executemany(schema.INSERT_COVERAGE,
                                [(test_run_id, *row) for row in coverage_rows])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    linked = sum(1 for row in result_rows if row[1])
    return {"test_run_id": test_run_id, "already_recorded": False, "commit_sha": sha,
            "files": out_files,
            "totals": totals, "linked": linked, "unlinked_refs": len(result_rows) - linked,
            "coverage_packages": len(coverage_rows), "skipped_testcases": skipped,
            "unresolved_files": unresolved_files}


# --- reads -------------------------------------------------------------------

def action_cases(conn, args: dict) -> str:
    where, params = [], []
    layer = args.get("layer")
    if layer:
        if layer not in LAYERS:
            raise ToolError(f"layer must be one of {', '.join(LAYERS)}")
        where.append("layer = %s")
        params.append(layer)
    status = args.get("status")
    if status:
        if status not in schema.CASE_STATUSES:
            raise ToolError("status must be active or retired")
        where.append("status = %s")
        params.append(status)
    area = _one_line(args.get("area"), 60)
    if area:
        where.append("area = %s")
        params.append(area)
    q = _one_line(args.get("q"), 200)
    if q:
        where.append("(key ILIKE %s OR title ILIKE %s)")
        params += [f"%{q}%", f"%{q}%"]
    if args.get("unlinked"):
        where.append("layer <> 'manual' AND NOT EXISTS "
                     "(SELECT 1 FROM results r WHERE r.case_key = cases.key)")
    limit = _int_arg(args, "limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM cases" + clause, params)
        total = (cur.fetchone() or (0,))[0]
        cur.execute("SELECT key, suite, area, title, layer, priority, status, automation "
                    "FROM cases" + clause + " ORDER BY key LIMIT %s", params + [limit])
        rows = cur.fetchall()
    lines = [f"{key}  [{layer} {priority} {status}]  {_one_line(title)}  "
             f"({len(_jsonish(automation))} refs)"
             for key, _suite, _area, title, layer, priority, status, automation in rows]
    return page(lines, total, "cases")


def action_case(conn, args: dict) -> str:
    key = str(args.get("key") or "").strip()
    if not KEY_RE.match(key):
        raise ToolError("key must be a case key like relay.mention-summons")
    with conn.cursor() as cur:
        cur.execute(schema.CASE_BY_KEY, (key,))
        row = cur.fetchone()
    if not row:
        return f"no case {key}"
    c = dict(zip(schema.COLUMNS["cases"], row))
    lines = [f"{c['key']}  [{c['layer']} {c['priority']} {c['status']}]  {_one_line(c['title'])}",
             (f"suite {c['suite']} · area {c['area']} · synced {_when(c['synced_at'])} "
              f"@ {str(c['source_sha'] or '')[:12]}")]
    pre = _jsonish(c["preconditions"])
    if pre:
        lines.append("preconditions: " + " | ".join(_one_line(p, 200) for p in pre))
    for i, step in enumerate(_jsonish(c["steps"]), 1):
        lines.append(f"  {i}. {_one_line(step, 300)}")
    lines.append("expected: " + _one_line(c["expected"], 500))
    tags, tickets = _jsonish(c["tags"]), _jsonish(c["tickets"])
    if tags or tickets:
        lines.append(f"tags {', '.join(map(str, tags)) or '-'} · tickets "
                     f"{', '.join(map(str, tickets)) or '-'}")
    refs = _jsonish(c["automation"])
    if not refs:
        lines.append("automation: none (manual)")
    for ref in refs:
        lines.append(f"ref {ref}")
        with conn.cursor() as cur:
            cur.execute(schema.RESULTS_FOR_REF, (ref, HISTORY_N))
            hist = cur.fetchall()
        if not hist:
            lines.append("  no results yet")
        for status, ms, message, sha, when in hist:
            msg = _one_line((message or "").splitlines()[0] if message else "", 120)
            lines.append(f"  {status:<5} {int(ms or 0):>7} ms  {str(sha or '')[:7]}  "
                         f"{_when(when)}" + (f"  {msg}" if msg else ""))
    return page(lines, len(lines), "lines")


def action_coverage_gaps(conn, args: dict) -> str:
    threshold = args.get("threshold_pct")
    try:
        threshold = float(threshold) if threshold not in (None, "") else 70.0
    except (TypeError, ValueError):
        raise ToolError("threshold_pct must be a number") from None
    threshold = max(0.0, min(100.0, threshold))
    with conn.cursor() as cur:
        cur.execute(schema.COVERAGE_GAPS, (threshold,))
        packages = cur.fetchall()
        cur.execute(schema.MANUAL_CASES)
        manual = cur.fetchall()
        cur.execute(schema.UNLINKED_REFS)
        unlinked = cur.fetchall()
        cur.execute(schema.CASES_UNMATCHED_LATEST)
        unmatched = cur.fetchall()
    lines = [f"packages under {threshold:g}% (latest run): {len(packages)}"]
    lines += [f"  {pkg}  {cov}/{tot}  {float(pct):.1f}%" for pkg, cov, tot, pct in packages]
    lines.append(f"cases with no automation (manual): {len(manual)}")
    lines += [f"  {key}  {_one_line(title)}" for key, title in manual]
    lines.append(f"refs no case names (latest run): {len(unlinked)}")
    lines += [f"  {ref}" for (ref,) in unlinked]
    lines.append(f"automated cases with no result in the latest run: {len(unmatched)}")
    lines += [f"  {key}" for (key,) in unmatched]
    return page(lines, len(lines), "lines")


def action_runtime_report(conn, args: dict) -> str:
    runs = _int_arg(args, "runs", 10, 1, MAX_RUNS)
    with conn.cursor() as cur:
        cur.execute(schema.RUNTIME_BY_LAYER, (runs,))
        by_layer = cur.fetchall()
        cur.execute(schema.SLOWEST, (runs, SLOWEST_N))
        slowest = cur.fetchall()
    per_run, order = {}, []
    for run_id, sha, started, layer, total_ms, n in by_layer:
        if run_id not in per_run:
            per_run[run_id] = {"sha": sha, "started": started, "layers": {}, "total": 0}
            order.append(run_id)
        secs = int(total_ms or 0) // 1000
        per_run[run_id]["layers"][layer] = (secs, int(n or 0))
        per_run[run_id]["total"] += secs
    lines = [f"seconds by layer, last {len(order)} runs (newest first):"]
    for run_id in order:
        r = per_run[run_id]
        parts = " · ".join(f"{layer} {secs}s ({n})" for layer, (secs, n)
                           in sorted(r["layers"].items()))
        lines.append(f"  {str(r['sha'] or '')[:7]}  {_when(r['started'])}  {parts}  "
                     f"total {r['total']}s")
    if len(order) >= 2:
        newest, oldest = per_run[order[0]]["total"], per_run[order[-1]]["total"]
        lines.append(f"trend: {oldest}s → {newest}s ({newest - oldest:+d}s over "
                     f"{len(order)} runs, oldest → newest)")
    elif order:
        lines.append("trend: one run only")
    lines.append(f"slowest {len(slowest)} refs (mean over the window):")
    lines += [f"  {int(avg_ms or 0) / 1000:.1f}s  [{layer}]  {ref}  (max {int(max_ms or 0) / 1000:.1f}s, n={n})"
              for ref, layer, avg_ms, max_ms, n in slowest]
    return page(lines, len(lines), "lines")


def action_flaky(conn, args: dict) -> str:
    runs = _int_arg(args, "runs", 20, 1, MAX_RUNS)
    with conn.cursor() as cur:
        cur.execute(schema.FLAKY, (runs,))
        rows = cur.fetchall()
    lines = [f"{ref}  {fails} fail / {passes} pass / {flaky} flaky · {flips} same-commit flips "
             f"· {commits} failing commits"
             for ref, fails, passes, flaky, commits, flips in rows]
    return page(lines, len(rows), "refs", empty="flaky refs")


def action_prune_candidates(conn, args: dict) -> str:
    runs = _int_arg(args, "runs", 50, 1, MAX_RUNS)
    with conn.cursor() as cur:
        cur.execute(schema.PRUNE_CANDIDATES, (runs,))
        rows = cur.fetchall()
    lines = ["(duplicate-coverage candidates are omitted: no coverage contexts are recorded)"]
    lines += [f"{reason}  {ref}  {int(avg_ms or 0) / 1000:.1f}s mean over {n} runs, {fails} fails"
              f"  case={case_key or '-'}"
              for ref, case_key, avg_ms, n, fails, reason in rows]
    return page(lines, len(lines), "lines")


def _is_db_error(e: BaseException) -> bool:
    return any(c.__module__.split(".")[0] == "psycopg" for c in type(e).__mro__)


ACTIONS = {
    "sync_cases": sync_cases,
    "record_results": record_results,
    "cases": action_cases,
    "case": action_case,
    "coverage_gaps": action_coverage_gaps,
    "runtime_report": action_runtime_report,
    "flaky": action_flaky,
    "prune_candidates": action_prune_candidates,
}
WRITES = {"sync_cases", "record_results"}


def main() -> int:
    args = json.load(sys.stdin)
    action = args.get("action")
    if action not in ACTIONS:
        print(f"unknown action {action!r}", file=sys.stderr)
        return 2
    try:
        conn = connect()
    except Exception as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        try:
            out = ACTIONS[action](conn, args)
        except (ToolError, ingest.Refused) as e:
            print(str(e), file=sys.stderr)
            return 2
        except Exception as e:
            # Only a driver error is an outage; anything else is this tool's
            # own fault and must say so, or a bug reads as "not deployed".
            if _is_db_error(e):
                print(f"could not use app_tcms ({str(e)[:300]}) — the tcms app owns these "
                      f"tables and creates them at startup; is it deployed?", file=sys.stderr)
            else:
                print(f"tcms {action} failed: {type(e).__name__}: {str(e)[:300]}",
                      file=sys.stderr)
            return 2
        print(json.dumps(out) if action in WRITES else out)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
