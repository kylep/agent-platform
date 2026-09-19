"""bin/ap-verify — the one verifier the engineer, the runner and a laptop share.

The script has no .py suffix and no package, so it is loaded here by path.
Nothing in these tests runs a real suite: fake tables inject tiny commands.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "bin" / "ap-verify"


def _load():
    loader = importlib.machinery.SourceFileLoader("ap_verify", str(SCRIPT))
    spec = importlib.util.spec_from_loader("ap_verify", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def apv():
    return _load()


def _names(suites):
    return [s["name"] for s in suites]


# ---------------------------------------------------------------- mapping


@pytest.mark.parametrize(
    "path,expected",
    [
        ("services/backend/agentplatform/db.py", "backend"),
        ("sdk/agent_platform_sdk/client.py", "backend"),
        ("services/mcp-broker/broker.py", "broker"),
        ("services/mcp-facade/facade.py", "facade"),
        ("services/tool-executor/executor.py", "executor"),
        ("services/runner/runner.py", "runner"),
        ("services/connector-discord/connector.py", "connector-discord"),
        ("sdk/agent_platform_sdk/client.py", "sdk"),
        ("services/backend/agentplatform/api/agents.py", "sdk"),
        ("services/backend/agentplatform/api/agents.py", "facade"),
        ("services/web/src/App.tsx", "web-lint"),
        ("services/web/src/App.tsx", "web-storybook"),
        ("packages/ui/src/sidenav.tsx", "web-playwright"),
        ("apps/news/backend/newsapp/__init__.py", "app-news-backend"),
        ("apps/news/frontend/src/main.tsx", "app-news-frontend"),
        ("tools/prices/run.py", "tool-prices"),
        ("charts/agent-platform/values.yaml", "helm"),
    ],
)
def test_path_maps_to_suite(apv, path, expected):
    table = apv.suite_table(REPO_ROOT)
    assert expected in _names(apv.select(table, [path]))


def test_tool_change_also_runs_executor(apv):
    table = apv.suite_table(REPO_ROOT)
    names = _names(apv.select(table, ["tools/prices/run.py"]))
    assert "executor" in names and "tool-prices" in names


def test_web_change_runs_the_web_steps_in_ci_order(apv):
    table = apv.suite_table(REPO_ROOT)
    names = _names(apv.select(table, ["services/web/src/api.ts"]))
    assert names == ["web-lint", "web-tokens", "web-build", "web-storybook", "web-playwright"]


def test_runner_suite_runs_the_directory_like_ci(apv):
    table = apv.suite_table(REPO_ROOT)
    runner = next(s for s in table if s["name"] == "runner")
    assert runner["cwd"] == "services/runner"
    assert not any(a.endswith(".py") for a in runner["cmd"])


def test_sdk_suite_regenerates_then_diffs_from_the_root(apv):
    table = apv.suite_table(REPO_ROOT)
    sdk = next(s for s in table if s["name"] == "sdk")
    assert sdk["cwd"] == "."
    script = sdk["cmd"][-1]
    assert "sdk/regenerate.py" in script and "git diff --exit-code -- sdk/" in script
    assert apv.python_for_suites() in script
    assert "git checkout -- sdk/" in sdk["on_fail_note"]


def test_helm_suites_build_dependencies_first(apv):
    table = apv.suite_table(REPO_ROOT)
    for name in ("helm", "claude-proxy"):
        suite = next(s for s in table if s["name"] == name)
        assert "helm dependency build" in suite["cmd"][-1]
        assert "helm" in suite["requires"]


def test_docs_map_to_nothing(apv):
    table = apv.suite_table(REPO_ROOT)
    assert apv.select(table, ["docs/design/24-coding-agent.md", "README.md"]) == []


def test_mixed_change_picks_both(apv):
    table = apv.suite_table(REPO_ROOT)
    names = _names(apv.select(table, ["services/backend/x.py", "services/mcp-broker/y.py"]))
    assert names == ["backend", "broker"]


def test_select_all_returns_whole_table_in_order(apv):
    table = apv.suite_table(REPO_ROOT)
    assert _names(apv.select(table, None)) == _names(table)


# ---------------------------------------------------------------- backend flags


def test_backend_gets_junit_and_cov_only_when_pytest_cov_imports(apv, tmp_path, monkeypatch):
    monkeypatch.setattr(apv, "has_module", lambda py, name: True)
    table = apv.suite_table(REPO_ROOT, out=tmp_path)
    backend = next(s for s in table if s["name"] == "backend")
    cmd = " ".join(backend["cmd"])
    assert f"--junitxml={tmp_path / 'junit-backend.xml'}" in cmd
    assert "--cov=agentplatform" in cmd
    assert f"--cov-report=xml:{tmp_path / 'coverage-backend.xml'}" in cmd

    monkeypatch.setattr(apv, "has_module", lambda py, name: False)
    table = apv.suite_table(REPO_ROOT, out=tmp_path)
    backend = next(s for s in table if s["name"] == "backend")
    cmd = " ".join(backend["cmd"])
    assert "--junitxml" in cmd and "--cov" not in cmd


def test_playwright_reporters_point_into_out(apv, tmp_path):
    table = apv.suite_table(REPO_ROOT, out=tmp_path)
    pw = next(s for s in table if s["name"] == "web-playwright")
    assert "--reporter=line,junit,json" in pw["cmd"]
    assert pw["env"]["PLAYWRIGHT_JUNIT_OUTPUT_NAME"] == str(tmp_path / "junit-web.xml")
    assert pw["env"]["PLAYWRIGHT_JSON_OUTPUT_NAME"] == str(tmp_path / "playwright-web.json")


def test_agentplatform_importers_get_pythonpath(apv):
    table = apv.suite_table(REPO_ROOT)
    facade = next(s for s in table if s["name"] == "facade")
    assert str(REPO_ROOT / "services" / "backend") in facade["env"]["PYTHONPATH"].split(":")
    assert str(REPO_ROOT / "sdk") in facade["env"]["PYTHONPATH"].split(":")


# ---------------------------------------------------------------- running


def _fake(name, code, **extra):
    s = {
        "name": name, "globs": ["x/**"], "cwd": ".",
        "cmd": [sys.executable, "-c", code], "env": {}, "requires": [],
    }
    s.update(extra)
    return s


def test_failing_suite_yields_ok_false_with_exit_and_tail(apv, tmp_path):
    suites = [_fake("bad", "print('line one'); print('boom'); raise SystemExit(1)")]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30)
    assert result["ok"] is False
    row = result["suites"][0]
    assert row["exit"] == 1
    assert row["skipped_reason"] is None
    assert row["tail"].splitlines()[-1] == "boom"
    assert row["seconds"] >= 0


def test_passing_suite_is_ok(apv, tmp_path):
    suites = [_fake("good", "print('fine')")]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30)
    assert result["ok"] is True
    assert result["suites"][0]["exit"] == 0


def test_timeout_is_a_failure_not_a_skip(apv):
    suites = [_fake("slow", "import time; print('start', flush=True); time.sleep(30)")]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=1)
    row = result["suites"][0]
    assert row["exit"] is None
    assert row["skipped_reason"] is None
    assert "timed out" in row["tail"]
    assert result["ok"] is False


def test_suites_run_without_the_pods_platform_env(apv, monkeypatch):
    """In the dev pod the platform's own settings are in the environment
    (AP_API_URL, AP_SESSION_TOKEN, the Kubernetes service variables), and the
    backend's tests read AP_* as configuration — six of them failed only in
    the pod. Every suite runs with those scrubbed; AP_VERIFY_PYTHON stays, it
    is ap-verify's own, and the suite table's env still lands."""
    monkeypatch.setenv("AP_API_URL", "http://api:8090")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_secret")
    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "10.0.0.1")
    monkeypatch.setenv("AP_VERIFY_PYTHON", sys.executable)
    monkeypatch.setenv("AP_WORKSPACE", "dev")
    monkeypatch.setenv("UNRELATED_VAR", "kept")
    suites = [_fake("env", "import json, os; print(json.dumps(dict(os.environ)))",
                    env={"PYTHONPATH": "/x"})]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30)
    child = json.loads(result["suites"][0]["tail"].splitlines()[-1])
    assert not [k for k in child if k.startswith("AP_") and k not in ("AP_VERIFY_PYTHON", "AP_WORKSPACE")]
    assert not [k for k in child if k.startswith("KUBERNETES_")]
    assert "AP_SESSION_TOKEN" not in child and "AP_API_URL" not in child
    assert "KUBERNETES_SERVICE_HOST" not in child
    assert "ap_secret" not in json.dumps(child)
    assert child["AP_VERIFY_PYTHON"] == sys.executable
    assert child["AP_WORKSPACE"] == "dev"   # playwright.config.ts: no Chromium sandbox in the pod
    assert child["UNRELATED_VAR"] == "kept" and child["PYTHONPATH"] == "/x"
    assert "PATH" in child


def test_missing_tool_is_skipped_not_failed(apv):
    suites = [_fake("needs-tool", "print('never')", requires=["definitely-not-a-real-binary-xyz"])]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30)
    row = result["suites"][0]
    assert row["exit"] is None
    assert "definitely-not-a-real-binary-xyz" in row["skipped_reason"]
    assert result["ok"] is True


def test_tail_is_last_forty_lines(apv):
    suites = [_fake("chatty", "print('\\n'.join(str(i) for i in range(100)))")]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30)
    lines = result["suites"][0]["tail"].splitlines()
    assert len(lines) == 40 and lines[0] == "60" and lines[-1] == "99"


def test_output_streams_to_the_log_and_only_the_tail_stays(apv, tmp_path):
    suites = [_fake("firehose", "import sys; sys.stdout.write(''.join(f'{i}\\n' for i in range(200000)))")]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=60, out=tmp_path)
    row = result["suites"][0]
    assert row["exit"] == 0
    lines = row["tail"].splitlines()
    assert len(lines) == 40 and lines[-1] == "199999"
    log = (tmp_path / "firehose.log").read_text().splitlines()
    assert len(log) == 200000 and log[0] == "0" and log[-1] == "199999"


def test_on_fail_note_lands_in_the_tail_only_on_failure(apv):
    note = "[ap-verify] left the tree as is"
    bad = _fake("bad", "raise SystemExit(1)", on_fail_note=note)
    good = _fake("good", "pass", on_fail_note=note)
    result = apv.run_suites([bad, good], root=REPO_ROOT, timeout=30)
    assert note in result["suites"][0]["tail"]
    assert note not in result["suites"][1]["tail"]


# ---------------------------------------------------------------- verify.json + cli


def test_verify_json_shape(apv, tmp_path):
    out = tmp_path / "v"
    suites = [_fake("good", "print('ok')")]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30, out=out)
    result.update({"base": "origin/main", "head": "abc", "changed": ["x/y.py"]})
    apv.write_report(out, result)
    data = json.loads((out / "verify.json").read_text())
    assert set(data) >= {"ok", "base", "head", "changed", "suites", "files"}
    row = data["suites"][0]
    assert set(row) >= {"name", "cmd", "cwd", "exit", "seconds", "skipped_reason", "tail"}
    assert isinstance(data["files"], list)


def test_files_are_relative_to_out(apv, tmp_path):
    out = tmp_path / "v"
    out.mkdir()
    (out / "junit-x.xml").write_text("<x/>")
    suites = [_fake("good", "pass", artifacts=[out / "junit-x.xml", out / "absent.xml"])]
    result = apv.run_suites(suites, root=REPO_ROOT, timeout=30, out=out)
    assert result["files"] == ["junit-x.xml"]


def test_list_prints_every_suite_without_running(apv, capsys):
    rc = apv.main(["--list", "--all"])
    assert rc == 0
    text = capsys.readouterr().out
    for name in ("backend", "sdk", "broker", "facade", "executor", "runner", "web-lint",
                 "web-storybook", "web-playwright", "helm", "connector-discord"):
        assert name in text


def test_cli_exit_mirrors_ok(apv, tmp_path, monkeypatch):
    monkeypatch.setattr(apv, "suite_table",
                        lambda root, out=None: [_fake("bad", "raise SystemExit(3)", globs=["**"])])
    out = tmp_path / "verify"
    assert apv.main(["--all", "--out", str(out)]) == 1
    data = json.loads((out / "verify.json").read_text())
    assert data["ok"] is False and data["suites"][0]["exit"] == 3

    monkeypatch.setattr(apv, "suite_table",
                        lambda root, out=None: [_fake("good", "pass", globs=["**"])])
    assert apv.main(["--all", "--out", str(out)]) == 0


def test_skip_marks_a_suite_skipped_without_failing(apv, tmp_path, monkeypatch):
    monkeypatch.setattr(apv, "suite_table", lambda root, out=None: [
        _fake("bad", "raise SystemExit(1)", globs=["**"]),
        _fake("good", "pass", globs=["**"])])
    out = tmp_path / "verify"
    assert apv.main(["--all", "--out", str(out), "--skip", "bad"]) == 0
    rows = {r["name"]: r for r in json.loads((out / "verify.json").read_text())["suites"]}
    assert rows["bad"]["exit"] is None and "--skip" in rows["bad"]["skipped_reason"]
    assert rows["good"]["exit"] == 0


def test_script_is_executable_and_stdlib_only():
    assert SCRIPT.stat().st_mode & 0o111
    first = SCRIPT.read_text().splitlines()[0]
    assert first == "#!/usr/bin/env python3"
    # Every top-level import must resolve without the venv: the runner and a
    # bare laptop python both have to run it.
    r = subprocess.run([sys.executable, "-I", "-c",
                        "import ast,sys;t=ast.parse(open(sys.argv[1]).read());"
                        "mods={n.name.split('.')[0] for s in t.body if isinstance(s,ast.Import) for n in s.names}"
                        "|{s.module.split('.')[0] for s in t.body if isinstance(s,ast.ImportFrom)};"
                        "bad=[m for m in mods if m not in sys.stdlib_module_names];print(bad);sys.exit(bool(bad))",
                        str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# ---------------------------------------------------------------- junit merge (--all)

JUNIT_BACKEND = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="2" tests="5" time="1.500">
<testcase classname="tests.test_a" name="test_one" time="0.1"/>
<testcase classname="tests.test_a" name="test_two" time="0.2"><failure message="boom">x</failure></testcase>
</testsuite></testsuites>
"""
JUNIT_WEB = """<testsuites id="" name="" tests="3" failures="0" skipped="1" errors="0" time="2.25">
<testsuite name="smoke.spec.ts" timestamp="2026-09-19T01:02:03.000Z" hostname="chromium" tests="2" failures="0" skipped="0" time="1.0" errors="0">
<testcase name="home renders" classname="smoke.spec.ts" time="0.5"/>
</testsuite>
<testsuite name="a11y.spec.ts" timestamp="2026-09-19T01:02:04.000Z" hostname="chromium" tests="1" failures="0" skipped="1" time="1.25" errors="0">
<testcase name="axe" classname="a11y.spec.ts" time="1.25"><skipped/></testcase>
</testsuite>
</testsuites>
"""


