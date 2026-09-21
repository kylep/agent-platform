"""The parsers against real reporter output, and the tool against a recording
fake connection (no database in CI, exactly as `prices` and `memory` test).

Fixture provenance (tools/tcms/fixtures/):
  junit-backend.xml     pytest 9.1 `--junitxml` of services/backend/tests/test_config.py
  junit-mixed.xml       pytest 9.1 `--junitxml` of a scratch tests/test_mixed.py with a
                        failure, a skip, a fixture error, a parametrized pair and a
                        10 000-character assertion (paths rewritten to /workspace/mixed)
  playwright-web.json   `npx playwright test tests/quota.spec.ts --reporter=json`
  coverage-backend.xml  `pytest --cov=agentplatform --cov-report=xml` of the same
                        test_config.py run (paths rewritten to /workspace/agent-platform)
  billion-laughs.xml    the classic entity-expansion document around one testcase
"""
import importlib.util
import json
import re
from pathlib import Path

import ingest
import pytest
import run
from cases import split_ref

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures"
REPO = HERE.parents[1]
MIB = 1024 * 1024

_spec = importlib.util.spec_from_file_location(
    "tcmsapp.schema", REPO / "apps" / "tcms" / "backend" / "tcmsapp" / "schema.py")
schema = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(schema)


def _fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


# --- sniffing ----------------------------------------------------------------

def test_sniff_by_content_never_by_name_or_mime():
    # The broker stores XML claims as application/octet-stream and the names
    # are whatever the uploader chose: only the bytes can say what a file is.
    assert ingest.sniff("anything.bin", _fx("junit-backend.xml")) == "junit"
    assert ingest.sniff("report.txt", _fx("playwright-web.json")) == "playwright"
    assert ingest.sniff("cov", _fx("coverage-backend.xml")) == "cobertura"
    assert ingest.sniff("junit.xml", b"<html><body>nope</body></html>") is None
    assert ingest.sniff("x.json", b'{"not": "a report"}') is None
    assert ingest.sniff("x.json", b"\x00\x01\x02") is None
    assert ingest.sniff("empty.xml", b"") is None


def test_sniff_tolerates_a_bom_and_leading_whitespace():
    assert ingest.sniff("j.xml", b"\xef\xbb\xbf\n  " + _fx("junit-backend.xml")) == "junit"


# --- JUnit -------------------------------------------------------------------

def test_parse_junit_builds_pytest_refs_from_classname_and_name():
    results = ingest.parse_junit(_fx("junit-backend.xml"))
    assert [r.ref for r in results] == [
        "pytest:tests/test_config.py::test_defaults",
        "pytest:tests/test_config.py::test_env_override",
        "pytest:tests/test_config.py::test_dev_profile_defaults",
        "pytest:tests/test_config.py::test_web_internal_url_reads_the_env_the_chart_sets",
    ]
    assert all(r.status == "pass" and r.message == "" for r in results)
    assert results[0].duration_ms == 2 and results[1].duration_ms == 1
    for r in results:
        kind, path, name = split_ref(r.ref)
        assert (kind, path) == ("pytest", "tests/test_config.py") and name


def test_parse_junit_statuses_params_and_message_cap():
    by = {r.ref: r for r in ingest.parse_junit(_fx("junit-mixed.xml"))}
    assert by["pytest:tests/test_mixed.py::test_passes"].status == "pass"
    assert by["pytest:tests/test_mixed.py::test_fails"].status == "fail"
    assert by["pytest:tests/test_mixed.py::test_skipped"].status == "skip"
    assert by["pytest:tests/test_mixed.py::test_errors"].status == "error"
    assert by["pytest:tests/test_mixed.py::test_param[1]"].status == "pass"
    assert by["pytest:tests/test_mixed.py::test_param[2]"].status == "fail"
    assert "the values differ" in by["pytest:tests/test_mixed.py::test_fails"].message
    assert "fixture exploded" in by["pytest:tests/test_mixed.py::test_errors"].message
    assert "not on this platform" in by["pytest:tests/test_mixed.py::test_skipped"].message
    long = by["pytest:tests/test_mixed.py::test_long_message"]
    assert long.status == "fail" and len(long.message.encode()) <= ingest.MAX_MESSAGE


def test_parse_junit_skips_and_counts_a_testcase_without_classname():
    doc = (b'<testsuites><testsuite name="pytest"><testcase name="orphan" time="1"/>'
           b'<testcase classname="tests.test_a" name="test_b" time="0.5"/>'
           b"</testsuite></testsuites>")
    results = ingest.parse_junit(doc)
    assert [r.ref for r in results] == ["pytest:tests/test_a.py::test_b"]
    assert ingest.skipped_testcases(doc) == 1


def test_parse_junit_refuses_playwrights_junit_twin():
    # ap-verify writes junit-web.xml AND playwright-web.json for the same run;
    # recording both would double every e2e result, so the XML twin is refused
    # by name of the fix rather than silently parsed.
    doc = (b'<testsuites><testsuite name="quota.spec.ts"><testcase name="t" '
           b'classname="quota.spec.ts" time="0.7"></testcase></testsuite></testsuites>')
    with pytest.raises(ingest.Refused, match="JSON"):
        ingest.parse_junit(doc)


