"""The `app_tcms` tables as both writers see them: the column lists and every
SQL statement the `tcms` tool (`tools/tcms/run.py`) and this app share.

The app owns the DDL (design 25: an app creates its tables at boot, a tool
that writes them creates nothing). The tool loads this file by path from the
synced checkout, so a column or a query has exactly one home and the tool's
tests can pin their INSERTs to `COLUMNS` here. Every statement is a constant
with `%s` placeholders: nothing from a report file, a case file or a tool
argument is ever spliced into SQL text. Stdlib only — the tool image has no
SQLAlchemy and this module must import there.

JSON columns take a JSON *string* (`%s::jsonb`) rather than a driver-specific
wrapper, so the tool (psycopg) and the app (asyncpg through SQLAlchemy) bind
them the same way.
"""

COLUMNS = {
    "cases": [
        "key", "suite", "area", "title", "layer", "priority", "preconditions",
        "steps", "expected", "automation", "tags", "tickets", "status",
        "synced_at", "source_sha",
    ],
    "test_runs": [
        "id", "commit_sha", "branch", "run_id", "agent", "started_at",
        "finished_at", "verify_ok", "suites", "published_at",
    ],
    "results": [
        "id", "test_run_id", "ref", "case_key", "status", "duration_ms",
        "message", "layer",
    ],
    "coverage_snapshots": [
        "id", "test_run_id", "package", "lines_covered", "lines_total",
        "branch_rate",
    ],
}

# Unique constraints the app's DDL must declare (create_all), as
# {table: [columns]}: `INSERT_RUN`'s ON CONFLICT infers the `run_id` one, so
# a platform run records once however many times the tool call is retried.
UNIQUE = {"test_runs": ["run_id"]}

CASE_STATUSES = ("active", "retired")
RESULT_STATUSES = ("pass", "fail", "skip", "flaky", "error")

# --- writes (the tool) ---------------------------------------------------------

# Returns no row when this run_id is already recorded (see UNIQUE).
INSERT_RUN = (
    "INSERT INTO test_runs (commit_sha, branch, run_id, agent, started_at, "
    "finished_at, verify_ok, suites) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
    "ON CONFLICT (run_id) DO NOTHING RETURNING id"
)

# Params: (run_id,). The run a swallowed INSERT_RUN collided with.
RUN_BY_RUN_ID = "SELECT id FROM test_runs WHERE run_id = %s"

INSERT_RESULT = (
    "INSERT INTO results (test_run_id, ref, case_key, status, duration_ms, "
    "message, layer) VALUES (%s, %s, %s, %s, %s, %s, %s)"
)

INSERT_COVERAGE = (
    "INSERT INTO coverage_snapshots (test_run_id, package, lines_covered, "
    "lines_total, branch_rate) VALUES (%s, %s, %s, %s, %s)"
)

UPSERT_CASE = (
    "INSERT INTO cases (key, suite, area, title, layer, priority, preconditions, "
    "steps, expected, automation, tags, tickets, status, synced_at, source_sha) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, "
    "%s::jsonb, %s::jsonb, %s, %s, %s) "
    "ON CONFLICT (key) DO UPDATE SET suite = EXCLUDED.suite, area = EXCLUDED.area, "
    "title = EXCLUDED.title, layer = EXCLUDED.layer, priority = EXCLUDED.priority, "
    "preconditions = EXCLUDED.preconditions, steps = EXCLUDED.steps, "
    "expected = EXCLUDED.expected, automation = EXCLUDED.automation, "
    "tags = EXCLUDED.tags, tickets = EXCLUDED.tickets, status = EXCLUDED.status, "
    "synced_at = EXCLUDED.synced_at, source_sha = EXCLUDED.source_sha"
)

# Params: (synced_at, the keys this sync loaded, the suites whose file failed
# to load). A key absent from the checkout is retired — unless its whole
# file was dropped for a validation error, in which case its rows stay as
# they were: a typo in one file must not retire an area.
RETIRE_CASES = (
    "UPDATE cases SET status = 'retired', synced_at = %s "
    "WHERE status = 'active' AND key <> ALL(%s) AND split_part(key, '.', 1) <> ALL(%s)"
)

