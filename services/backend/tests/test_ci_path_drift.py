"""The CI path filters (.github/workflows/ci.yaml, `changes` job) and bin/ap-verify
keep separate glob lists. Drift in one direction means a local `--changed` run
checks something CI would skip. This holds every ap-verify suite's globs inside
the CI job that runs the same suite."""
import importlib.machinery
import importlib.util
import re

import pytest
import yaml

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]

# ap-verify suite -> the CI job (a `changes` output) that runs it. A new suite
# must be added here, which is the point: the author has to decide where CI runs it.
EXACT = {"backend": "backend", "sdk": "backend", "broker": "backend",
         "connector-discord": "backend", "facade": "facade", "executor": "tools",
         "runner": "runner", "helm": "chart", "claude-proxy": "chart",
         "codex-proxy": "codex_proxy"}
PATTERNS = [(r"web-.*", "web"), (r"app-.*-backend", "apps"),
            (r"app-.*-frontend", "web"), (r"tool-.*", "tools")]


def _job_for(suite):
    if suite in EXACT:
        return EXACT[suite]
    for pat, job in PATTERNS:
        if re.fullmatch(pat, suite):
            return job
    pytest.fail(f"ap-verify suite {suite!r} has no CI job mapping in test_ci_path_drift.py")


def _regex(glob):
    out, i = "", 0
    while i < len(glob):
        if glob.startswith("**", i):
            out, i = out + ".*", i + 2
        elif glob[i] == "*":
            out, i = out + "[^/]*", i + 1
        else:
            out, i = out + re.escape(glob[i]), i + 1
    return re.compile(out + r"\Z")


def _ci_filters():
    wf = yaml.safe_load((REPO_ROOT / ".github/workflows/ci.yaml").read_text())
    steps = {s["id"]: yaml.safe_load(s["with"]["filters"])
             for s in wf["jobs"]["changes"]["steps"] if s.get("id") in ("f", "g")}
    return steps["f"], steps["g"]["backend"]


def _ci_jobs(path, some, backend):
    if any(_regex(g).match(path) for g in some["global"]):
        return {"ALL"}
    jobs = {job for job, globs in some.items() if any(_regex(g).match(path) for g in globs)}
    pos = [g for g in backend if not g.startswith("!")]
    neg = [g[1:] for g in backend if g.startswith("!")]
    if any(_regex(g).match(path) for g in pos) and not any(_regex(g).match(path) for g in neg):
        jobs.add("backend")
    return jobs


def _sample(glob):
    return glob.replace("**", "x/sample.py").replace("*", "x")


def test_every_ap_verify_glob_selects_its_ci_job():
    loader = importlib.machinery.SourceFileLoader("ap_verify", str(REPO_ROOT / "bin/ap-verify"))
    spec = importlib.util.spec_from_loader("ap_verify", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    some, backend = _ci_filters()
    gaps = []
    for suite in mod.suite_table(REPO_ROOT):
        job = _job_for(suite["name"])
        for glob in suite["globs"]:
            jobs = _ci_jobs(_sample(glob), some, backend)
            if "ALL" not in jobs and job not in jobs:
                gaps.append(f"{suite['name']}: {glob} would not select CI job {job!r}")
    assert gaps == []


def test_web_only_changes_skip_the_backend_and_everything_else_reaches_it():
    some, backend = _ci_filters()
    assert "backend" not in _ci_jobs("services/web/src/App.tsx", some, backend)
    assert "web" in _ci_jobs("services/web/src/App.tsx", some, backend)
    for path in ("docs/building-blocks/apps.md", "plugins/x/skills/a/SKILL.md", "tools/a/run.py",
                 "charts/agent-platform/values.yaml", "tcms/cases/apps.yaml"):
        assert "backend" in _ci_jobs(path, some, backend), path
    assert _ci_jobs(".github/workflows/ci.yaml", some, backend) == {"ALL"}