def test_parse_junit_refuses_a_document_with_a_dtd_before_parsing(monkeypatch):
    import xml.etree.ElementTree as ET
    called = []
    monkeypatch.setattr(ET, "fromstring", lambda *a, **k: called.append(1))
    for kind in ("junit", "cobertura"):
        with pytest.raises(ingest.Refused, match="DOCTYPE"):
            ingest.parse(kind, _fx("billion-laughs.xml"))
    with pytest.raises(ingest.Refused, match="ENTITY"):
        ingest.parse_junit(b'<?xml version="1.0"?><!ENTITY x "y"><testsuites/>')
    with pytest.raises(ingest.Refused):
        ingest.parse_junit(b"<!doctype junk><testsuites/>")
    assert called == []


def test_parse_junit_refuses_malformed_xml():
    with pytest.raises(ingest.Refused, match="not well-formed"):
        ingest.parse_junit(b"<testsuites><testsuite></testsuites>")


@pytest.mark.parametrize("fn", [ingest.parse_junit, ingest.parse_playwright_json,
                                ingest.parse_cobertura])
def test_parsers_refuse_input_over_8_mib(fn):
    with pytest.raises(ingest.Refused, match="8 MiB"):
        fn(b"<testsuites>" + b" " * (9 * MIB))
    assert ingest.sniff("big", b"{" + b" " * (9 * MIB)) is None


# --- Playwright --------------------------------------------------------------

def test_parse_playwright_json_builds_refs_from_file_and_title():
    results = ingest.parse_playwright_json(_fx("playwright-web.json"))
    assert len(results) == 7
    assert results[0].ref == ("playwright:quota.spec.ts::"
                              "the bars sit under the brand and say what they are")
    assert {r.status for r in results} == {"pass"}
    assert results[0].duration_ms == 735
    # A title may carry its own double colon or quotes; split_ref still works.
    kind, path, name = split_ref(results[3].ref)
    assert kind == "playwright" and path == "quota.spec.ts" and "server's" in name


def test_parse_playwright_json_outcomes_and_nested_titles():
    doc = {"config": {"rootDir": "/w/services/web/tests"}, "suites": [{
        "title": "a.spec.ts", "file": "a.spec.ts", "specs": [], "suites": [{
            "title": "group", "file": "a.spec.ts", "specs": [
                {"title": "flips", "file": "a.spec.ts", "tests": [{
                    "status": "flaky", "results": [
                        {"status": "failed", "duration": 100,
                         "errors": [{"message": "boom"}]},
                        {"status": "passed", "duration": 50, "errors": []}]}]},
                {"title": "breaks", "file": "a.spec.ts", "tests": [{
                    "status": "unexpected", "results": [
                        {"status": "failed", "duration": 10,
                         "errors": [{"message": "x" * 9000}]}]}]},
                {"title": "off", "file": "a.spec.ts", "tests": [{
                    "status": "skipped", "results": [{"status": "skipped", "duration": 0}]}]},
                {"title": "fine", "file": "a.spec.ts", "tests": [{
                    "status": "expected", "results": [{"status": "passed", "duration": 5}]}]},
            ]}]}], "stats": {}}
    by = {r.ref: r for r in ingest.parse_playwright_json(json.dumps(doc).encode())}
    assert by["playwright:a.spec.ts::group › flips"].status == "flaky"
    assert by["playwright:a.spec.ts::group › flips"].duration_ms == 150
    assert "boom" in by["playwright:a.spec.ts::group › flips"].message
    assert by["playwright:a.spec.ts::group › breaks"].status == "fail"
    assert len(by["playwright:a.spec.ts::group › breaks"].message.encode()) <= ingest.MAX_MESSAGE
    assert by["playwright:a.spec.ts::group › off"].status == "skip"
    assert by["playwright:a.spec.ts::group › fine"].status == "pass"


def test_parse_playwright_json_refuses_suites_nested_too_deep():
    # 3000 nested suites is a 200 KB document that would blow the recursion
    # limit and surface as a database outage; the cap refuses it by name.
    leaf = {"title": "deep", "file": "a.spec.ts", "specs": [
        {"title": "t", "file": "a.spec.ts", "tests": [{"status": "expected", "results": []}]}]}
    doc = leaf
    for _ in range(100):
        doc = {"title": "g", "file": "a.spec.ts", "specs": [], "suites": [doc]}
    with pytest.raises(ingest.Refused, match="deeper than"):
        ingest.parse_playwright_json(json.dumps({"config": {}, "suites": [doc]}).encode())
    doc = leaf
    for _ in range(10):
        doc = {"title": "g", "file": "a.spec.ts", "specs": [], "suites": [doc]}
    (r,) = ingest.parse_playwright_json(json.dumps({"config": {}, "suites": [doc]}).encode())
    assert r.ref.startswith("playwright:a.spec.ts::g › g › ")


def test_parse_playwright_json_refuses_other_json():
    with pytest.raises(ingest.Refused):
        ingest.parse_playwright_json(b'{"suites": "nope"}')
    with pytest.raises(ingest.Refused):
        ingest.parse_playwright_json(b"[1, 2")


# --- Cobertura ---------------------------------------------------------------

def test_parse_cobertura_packages_with_line_counts_from_classes():
    rows = ingest.parse_cobertura(_fx("coverage-backend.xml"))
    by = {r.package: r for r in rows}
    # coverage.py names the root package "." — the source directory's own
    # name is the package a reader recognises.
    assert set(by) == {"agentplatform", "agentplatform.api"}
    top, api = by["agentplatform"], by["agentplatform.api"]
    assert 0 < top.lines_covered < top.lines_total
    assert 0 < api.lines_covered < api.lines_total
    # The file's own totals are the sum of what the classes count.
    assert top.lines_total + api.lines_total == 11707
    assert top.lines_covered + api.lines_covered == 3559
    assert top.branch_rate is None                   # branches-valid="0"