# --- reads (both sides) --------------------------------------------------------

# The last N runs, newest first — every windowed read starts here.
_RECENT = (
    "recent AS (SELECT id, commit_sha, started_at FROM test_runs "
    "ORDER BY started_at DESC, id DESC LIMIT %s)"
)

# Params: (runs,). A ref that both failed and passed inside the window, or
# that Playwright itself marked flaky. `same_commit_flips` counts the fails
# that a pass on the SAME commit contradicts — the strongest flake signal.
FLAKY = (
    "WITH " + _RECENT + " "
    "SELECT r.ref, "
    "COUNT(*) FILTER (WHERE r.status IN ('fail', 'error')) AS fails, "
    "COUNT(*) FILTER (WHERE r.status = 'pass') AS passes, "
    "COUNT(*) FILTER (WHERE r.status = 'flaky') AS flaky, "
    "COUNT(DISTINCT rc.commit_sha) FILTER (WHERE r.status IN ('fail', 'error')) AS failing_commits, "
    "COUNT(*) FILTER (WHERE r.status IN ('fail', 'error') AND EXISTS ("
    "SELECT 1 FROM results p JOIN recent pc ON pc.id = p.test_run_id "
    "WHERE p.ref = r.ref AND p.status = 'pass' AND pc.commit_sha = rc.commit_sha)) "
    "AS same_commit_flips "
    "FROM results r JOIN recent rc ON rc.id = r.test_run_id "
    "GROUP BY r.ref "
    "HAVING (COUNT(*) FILTER (WHERE r.status IN ('fail', 'error')) > 0 "
    "AND COUNT(*) FILTER (WHERE r.status = 'pass') > 0) "
    "OR COUNT(*) FILTER (WHERE r.status = 'flaky') > 0 "
    "ORDER BY same_commit_flips DESC, fails DESC, r.ref"
)

# Params: (runs, limit). Mean duration per ref over the window.
SLOWEST = (
    "WITH " + _RECENT + " "
    "SELECT r.ref, r.layer, AVG(r.duration_ms)::int AS avg_ms, MAX(r.duration_ms) AS max_ms, "
    "COUNT(*) AS n "
    "FROM results r JOIN recent rc ON rc.id = r.test_run_id "
    "GROUP BY r.ref, r.layer ORDER BY avg_ms DESC, r.ref LIMIT %s"
)

# Params: (runs,). Never failed across the window AND in the slowest decile,
# or linked to a retired case — those first, being the surest prune, so a
# capped page never hides them behind the slow list. Duplicate-coverage
# candidates need coverage contexts, which no snapshot records, so that
# reason is not computed here.
PRUNE_CANDIDATES = (
    "WITH " + _RECENT + ", "
    "agg AS (SELECT r.ref, MAX(r.case_key) AS case_key, AVG(r.duration_ms) AS avg_ms, "
    "COUNT(*) AS n, COUNT(*) FILTER (WHERE r.status IN ('fail', 'error', 'flaky')) AS fails "
    "FROM results r JOIN recent rc ON rc.id = r.test_run_id GROUP BY r.ref), "
    "ranked AS (SELECT a.*, PERCENT_RANK() OVER (ORDER BY a.avg_ms) AS pr FROM agg a) "
    "SELECT k.ref, k.case_key, k.avg_ms::int AS avg_ms, k.n, k.fails, "
    "CASE WHEN c.status = 'retired' THEN 'retired-case' ELSE 'never-failed-slow' END AS reason "
    "FROM ranked k LEFT JOIN cases c ON c.key = k.case_key "
    "WHERE (k.fails = 0 AND k.pr >= 0.9 AND k.n > 1) OR c.status = 'retired' "
    "ORDER BY CASE WHEN c.status = 'retired' THEN 0 ELSE 1 END, k.avg_ms DESC, k.ref"
)

# Params: (threshold_pct,). Packages under the threshold in the latest run
# that recorded coverage.
COVERAGE_GAPS = (
    "SELECT package, lines_covered, lines_total, "
    "ROUND(100.0 * lines_covered / lines_total, 1) AS pct "
    "FROM coverage_snapshots "
    "WHERE test_run_id = (SELECT MAX(test_run_id) FROM coverage_snapshots) "
    "AND lines_total > 0 AND 100.0 * lines_covered / lines_total < %s "
    "ORDER BY pct ASC, package"
)