def _junit_fixtures(out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    a, b = out / "junit-backend.xml", out / "junit-web.xml"
    a.write_text(JUNIT_BACKEND)
    b.write_text(JUNIT_WEB)
    return [a, b]


def test_merge_junit_concatenates_every_testsuite_under_one_root(apv, tmp_path):
    """Two per-suite files — pytest's (one <testsuite> under <testsuites>)
    and Playwright's (several) — become one <testsuites> carrying every
    <testsuite> in file order, with the counts summed on the root so a reader
    that only looks at the top line still gets the totals."""
    import xml.etree.ElementTree as ET
    out = tmp_path / "v"
    files = _junit_fixtures(out)
    merged = apv.merge_junit(files, out / "junit-all.xml")
    assert merged == out / "junit-all.xml"
    root = ET.parse(merged).getroot()
    assert root.tag == "testsuites"
    assert [s.get("name") for s in root] == ["pytest", "smoke.spec.ts", "a11y.spec.ts"]
    assert all(s.tag == "testsuite" for s in root)
    assert root.get("tests") == "8" and root.get("failures") == "1"
    assert root.get("skipped") == "3" and root.get("errors") == "0"
    assert root.get("time") == "3.75"
    # The cases travel intact.
    names = [c.get("name") for c in root.iter("testcase")]
    assert names == ["test_one", "test_two", "home renders", "axe"]
    assert root.find("./testsuite/testcase/failure").get("message") == "boom"


def test_merge_junit_is_a_parser_of_untrusted_files(apv, tmp_path, capsys):
    """The files are produced in the agent's workspace: a DOCTYPE or an ENTITY
    is refused before parsing, an unparseable file is skipped with a line on
    stderr, an oversized one likewise — and the good ones still merge."""
    out = tmp_path / "v"
    good, _ = _junit_fixtures(out)
    evil = out / "junit-evil.xml"
    evil.write_text('<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><testsuites/>')
    broken = out / "junit-broken.xml"
    broken.write_text("<testsuites><testsuite")
    big = out / "junit-big.xml"
    big.write_text("<testsuites>" + " " * (apv.JUNIT_MAX_BYTES + 1) + "</testsuites>")
    merged = apv.merge_junit([evil, broken, big, good], out / "junit-all.xml")
    import xml.etree.ElementTree as ET
    root = ET.parse(merged).getroot()
    assert [s.get("name") for s in root] == ["pytest"]
    err = capsys.readouterr().err
    assert "junit-evil.xml" in err and "junit-broken.xml" in err and "junit-big.xml" in err


def test_merge_junit_with_nothing_to_merge_writes_nothing(apv, tmp_path):
    out = tmp_path / "v"; out.mkdir()
    assert apv.merge_junit([], out / "junit-all.xml") is None
    assert apv.merge_junit([out / "absent.xml"], out / "junit-all.xml") is None
    assert not (out / "junit-all.xml").exists()


def test_all_writes_junit_all_and_lists_it(apv, tmp_path, monkeypatch):
    """`--all` is the nightly: its report names the merged file beside the
    per-suite ones, so the upload is three files (junit, Playwright JSON,
    coverage) and the TCMS reads one JUnit document."""
    out = tmp_path / "verify"
    files = _junit_fixtures(out)
    monkeypatch.setattr(apv, "suite_table", lambda root, out=None: [
        _fake("backend", "pass", globs=["**"], artifacts=[files[0]]),
        _fake("web-playwright", "pass", globs=["**"], artifacts=[files[1]])])
    assert apv.main(["--all", "--out", str(out)]) == 0
    data = json.loads((out / "verify.json").read_text())
    assert data["files"] == ["junit-backend.xml", "junit-web.xml", "junit-all.xml"]
    assert (out / "junit-all.xml").is_file()


def test_changed_does_not_write_junit_all(apv, tmp_path, monkeypatch):
    """`--changed` is the mid-run check and the publish's evidence: the
    per-suite files as before, no merge (nothing ingests a partial run)."""
    out = tmp_path / "verify"
    files = _junit_fixtures(out)
    monkeypatch.setattr(apv, "suite_table", lambda root, out=None: [
        _fake("backend", "pass", globs=["**"], artifacts=[files[0]])])
    monkeypatch.setattr(apv, "changed_paths", lambda root, base: (["x/y.py"], "abc123abc123"))
    assert apv.main(["--changed", "--out", str(out)]) == 0
    data = json.loads((out / "verify.json").read_text())
    assert data["files"] == ["junit-backend.xml"]
    assert not (out / "junit-all.xml").exists()