def test_parse_cobertura_branch_rate_when_branches_measured():
    doc = (b'<coverage lines-valid="2" lines-covered="1" branches-valid="4" branch-rate="0.5">'
           b'<sources><source>/w/pkg</source></sources><packages>'
           b'<package name="sub" line-rate="0.5" branch-rate="0.25"><classes>'
           b'<class name="m.py" filename="sub/m.py"><lines><line number="1" hits="1"/>'
           b'<line number="2" hits="0"/></lines></class></classes></package>'
           b"</packages></coverage>")
    (row,) = ingest.parse_cobertura(doc)
    assert (row.package, row.lines_covered, row.lines_total, row.branch_rate) == \
        ("pkg.sub", 1, 2, 0.25)


# --- layers and roots --------------------------------------------------------

def test_layer_of_by_path_prefix():
    assert ingest.layer_of("playwright:services/web/tests/quota.spec.ts::t") == "e2e"
    assert ingest.layer_of("pytest:tools/prices/test_run.py::test_x") == "unit"
    assert ingest.layer_of("pytest:services/backend/tests/test_x.py::test_y") == "integration"
    assert ingest.layer_of("pytest:apps/news/backend/test_x.py::test_y") == "integration"
    assert ingest.layer_of("pytest:tools/prices/tests/test_x.py::test_y") == "integration"


def test_resolve_root_finds_the_one_directory_that_defines_the_test(tmp_path):
    (tmp_path / "services/backend/tests").mkdir(parents=True)
    (tmp_path / "services/backend/tests/test_config.py").write_text("def test_defaults():\n    pass\n")
    for tool in ("prices", "memory"):
        (tmp_path / "tools" / tool).mkdir(parents=True)
    (tmp_path / "tools/prices/test_run.py").write_text("def test_symbols():\n    pass\n")
    (tmp_path / "tools/memory/test_run.py").write_text("def test_read():\n    pass\n")
    (tmp_path / "services/web/tests").mkdir(parents=True)
    (tmp_path / "services/web/tests/quota.spec.ts").write_text('test("the bars sit", async () => {});\n')
    # A copy under node_modules or a venv is never a root.
    (tmp_path / "services/web/node_modules/x/tests").mkdir(parents=True)
    (tmp_path / "services/web/node_modules/x/tests/quota.spec.ts").write_text("x")

    assert run.resolve_root(tmp_path, "tests/test_config.py", ["test_defaults"]) == "services/backend"
    # Two tools share the file name; the test's own definition picks the tool.
    assert run.resolve_root(tmp_path, "test_run.py", ["test_symbols"]) == "tools/prices"
    assert run.resolve_root(tmp_path, "test_run.py", ["test_read[param]"]) == "tools/memory"
    assert run.resolve_root(tmp_path, "test_run.py", ["test_nowhere"]) is None
    assert run.resolve_root(tmp_path, "quota.spec.ts", ["the bars sit"]) == "services/web/tests"
    assert run.resolve_root(tmp_path, "nope.py", ["x"]) is None
    # A reporter path is text, not a pattern: `[x]` must match only itself.
    (tmp_path / "services/backend/tests/test_[x].py").write_text("def test_a():\n    pass\n")
    (tmp_path / "services/backend/tests/test_x.py").write_text("def test_a():\n    pass\n")
    assert run.resolve_root(tmp_path, "tests/test_[x].py", ["test_a"]) == "services/backend"
    assert run.resolve_root(tmp_path, "tests/test_*.py", ["test_a"]) is None


def test_resolve_root_against_the_real_checkout():
    assert run.resolve_root(REPO, "tests/test_config.py", ["test_defaults"]) == "services/backend"
    assert run.resolve_root(REPO, "quota.spec.ts",
                            ["the bars sit under the brand and say what they are"]) == "services/web/tests"


# --- the recording fake connection ------------------------------------------