# Active cases with no automation at all.
MANUAL_CASES = (
    "SELECT key, title FROM cases WHERE status = 'active' AND layer = 'manual' ORDER BY key"
)

# Refs the latest run recorded that no case names.
UNLINKED_REFS = (
    "SELECT DISTINCT ref FROM results "
    "WHERE test_run_id = (SELECT MAX(id) FROM test_runs) AND case_key IS NULL ORDER BY ref"
)

# Automated, active cases none of whose refs produced a result in the latest
# run: the case keeps its last result and counts as unlinked.
CASES_UNMATCHED_LATEST = (
    "SELECT c.key FROM cases c WHERE c.status = 'active' AND c.layer <> 'manual' "
    "AND NOT EXISTS (SELECT 1 FROM results r WHERE r.case_key = c.key "
    "AND r.test_run_id = (SELECT MAX(id) FROM test_runs)) ORDER BY c.key"
)

# Params: (runs,). Seconds by layer per run, newest first, for the pyramid
# and the trend.
RUNTIME_BY_LAYER = (
    "WITH " + _RECENT + " "
    "SELECT rc.id, rc.commit_sha, rc.started_at, r.layer, SUM(r.duration_ms) AS total_ms, "
    "COUNT(*) AS n "
    "FROM results r JOIN recent rc ON rc.id = r.test_run_id "
    "GROUP BY rc.id, rc.commit_sha, rc.started_at, r.layer "
    "ORDER BY rc.started_at DESC, rc.id DESC, r.layer"
)

# Params: (key,). One case, every column.
CASE_BY_KEY = "SELECT " + ", ".join(COLUMNS["cases"]) + " FROM cases WHERE key = %s"

# Params: (ref, limit). A ref's newest results with the run they came from.
RESULTS_FOR_REF = (
    "SELECT r.status, r.duration_ms, r.message, t.commit_sha, t.started_at "
    "FROM results r JOIN test_runs t ON t.id = r.test_run_id "
    "WHERE r.ref = %s ORDER BY t.started_at DESC, r.id DESC LIMIT %s"
)

# What `record_results` joins a parsed ref against: every active case's refs.
ACTIVE_CASE_REFS = "SELECT key, layer, automation FROM cases WHERE status = 'active'"

# --- reads (the app) -----------------------------------------------------------
# The app's own pages, kept here rather than in `api.py` so that a count the
# Overview shows and a list the tool prints come from the same text. `_TOTALS`
# is the per-run status breakdown every run view and the envelope carry.

_TOTALS = (
    "COUNT(r.id) AS n, "
    "COUNT(*) FILTER (WHERE r.status = 'pass') AS pass, "
    "COUNT(*) FILTER (WHERE r.status = 'fail') AS fail, "
    "COUNT(*) FILTER (WHERE r.status = 'skip') AS skip, "
    "COUNT(*) FILTER (WHERE r.status = 'flaky') AS flaky, "
    "COUNT(*) FILTER (WHERE r.status = 'error') AS error, "
    "COUNT(*) FILTER (WHERE r.case_key IS NULL) AS unlinked"
)

# Every run column (in COLUMNS order) followed by the _TOTALS columns.
RUN_COLUMNS = COLUMNS["test_runs"] + ["n", "pass", "fail", "skip", "flaky", "error", "unlinked"]

_RUN_SELECT = (
    "SELECT " + ", ".join("t." + c for c in COLUMNS["test_runs"]) + ", " + _TOTALS + " "
    "FROM test_runs t LEFT JOIN results r ON r.test_run_id = t.id "
)
_RUN_GROUP = "GROUP BY " + ", ".join("t." + c for c in COLUMNS["test_runs"]) + " "

# Params: (limit,). Runs with their totals, newest first.
RUNS_WITH_TOTALS = _RUN_SELECT + _RUN_GROUP + "ORDER BY t.started_at DESC, t.id DESC LIMIT %s"

