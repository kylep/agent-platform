"""The TCMS case files (`tcms/cases/*.yaml`, design 25) and their one
validator (`tools/tcms/cases.py`).

The validator is loaded by path: it ships inside the tool image, where
`agentplatform` does not exist, so this test is also the guard that it never
grows an import of it. The real case tree is under test too — every
`pytest:` ref must name a function that exists in the file it points at and
every `playwright:` ref a title that appears in its spec, so a renamed test
fails CI here rather than silently unlinking its case at the next nightly.
"""
import importlib.machinery
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
CASES_PY = REPO_ROOT / "tools" / "tcms" / "cases.py"
CASES_DIR = REPO_ROOT / "tcms" / "cases"


def _load():
    loader = importlib.machinery.SourceFileLoader("tcms_cases", str(CASES_PY))
    spec = importlib.util.spec_from_loader("tcms_cases", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def cases_mod():
    return _load()


@pytest.fixture(scope="module")
def real(cases_mod):
    return cases_mod.load_suites(CASES_DIR)


# --- the module itself -------------------------------------------------------

def test_the_validator_imports_nothing_from_agentplatform():
    src = CASES_PY.read_text()
    assert not re.search(r"^\s*(from|import)\s+agentplatform", src, re.MULTILINE)


# --- the shipped case tree ----------------------------------------------------

def test_the_real_tree_loads_with_zero_errors(real):
    suites, errors = real
    assert errors == []
    assert {s.name for s in suites} >= {"relay", "tickets", "wiki", "quota",
                                        "artifacts", "workbench", "apps", "tools"}


def test_the_real_tree_is_at_least_the_first_forty(real):
    suites, _ = real
    cases = [c for s in suites for c in s.cases]
    assert len(cases) >= 40
    layers = {c.layer for c in cases}
    assert layers == {"unit", "integration", "e2e", "manual"}
    assert sum(1 for c in cases if c.layer == "manual") >= 3
    # Priorities are a judgement, but a tree where everything is p0 (or
    # nothing is) has not been judged.
    assert len({c.priority for c in cases}) >= 3


def _pytest_function_names(path: Path) -> set[str]:
    return set(re.findall(r"^(?:async\s+)?def\s+(test_\w+)\s*\(",
                          path.read_text(), re.MULTILINE))


def test_every_pytest_ref_names_a_function_in_its_file(real, cases_mod):
    suites, _ = real
    missing = []
    for suite in suites:
        for case in suite.cases:
            for ref in case.automation:
                kind, path, name = cases_mod.split_ref(ref)
                if kind != "pytest":
                    continue
                file = REPO_ROOT / path
                if not file.is_file():
                    missing.append((case.key, ref, "no such file"))
                    continue
                # `Class::test_x[param]` → `test_x`.
                func = name.split("::")[-1].split("[", 1)[0]
                if func not in _pytest_function_names(file):
                    missing.append((case.key, ref, "no such function"))
    assert missing == []


def test_every_playwright_ref_names_a_title_in_its_spec(real, cases_mod):
    suites, _ = real
    missing = []
    for suite in suites:
        for case in suite.cases:
            for ref in case.automation:
                kind, path, title = cases_mod.split_ref(ref)
                if kind != "playwright":
                    continue
                file = REPO_ROOT / path
                if not file.is_file():
                    missing.append((case.key, ref, "no such file"))
                elif title not in file.read_text():
                    missing.append((case.key, ref, "title not in spec"))
    assert missing == []


def test_manual_cases_have_no_automation_and_automated_cases_have_some(real):
    suites, _ = real
    for suite in suites:
        for case in suite.cases:
            assert (case.layer == "manual") == (case.automation == ()), case.key


# --- the validator against a fixture tree -------------------------------------

GOOD = """\
suite: good
area: relay
cases:
  - key: good.one
    title: One good case
    layer: integration
    priority: p1
    steps: [do the thing]
    expected: it works
    automation:
      - pytest:services/backend/tests/test_relay_api.py::test_reactions_toggle
  - key: good.two
    title: A manual one
    layer: manual
    priority: p3
    expected: a human looked
"""

BAD = """\
suite: bad
area: relay
cases:
  - key: bad.dup
    title: first
    layer: unit
    priority: p2
    expected: x
    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]
  - key: bad.dup
    title: duplicate key
    layer: unit
    priority: p2
    expected: x
    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]
  - key: bad.layer
    title: bad layer
    layer: smoke
    priority: p2
    expected: x
    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]
  - key: bad.dotdot
    title: a ref that climbs
    layer: unit
    priority: p2
    expected: x
    automation: [pytest:../secrets/test_x.py::test_y]
  - key: other.prefix
    title: wrong prefix
    layer: unit
    priority: p2
    expected: x
    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]
  - key: bad.long
    title: "{long}"
    layer: unit
    priority: p2
    expected: x
    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]
"""


@pytest.fixture
def fixture_tree(tmp_path):
    (tmp_path / "good.yaml").write_text(GOOD)
    (tmp_path / "bad.yaml").write_text(BAD.replace("{long}", "t" * 161))
    return tmp_path


def test_a_bad_file_yields_its_errors_and_the_good_file_still_loads(
        cases_mod, fixture_tree):
    suites, errors = cases_mod.load_suites(fixture_tree)
    assert [s.name for s in suites] == ["good"]
    assert [c.key for c in suites[0].cases] == ["good.one", "good.two"]
    assert suites[0].cases[1].automation == ()
    assert all(f == "bad.yaml" for f, _, _ in errors)
    by_key = {k: m for _, k, m in errors}
    assert set(by_key) == {"bad.dup", "bad.layer", "bad.dotdot",
                           "other.prefix", "bad.long"}
    assert "duplicate" in by_key["bad.dup"]
    assert "layer" in by_key["bad.layer"]
    assert "not a repository path" in by_key["bad.dotdot"]
    assert "bad." in by_key["other.prefix"]
    assert "160" in by_key["bad.long"]


def test_the_prefix_rule_makes_keys_unique_across_files(cases_mod, tmp_path):
    """A second file cannot claim `good.two`: every key must start with its
    own file's stem, so the cross-file uniqueness the design asks for is the
    prefix rule plus unique filenames — no separate check to drift."""
    (tmp_path / "good.yaml").write_text(GOOD)
    (tmp_path / "other.yaml").write_text(
        GOOD.replace("suite: good", "suite: other").replace("good.one", "other.one"))
    suites, errors = cases_mod.load_suites(tmp_path)
    assert [s.name for s in suites] == ["good"]
    assert errors == [("other.yaml", "good.two",
                       "key good.two must start with 'other.'")]


def test_a_file_that_is_not_a_suite_is_one_file_level_error(cases_mod, tmp_path):
    (tmp_path / "broken.yaml").write_text("suite: [\n")
    (tmp_path / "list.yaml").write_text("- not\n- a mapping\n")
    (tmp_path / "stem.yaml").write_text("suite: other\narea: x\ncases: []\n")
    suites, errors = cases_mod.load_suites(tmp_path)
    assert suites == []
    assert [(f, k) for f, k, _ in errors] == [
        ("broken.yaml", None), ("list.yaml", None), ("stem.yaml", None)]


def test_list_and_string_caps_are_enforced(cases_mod, tmp_path):
    steps = "\n".join(f"      - step {i}" for i in range(21))
    (tmp_path / "cap.yaml").write_text(
        "suite: cap\narea: x\ncases:\n"
        "  - key: cap.steps\n    title: t\n    layer: unit\n    priority: p1\n"
        f"    expected: x\n    steps:\n{steps}\n"
        "    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]\n"
        "  - key: cap.expected\n    title: t\n    layer: unit\n    priority: p1\n"
        f"    expected: \"{'e' * 2001}\"\n"
        "    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]\n"
        "  - key: cap.priority\n    title: t\n    layer: unit\n    priority: p4\n"
        "    expected: x\n"
        "    automation: [pytest:tools/memory/test_run.py::test_read_clamps_limit]\n"
        "  - key: cap.ref\n    title: t\n    layer: unit\n    priority: p1\n"
        "    expected: x\n    automation: [junit:tools/x.py::y]\n"
        "  - key: cap.ticket\n    title: t\n    layer: manual\n    priority: p1\n"
        "    expected: x\n    tickets: [lowercase-1]\n")
    suites, errors = cases_mod.load_suites(tmp_path)
    assert suites == []
    assert {k for _, k, _ in errors} == {"cap.steps", "cap.expected",
                                         "cap.priority", "cap.ref", "cap.ticket"}


def _one_case_with_ref(ref: str) -> str:
    # json.dumps: a double-quoted YAML scalar, so a control char travels as
    # an escape and lands in the loaded string as itself.
    return ("suite: r\narea: x\ncases:\n"
            "  - key: r.one\n    title: t\n    layer: unit\n    priority: p1\n"
            f"    expected: x\n    automation: [{json.dumps(ref)}]\n")


@pytest.mark.parametrize("ref", [
    "pytest:/etc/passwd::t",
    "pytest://x::t",
    "pytest:a//b::t",
    "pytest:./a.py::t",
    "pytest:a/./b.py::t",
    "pytest:a/../b.py::t",
    "playwright:a.spec.ts::a title\rwith a return",
    "playwright:a.spec.ts::a title\x07with a bell",
])
def test_a_ref_that_is_not_a_repo_path_or_carries_a_control_char_is_refused(
        cases_mod, tmp_path, ref):
    (tmp_path / "r.yaml").write_text(_one_case_with_ref(ref))
    suites, errors = cases_mod.load_suites(tmp_path)
    assert suites == []
    assert [(f, k) for f, k, _ in errors] == [("r.yaml", "r.one")]


def test_an_oversized_file_is_refused_unparsed(cases_mod, tmp_path):
    (tmp_path / "big.yaml").write_text("# " + "x" * (cases_mod.MAX_FILE_BYTES))
    suites, errors = cases_mod.load_suites(tmp_path)
    assert suites == [] and errors[0][:2] == ("big.yaml", None)


def test_split_ref_splits_on_the_first_double_colon(cases_mod):
    assert cases_mod.split_ref(
        "playwright:services/web/tests/a.spec.ts::a title::with colons"
    ) == ("playwright", "services/web/tests/a.spec.ts", "a title::with colons")
    assert cases_mod.split_ref(
        "pytest:services/backend/tests/test_x.py::TestK::test_y[p]"
    ) == ("pytest", "services/backend/tests/test_x.py", "TestK::test_y[p]")