class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self._rows = []
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.conn.executed.append((sql, None if params is None else list(params)))
        self._rows = list(self.conn.rows_for(sql, params))
        self.rowcount = self.conn.rowcount_for(sql)

    def executemany(self, sql, seq):
        rows = [list(p) for p in seq]
        self.conn.executed.append((sql, rows))
        self.rowcount = len(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class FakeConn:
    """Records every statement; answers a query by the first substring key of
    `rows` that appears in its SQL."""

    def __init__(self, rows=None, rowcounts=None):
        self.rows = rows or {}
        self.rowcounts = rowcounts or {}
        self.executed = []
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return FakeCursor(self)

    def rows_for(self, sql, params):
        for key, rows in self.rows.items():
            if key in sql:
                return rows(params) if callable(rows) else rows
        return []

    def rowcount_for(self, sql):
        for key, n in self.rowcounts.items():
            if key in sql:
                return n
        return 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closed = True


def _columns(sql: str) -> tuple:
    """(table, [columns]) of an INSERT statement."""
    m = re.search(r"INSERT INTO (\w+) \(([^)]*)\)", sql)
    assert m, sql
    return m.group(1), [c.strip() for c in m.group(2).split(",")]


def _placeholders(sql: str) -> int:
    body = re.sub(r"INSERT INTO \w+ \([^)]*\)", "", sql)
    return body.count("%s")


CASE_REFS = [
    ("cfg.defaults", "integration",
     json.dumps(["pytest:services/backend/tests/test_config.py::test_defaults"])),
    ("cfg.env", "integration",
     json.dumps(["pytest:services/backend/tests/test_config.py::test_env_override",
                 "pytest:services/backend/tests/test_config.py::test_dev_profile_defaults"])),
    ("quota.bars", "e2e",
     json.dumps([("playwright:services/web/tests/quota.spec.ts::"
                  "the bars sit under the brand and say what they are")])),
    ("prices.unit", "unit",
     json.dumps([("pytest:services/backend/tests/test_config.py::"
                  "test_web_internal_url_reads_the_env_the_chart_sets")])),
]


def _record_env(monkeypatch, in_dir):
    monkeypatch.setenv("TOOL_IN_DIR", str(in_dir))
    monkeypatch.setenv("TOOL_RUN_ID", "0123456789abcdef0123456789abcdef")
    monkeypatch.setenv("TOOL_CALLER_AGENT", "qa")


@pytest.fixture
def in_dir(tmp_path):
    d = tmp_path / "in"
    d.mkdir()
    return d


def test_record_results_one_run_n_results_linked_by_automation(monkeypatch, in_dir):
    for name in ("junit-backend.xml", "playwright-web.json", "coverage-backend.xml"):
        (in_dir / name).write_bytes(_fx(name))
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": CASE_REFS, "RETURNING id": [(77,)]})
    out = run.record_results(conn, {"commit_sha": "a1b2c3d", "branch": "main",
                                    "agent": "spoofed", "run_id": "spoofed",
                                    "verify": {"ok": True, "suites": [
                                        {"name": "backend", "exit": 0, "seconds": 12.5,
                                         "cmd": ["x"], "tail": "y" * 5000}]}},
                             repo=REPO)
    runs = [e for e in conn.executed if e[0].startswith("INSERT INTO test_runs")]
    assert len(runs) == 1 and runs[0][0] == schema.INSERT_RUN
    _table, cols = _columns(runs[0][0])
    assert set(cols) <= set(schema.COLUMNS["test_runs"])
    assert "id" not in cols and "published_at" not in cols
    assert _placeholders(runs[0][0]) == len(runs[0][1])
    row = dict(zip(cols, runs[0][1]))
    assert row["commit_sha"] == "a1b2c3d" and row["branch"] == "main"
    assert row["run_id"] == "0123456789abcdef0123456789abcdef" and row["agent"] == "qa"
    assert row["verify_ok"] is True
    assert json.loads(row["suites"]) == [{"name": "backend", "exit": 0, "seconds": 12.5}]
    assert row["finished_at"] > row["started_at"]

    (results,) = [e for e in conn.executed if e[0].startswith("INSERT INTO results")]
    assert results[0] == schema.INSERT_RESULT
    _table, cols = _columns(results[0])
    assert set(cols) <= set(schema.COLUMNS["results"]) and "id" not in cols
    rows = [dict(zip(cols, r)) for r in results[1]]
    assert len(rows) == 4 + 7
    assert all(r["test_run_id"] == 77 for r in rows)
    by = {r["ref"]: r for r in rows}
    # Refs are repo-root-relative after the root resolves, so they join to
    # the case's automation entries exactly.
    r = by["pytest:services/backend/tests/test_config.py::test_defaults"]
    assert r["case_key"] == "cfg.defaults" and r["layer"] == "integration"
    assert by["pytest:services/backend/tests/test_config.py::test_env_override"]["case_key"] == "cfg.env"
    pw = by["playwright:services/web/tests/quota.spec.ts::"
            "the bars sit under the brand and say what they are"]
    assert pw["case_key"] == "quota.bars" and pw["layer"] == "e2e" and pw["status"] == "pass"
    # A linked case that says unit overrides the prefix rule.
    assert by["pytest:services/backend/tests/test_config.py::"
              "test_web_internal_url_reads_the_env_the_chart_sets"]["layer"] == "unit"
    unlinked = [r for r in rows if r["case_key"] is None]
    assert len(unlinked) == 6 and all(r["layer"] == "e2e" for r in unlinked)

    (cov,) = [e for e in conn.executed if e[0].startswith("INSERT INTO coverage_snapshots")]
    assert cov[0] == schema.INSERT_COVERAGE
    _table, cols = _columns(cov[0])
    assert set(cols) <= set(schema.COLUMNS["coverage_snapshots"]) and "id" not in cols
    assert {r[cols.index("package")] for r in cov[1]} == {"agentplatform", "agentplatform.api"}
    assert all(r[cols.index("test_run_id")] == 77 for r in cov[1])

    assert conn.commits == 1
    assert out["test_run_id"] == 77
    assert out["totals"] == {"pass": 11}
    assert out["linked"] == 5 and out["unlinked_refs"] == 6
    assert out["coverage_packages"] == 2
    assert {f["name"]: f["kind"] for f in out["files"]} == {
        "junit-backend.xml": "junit", "playwright-web.json": "playwright",
        "coverage-backend.xml": "cobertura"}


def test_verify_rows_clamp_seconds_and_exit():
    ok, rows = run._verify_rows({"ok": True, "suites": [
        {"name": "a", "exit": 0, "seconds": 1e18},
        {"name": "b", "exit": -5, "seconds": -3},
        {"name": "c", "exit": 10 ** 12, "seconds": "nan"},
        {"name": "d", "exit": "1", "seconds": float("inf")},
    ]})
    assert ok is True
    assert [r["seconds"] for r in rows] == [86400.0, 0.0, 0.0, 86400.0]
    assert [r["exit"] for r in rows] == [0, -5, None, None]
    assert run._verify_rows("nope") == (None, [])


def test_record_results_survives_absurd_verify_seconds(monkeypatch, in_dir):
    (in_dir / "junit-backend.xml").write_bytes(_fx("junit-backend.xml"))
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": CASE_REFS, "RETURNING id": [(3,)]})
    out = run.record_results(conn, {"commit_sha": "a1b2c3d", "verify": {"ok": False, "suites": [
        {"name": "x", "exit": 1, "seconds": 1e18}] * 60}}, repo=REPO)
    assert out["test_run_id"] == 3
    (ins,) = [e for e in conn.executed if e[0].startswith("INSERT INTO test_runs")]
    row = dict(zip(_columns(ins[0])[1], ins[1]))
    assert len(json.loads(row["suites"])) == run.MAX_SUITES
    assert (row["finished_at"] - row["started_at"]).days == run.MAX_SUITES


def test_record_results_refuses_an_unknown_file_by_name_and_writes_nothing(monkeypatch, in_dir):
    (in_dir / "junit-backend.xml").write_bytes(_fx("junit-backend.xml"))
    (in_dir / "notes.txt").write_bytes(b"3 passed, 0 failed")
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": CASE_REFS})
    with pytest.raises(run.ToolError, match="notes.txt"):
        run.record_results(conn, {"commit_sha": "a1b2c3d"}, repo=REPO)
    assert not [e for e in conn.executed if e[0].startswith("INSERT")]


def test_record_results_refuses_no_files_and_a_bad_sha(monkeypatch, in_dir):
    _record_env(monkeypatch, in_dir)
    with pytest.raises(run.ToolError, match="files"):
        run.record_results(FakeConn(), {"commit_sha": "a1b2c3d"}, repo=REPO)
    (in_dir / "junit-backend.xml").write_bytes(_fx("junit-backend.xml"))
    with pytest.raises(run.ToolError, match="commit_sha"):
        run.record_results(FakeConn(), {"commit_sha": "main; drop"}, repo=REPO)
    with pytest.raises(run.ToolError, match="commit_sha"):
        run.record_results(FakeConn(), {}, repo=REPO)


def test_record_results_is_idempotent_per_platform_run(monkeypatch, in_dir):
    # A retried tool call inside the same run must not double-record: the
    # insert is ON CONFLICT (run_id) DO NOTHING, and a swallowed insert
    # answers with the existing id and writes no results.
    (in_dir / "junit-backend.xml").write_bytes(_fx("junit-backend.xml"))
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": CASE_REFS, "RETURNING id": [], "WHERE run_id": [(41,)]})
    out = run.record_results(conn, {"commit_sha": "a1b2c3d"}, repo=REPO)
    assert out["test_run_id"] == 41 and out["already_recorded"] is True
    assert not [e for e in conn.executed if e[0].startswith("INSERT INTO results")]
    assert "ON CONFLICT (run_id) DO NOTHING" in schema.INSERT_RUN
    assert schema.RUN_BY_RUN_ID.count("%s") == 1
    assert ("test_runs", "run_id") in {(t, c) for t, cols in schema.UNIQUE.items() for c in cols}
    assert next(e for e in conn.executed if "WHERE run_id" in e[0])[1] == [
        "0123456789abcdef0123456789abcdef"]


def test_record_results_refuses_the_billion_laughs_file(monkeypatch, in_dir):
    (in_dir / "junit-backend.xml").write_bytes(_fx("billion-laughs.xml"))
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": CASE_REFS})
    with pytest.raises(run.ToolError, match="DOCTYPE"):
        run.record_results(conn, {"commit_sha": "a1b2c3d"}, repo=REPO)
    assert conn.commits == 0


def test_record_results_needs_the_executors_identity(monkeypatch, in_dir):
    (in_dir / "junit-backend.xml").write_bytes(_fx("junit-backend.xml"))
    monkeypatch.setenv("TOOL_IN_DIR", str(in_dir))
    monkeypatch.delenv("TOOL_RUN_ID", raising=False)
    monkeypatch.delenv("TOOL_CALLER_AGENT", raising=False)
    with pytest.raises(run.ToolError, match="TOOL_CALLER_AGENT"):
        run.record_results(FakeConn(rows={"FROM cases": CASE_REFS}),
                           {"commit_sha": "a1b2c3d", "agent": "qa"}, repo=REPO)


def test_record_results_counts_mixed_statuses_and_skipped_testcases(monkeypatch, in_dir):
    (in_dir / "junit-mixed.xml").write_bytes(_fx("junit-mixed.xml"))
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": [], "RETURNING id": [(1,)]})
    out = run.record_results(conn, {"commit_sha": "a1b2c3d"}, repo=REPO)
    assert out["totals"] == {"pass": 2, "fail": 3, "skip": 1, "error": 1}
    # tests/test_mixed.py exists in no checkout root: the refs stay as the
    # reporter wrote them and count as unresolved, never invented.
    assert out["unresolved_files"] == ["junit-mixed.xml"]
    (results,) = [e for e in conn.executed if e[0].startswith("INSERT INTO results")]
    refs = {r[_columns(results[0])[1].index("ref")] for r in results[1]}
    assert "pytest:tests/test_mixed.py::test_param[2]" in refs


# --- sync_cases --------------------------------------------------------------

def _write_suite(d: Path, name: str, cases: list) -> None:
    import yaml
    (d / f"{name}.yaml").write_text(yaml.safe_dump(
        {"suite": name, "area": name, "cases": cases}, sort_keys=False))


def test_sync_cases_upserts_and_retires(tmp_path, monkeypatch):
    cases_dir = tmp_path / "tcms" / "cases"
    cases_dir.mkdir(parents=True)
    _write_suite(cases_dir, "relay", [
        {"key": "relay.a", "title": "A", "layer": "integration", "priority": "p1",
         "expected": "ok", "automation": ["pytest:services/backend/tests/test_r.py::test_a"],
         "tags": ["x"]},
        {"key": "relay.m", "title": "M", "layer": "manual", "priority": "p3",
         "expected": "eyeballed", "steps": ["look"]},
    ])
    _write_suite(cases_dir, "broken", [{"key": "broken.x", "title": "no layer"}])
    conn = FakeConn(rowcounts={"UPDATE cases": 3})
    monkeypatch.setattr(run, "source_sha", lambda root: "deadbeef")
    out = run.sync_cases(conn, {}, repo=tmp_path)
    ups = [e for e in conn.executed if e[0] == schema.UPSERT_CASE]
    assert len(ups) == 2
    cols = _columns(ups[0][0])[1]
    assert set(cols) == set(schema.COLUMNS["cases"])
    assert "ON CONFLICT (key) DO UPDATE" in schema.UPSERT_CASE
    row = dict(zip(cols, ups[0][1]))
    assert row["key"] == "relay.a" and row["suite"] == "relay" and row["area"] == "relay"
    assert row["status"] == "active" and row["source_sha"] == "deadbeef"
    assert json.loads(row["automation"]) == ["pytest:services/backend/tests/test_r.py::test_a"]
    assert json.loads(row["tags"]) == ["x"] and json.loads(row["steps"]) == []
    (ret,) = [e for e in conn.executed if e[0] == schema.RETIRE_CASES]
    _synced_at, keys, suites = ret[1]
    # Keys this sync saw stay; a suite whose file failed to load keeps its
    # rows as they were — a typo must not retire a whole area.
    assert sorted(keys) == ["relay.a", "relay.m"] and suites == ["broken"]
    assert conn.commits == 1
    assert out["synced"] == 2 and out["retired"] == 3 and out["source_sha"] == "deadbeef"
    assert out["errors"] == [{"file": "broken.yaml", "key": "broken.x",
                              "message": "layer is required"}]


def test_sync_cases_reads_the_repos_case_tree_by_default():
    assert run.CASES_DIR == REPO / "tcms" / "cases"
    assert run.SCHEMA_PATH == REPO / "apps" / "tcms" / "backend" / "tcmsapp" / "schema.py"
    assert run.CASES_DIR.is_dir() and run.SCHEMA_PATH.is_file()


def test_source_sha_reads_head_without_git(tmp_path, monkeypatch):
    assert run.source_sha(tmp_path) == "unknown"
    git = tmp_path / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text("ref: refs/heads/main\n")
    (git / "refs" / "heads" / "main").write_text("a" * 40 + "\n")
    monkeypatch.setattr(run.shutil, "which", lambda cmd: None)
    assert run.source_sha(tmp_path) == "a" * 40
    (git / "refs" / "heads" / "main").unlink()
    (git / "packed-refs").write_text("# pack-refs\n" + "b" * 40 + " refs/heads/main\n")
    assert run.source_sha(tmp_path) == "b" * 40
    (git / "HEAD").write_text("c" * 40 + "\n")                   # detached
    assert run.source_sha(tmp_path) == "c" * 40


def test_source_sha_prefers_git_when_present(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(run.shutil, "which", lambda cmd: "/usr/bin/git")
    monkeypatch.setattr(run.subprocess, "run",
                        lambda *a, **k: type("R", (), {"returncode": 0,
                                                       "stdout": "f" * 40 + "\n"})())
    assert run.source_sha(tmp_path) == "f" * 40


# --- the reads ---------------------------------------------------------------

def test_page_caps_at_4_kib_and_says_how_many():
    lines = [f"line {i} " + "x" * 90 for i in range(200)]
    text = run.page(lines, 200, "cases")
    assert len(text.encode()) <= run.LINE_CAP
    shown = text.count("\n")
    assert text.endswith(f"showing {shown} of 200 cases")
    assert run.page(lines[:3], 3, "cases").endswith("showing 3 of 3 cases")
    assert run.page([], 0, "cases") == "no cases (showing 0 of 0 cases)"


def _many_cases(n):
    return [(f"suite.case-{i}", "suite", "area", f"Title {i} " + "t" * 60, "integration",
             "p1", "active", json.dumps([f"pytest:services/backend/tests/test_{i}.py::test_x"]))
            for i in range(n)]


def test_cases_lists_one_line_each_with_filters_parameterized():
    conn = FakeConn(rows={"COUNT(*)": [(212,)], "FROM cases": _many_cases(40)})
    text = run.action_cases(conn, {"layer": "integration", "area": "relay", "status": "active",
                                   "q": "summons", "unlinked": True, "limit": 40})
    sql, params = next(e for e in conn.executed if "FROM cases" in e[0] and "COUNT" not in e[0])
    assert "layer = %s" in sql and "area = %s" in sql and "status = %s" in sql
    assert "ILIKE %s" in sql and "NOT EXISTS" in sql
    assert "summons" not in sql and "%summons%" in params
    assert params[-1] == 40 and "LIMIT %s" in sql
    assert len(text.encode()) <= run.LINE_CAP
    assert text.endswith("showing 40 of 212 cases") or "showing " in text
    assert text.splitlines()[0].startswith("suite.case-0 ")


def test_cases_clamps_limit_and_rejects_bad_filters():
    conn = FakeConn(rows={"COUNT(*)": [(0,)]})
    run.action_cases(conn, {"limit": 9999})
    assert conn.executed[-1][1][-1] == run.MAX_LIMIT
    with pytest.raises(run.ToolError, match="layer"):
        run.action_cases(conn, {"layer": "nope"})
    with pytest.raises(run.ToolError, match="status"):
        run.action_cases(conn, {"status": "deleted"})


def test_case_shows_refs_with_last_results_and_is_capped():
    import datetime
    case_row = ("relay.a", "relay", "relay", "A title", "integration", "p1",
                json.dumps(["pre"]), json.dumps(["s1", "s2"]), "expected text",
                json.dumps(["pytest:services/backend/tests/test_r.py::test_a",
                            "pytest:services/backend/tests/test_r.py::test_b"]),
                json.dumps(["x"]), json.dumps(["QA-3"]), "active",
                datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc), "deadbeef")
    when = datetime.datetime(2026, 9, 18, 3, 0, tzinfo=datetime.timezone.utc)
    hist = [("pass", 120, "", "a1b2c3d", when)] * 10 + [("fail", 5, "m" * 3000, "0000000", when)]
    conn = FakeConn(rows={"FROM cases": [case_row], "FROM results": hist[:10]})
    text = run.action_case(conn, {"key": "relay.a"})
    assert text.startswith("relay.a")
    assert "A title" in text and "QA-3" in text
    assert text.count("pytest:services/backend/tests/test_r.py::") == 2
    assert len(text.encode()) <= run.LINE_CAP
    hist_sql = [e for e in conn.executed if "FROM results" in e[0]]
    assert len(hist_sql) == 2 and all(p[-1] == 10 for _, p in hist_sql)
    assert run.action_case(FakeConn(), {"key": "relay.none"}) == "no case relay.none"
    with pytest.raises(run.ToolError, match="key"):
        run.action_case(FakeConn(), {})


def test_coverage_gaps_sections_and_threshold_param():
    conn = FakeConn(rows={
        schema.COVERAGE_GAPS: [("agentplatform.api", 30, 100, 30.0)] * 30,
        "layer = 'manual'": [(f"m.case-{i}", f"Manual {i}") for i in range(30)],
        "case_key IS NULL": [(f"pytest:services/x/test_{i}.py::test_y",) for i in range(30)],
        "NOT EXISTS": [(f"c.unmatched-{i}",) for i in range(30)],
    })
    text = run.action_coverage_gaps(conn, {"threshold_pct": 55})
    _sql, params = next(e for e in conn.executed if e[0] == schema.COVERAGE_GAPS)
    assert params == [55]
    assert "agentplatform.api" in text and "30.0%" in text
    assert "manual" in text.lower() and "m.case-0" in text
    assert "pytest:services/x/test_0.py::test_y" in text
    assert "c.unmatched-0" in text
    assert len(text.encode()) <= run.LINE_CAP
    assert "showing " in text
    conn = FakeConn()
    run.action_coverage_gaps(conn, {})
    assert next(e for e in conn.executed if e[0] == schema.COVERAGE_GAPS)[1] == [70]


def test_runtime_report_totals_by_layer_slowest_and_trend():
    import datetime
    t = datetime.datetime(2026, 9, 19, tzinfo=datetime.timezone.utc)
    by_layer = []
    for run_id, sha, e2e, integ in [(3, "ccc1111", 300_000, 90_000),
                                    (2, "bbb1111", 250_000, 80_000),
                                    (1, "aaa1111", 200_000, 70_000)]:
        by_layer += [(run_id, sha, t, "e2e", e2e, 40), (run_id, sha, t, "integration", integ, 900)]
    slow = [(f"playwright:services/web/tests/s{i}.spec.ts::" + "t" * 80, "e2e", 9000 - i, 9500, 3)
            for i in range(15)]
    conn = FakeConn(rows={schema.RUNTIME_BY_LAYER: by_layer, schema.SLOWEST: slow})
    text = run.action_runtime_report(conn, {"runs": 3})
    assert [e[1] for e in conn.executed if e[0] == schema.RUNTIME_BY_LAYER] == [[3]]
    assert [e[1] for e in conn.executed if e[0] == schema.SLOWEST] == [[3, 15]]
    assert "ccc1111" in text and "e2e 300s" in text and "integration 90s" in text
    assert "trend" in text and "+120s" in text          # 270 s → 390 s, oldest → newest
    assert "slowest" in text
    assert len(text.encode()) <= run.LINE_CAP


def test_flaky_and_prune_candidates_lines_and_caps():
    flaky = [(f"pytest:services/backend/tests/test_{i}.py::test_flip" + "x" * 60, 2, 5, 1, 2, 1)
             for i in range(60)]
    conn = FakeConn(rows={schema.FLAKY: flaky})
    text = run.action_flaky(conn, {"runs": 7})
    assert [e[1] for e in conn.executed if e[0] == schema.FLAKY] == [[7]]
    assert "test_0.py::test_flip" in text and "2 fail" in text
    assert len(text.encode()) <= run.LINE_CAP and "showing " in text
    assert run.action_flaky(FakeConn(), {}) == "no flaky refs (showing 0 of 0 refs)"
    assert [e[1] for e in FakeConn().executed] == []

    # Retired-case rows come first from the query, so the cap never hides them.
    prune = [("pytest:services/backend/tests/test_old.py::test_gone", "old.c", 10, 50, 0, "retired-case")]
    prune += [(f"pytest:services/backend/tests/test_{i}.py::test_slow" + "y" * 60, "k.c", 8000, 50, 0,
               "never-failed-slow") for i in range(40)]
    conn = FakeConn(rows={schema.PRUNE_CANDIDATES: prune})
    text = run.action_prune_candidates(conn, {"runs": 50})
    assert [e[1] for e in conn.executed if e[0] == schema.PRUNE_CANDIDATES] == [[50]]
    assert "never-failed-slow" in text and "retired-case" in text
    assert "coverage" in text.lower()          # the omitted duplicate-lines check is named
    assert len(text.encode()) <= run.LINE_CAP


def test_read_run_counts_are_clamped():
    conn = FakeConn()
    run.action_flaky(conn, {"runs": 100000})
    assert conn.executed[0][1] == [run.MAX_RUNS]
    conn = FakeConn()
    run.action_runtime_report(conn, {"runs": 0})
    assert conn.executed[0][1] == [10]


# --- schema.py is the one home -----------------------------------------------

def test_schema_columns_and_statements():
    assert set(schema.COLUMNS) == {"cases", "test_runs", "results", "coverage_snapshots"}
    for name in ("INSERT_RUN", "INSERT_RESULT", "INSERT_COVERAGE", "UPSERT_CASE",
                 "RETIRE_CASES", "FLAKY", "SLOWEST", "PRUNE_CANDIDATES", "COVERAGE_GAPS",
                 "RUNTIME_BY_LAYER"):
        sql = getattr(schema, name)
        assert isinstance(sql, str) and "%s" in sql, name
        # Parameterized means no literal from a file or an argument is ever
        # interpolated: the statement text is a constant.
        assert "{" not in sql and "format(" not in sql
    for sql in (schema.INSERT_RUN, schema.INSERT_RESULT, schema.INSERT_COVERAGE, schema.UPSERT_CASE):
        table, cols = _columns(sql)
        assert set(cols) <= set(schema.COLUMNS[table]), table
    assert (REPO / "apps" / "tcms" / "backend" / "tcmsapp" / "__init__.py").is_file()


# --- main(): stdin JSON → stdout JSON/text, the executor contract ---------------

def _main(monkeypatch, capsys, args, conn):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(args)))
    monkeypatch.setattr(run, "connect", lambda: conn)
    code = run.main()
    out = capsys.readouterr()
    return code, out.out, out.err