# Params: (id,). One run with its totals.
RUN_BY_ID = _RUN_SELECT + "WHERE t.id = %s " + _RUN_GROUP

# Runs the reconciler has not announced yet, oldest first so the room reads in
# order.
UNPUBLISHED_RUNS = (_RUN_SELECT + "WHERE t.published_at IS NULL " + _RUN_GROUP
                    + "ORDER BY t.started_at ASC, t.id ASC")

# Params: (published_at, id).
MARK_PUBLISHED = "UPDATE test_runs SET published_at = %s WHERE id = %s AND published_at IS NULL"

# Params: (test_run_id,). Every result of one run, in ref order.
RESULTS_FOR_RUN = (
    "SELECT " + ", ".join(COLUMNS["results"]) + " FROM results "
    "WHERE test_run_id = %s ORDER BY ref, id"
)

# Params: (runs,). Per-run pass rate inputs, oldest first — the sparkline.
PASS_RATE = (
    "WITH " + _RECENT + " "
    "SELECT rc.id, rc.commit_sha, rc.started_at, "
    "COUNT(*) FILTER (WHERE r.status = 'pass') AS pass, "
    "COUNT(*) FILTER (WHERE r.status IN ('fail', 'error')) AS fail, "
    "COUNT(r.id) AS n "
    "FROM recent rc LEFT JOIN results r ON r.test_run_id = rc.id "
    "GROUP BY rc.id, rc.commit_sha, rc.started_at ORDER BY rc.started_at ASC, rc.id ASC"
)

# Distinct refs failing in the latest run: the "failing now" tile.
FAILING_NOW = (
    "SELECT COUNT(DISTINCT ref) FROM results "
    "WHERE test_run_id = (SELECT MAX(id) FROM test_runs) AND status IN ('fail', 'error')"
)

# The `unlinked` filter of the cases page and the tool's `cases` action:
# automated cases none of whose refs ever produced a result.
CASES_UNLINKED_WHERE = ("layer <> 'manual' AND NOT EXISTS "
                        "(SELECT 1 FROM results r WHERE r.case_key = cases.key)")
COUNT_UNLINKED_CASES = "SELECT COUNT(*) FROM cases WHERE status = 'active' AND " + CASES_UNLINKED_WHERE

# The latest run that recorded coverage, every package.
COVERAGE_LATEST = (
    "SELECT test_run_id, package, lines_covered, lines_total, branch_rate "
    "FROM coverage_snapshots "
    "WHERE test_run_id = (SELECT MAX(test_run_id) FROM coverage_snapshots) ORDER BY package"
)

# Params: (runs,). Coverage totals per run that recorded any, oldest first.
COVERAGE_TOTALS = (
    "WITH " + _RECENT + " "
    "SELECT rc.id, rc.commit_sha, rc.started_at, "
    "SUM(c.lines_covered) AS lines_covered, SUM(c.lines_total) AS lines_total "
    "FROM recent rc JOIN coverage_snapshots c ON c.test_run_id = rc.id "
    "GROUP BY rc.id, rc.commit_sha, rc.started_at ORDER BY rc.started_at ASC, rc.id ASC"
)

# The newest status of every linked case, for the list page (a case page
# reads per ref with RESULTS_FOR_REF).
LAST_STATUS_BY_CASE = (
    "SELECT r.case_key, r.status FROM results r "
    "WHERE r.case_key IS NOT NULL AND r.id = "
    "(SELECT MAX(r2.id) FROM results r2 WHERE r2.case_key = r.case_key)"
)

# --- retention (the app) -------------------------------------------------------
# Params: (cutoff,) each. Children first so the delete never depends on the
# driver honouring ON DELETE CASCADE (sqlite does not unless asked).
PRUNE_RESULTS = ("DELETE FROM results WHERE test_run_id IN "
                 "(SELECT id FROM test_runs WHERE started_at < %s)")
PRUNE_COVERAGE = ("DELETE FROM coverage_snapshots WHERE test_run_id IN "
                  "(SELECT id FROM test_runs WHERE started_at < %s)")
PRUNE_RUNS = "DELETE FROM test_runs WHERE started_at < %s"