def test_main_routes_actions_and_reports_a_missing_table(monkeypatch, capsys):
    class UndefinedTable(Exception):
        __module__ = "psycopg.errors"

    class Boom(FakeConn):
        def cursor(self):
            raise UndefinedTable('relation "cases" does not exist')
    code, out, err = _main(monkeypatch, capsys, {"action": "cases"}, Boom())
    assert code == 2 and "tcms app owns these tables" in err and out == ""

    class Bug(FakeConn):
        def cursor(self):
            raise RuntimeError("something else entirely")
    # A non-database failure is named as such — never dressed as an outage.
    code, out, err = _main(monkeypatch, capsys, {"action": "cases"}, Bug())
    assert code == 2 and "something else entirely" in err and "deployed" not in err
    code, out, err = _main(monkeypatch, capsys, {"action": "nope"}, FakeConn())
    assert code == 2 and "unknown action" in err
    conn = FakeConn(rows={"COUNT(*)": [(0,)]})
    code, out, err = _main(monkeypatch, capsys, {"action": "cases"}, conn)
    assert code == 0 and out.strip() == "no cases (showing 0 of 0 cases)" and conn.closed


def test_main_record_results_prints_json(monkeypatch, capsys, in_dir):
    (in_dir / "junit-backend.xml").write_bytes(_fx("junit-backend.xml"))
    _record_env(monkeypatch, in_dir)
    conn = FakeConn(rows={"FROM cases": CASE_REFS, "RETURNING id": [(5,)]})
    code, out, err = _main(monkeypatch, capsys, {"action": "record_results",
                                                 "commit_sha": "a1b2c3d"}, conn)
    assert code == 0, err
    assert json.loads(out)["test_run_id"] == 5
    code, out, err = _main(monkeypatch, capsys, {"action": "record_results"}, FakeConn())
    assert code == 2 and "commit_sha" in err
    # A refused file is the model's problem to fix, not an outage.
    (in_dir / "junit-backend.xml").write_bytes(_fx("billion-laughs.xml"))
    code, out, err = _main(monkeypatch, capsys, {"action": "record_results",
                                                 "commit_sha": "a1b2c3d"}, FakeConn())
    assert code == 2 and "junit-backend.xml" in err and "DOCTYPE" in err and "deployed" not in err


def test_connect_names_the_missing_secret(monkeypatch):
    for k in ("APP_DB_HOST", "APP_DB_USER", "APP_DB_PASSWORD", "APP_DB_NAME"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(RuntimeError, match="app-tcms-db"):
        run.connect()


def test_tool_yaml_shape():
    import yaml
    doc = yaml.safe_load((HERE / "tool.yaml").read_text())
    assert doc["name"] == "tcms"
    assert doc["infra"]["secrets"] == ["app-tcms-db"]
    assert doc["timeout_seconds"] == 120
    props = doc["params"]["properties"]
    assert props["action"]["enum"] == ["sync_cases", "record_results", "cases", "case",
                                       "coverage_gaps", "runtime_report", "flaky",
                                       "prune_candidates"]
    assert doc["params"]["required"] == ["action"]
    # `files` is the broker's reserved argument (docs/building-blocks/tools.md):
    # the registry refuses a manifest that declares it, so the guidance lives
    # in the description instead.
    assert "files" not in props
    assert "ap-upload" in doc["description"] and "count" in doc["description"]
    for name in ("commit_sha", "branch", "verify", "key", "layer", "area", "status",
                 "unlinked", "q", "limit", "threshold_pct", "runs"):
        assert name in props, name
    assert "psycopg[binary]" in (HERE / "requirements.txt").read_text()
