"""The path policy behind publish: one glob matcher, one definition of "test
code", one deny list, three rules in one order.

The matcher is pinned against GitHub's ruleset fnmatch semantics (`**` is
whole segments, `*` never crosses `/`, case-sensitive, anchored) because the
platform's fence and Kyle's ruleset backstop must agree about what a test
path is. Every `TEST_PATH_GLOBS` entry is checked against a real path from
the tree and a near-miss that must stay outside it."""
import time

import pytest

from agentplatform import testpaths
from agentplatform.testpaths import (
    PUBLISH_DENY_GLOBS,
    TEST_PATH_GLOBS,
    Change,
    check_policy,
    is_test_path,
    match,
)


@pytest.mark.parametrize("pattern,path,expected", [
    # `**` spans zero segments...
    ("a/**/b", "a/b", True),
    # ...one, and many.
    ("a/**/b", "a/x/b", True),
    ("a/**/b", "a/x/y/z/b", True),
    ("**/conftest.py", "conftest.py", True),
    ("**/conftest.py", "x/y/conftest.py", True),
    # A trailing `**` takes anything beneath, at any depth, but not the
    # directory's own siblings.
    ("tests/**", "tests/a.py", True),
    ("tests/**", "tests/x/y/a.py", True),
    ("tests/**", "tests2/a.py", False),
    ("tests/**", "tests", False),
    # `*` and `?` stay inside a segment.
    ("a/*.py", "a/b.py", True),
    ("a/*.py", "a/b/c.py", False),
    ("*/test_*.py", "x/test_y.py", True),
    ("*/test_*.py", "x/y/test_z.py", False),
    ("a/?.py", "a/b.py", True),
    ("a/?.py", "a/bb.py", False),
    ("a/?.py", "a//.py", False),
    # A `*` matches the empty string, so `test_*.py` covers `test_.py`.
    ("test_*.py", "test_.py", True),
    # Case-sensitive.
    ("tests/**", "Tests/a.py", False),
    ("a/*.PY", "a/b.py", False),
    # Anchored to the whole path at both ends.
    ("tests/**", "x/tests/a.py", False),
    ("a/b", "a/b/c", False),
    ("a/b", "xa/b", False),
    ("a/b", "a/b", True),
    # Regex metacharacters in a pattern are literal.
    ("a.py", "aXpy", False),
    ("a+b", "a+b", True),
    ("a+b", "aab", False),
])
def test_match(pattern, path, expected):
    assert match(pattern, path) is expected


def test_match_caches_the_compiled_pattern():
    testpaths._compile.cache_clear()
    match("a/**/b", "a/b")
    match("a/**/b", "a/x/b")
    info = testpaths._compile.cache_info()
    assert info.misses == 1 and info.hits == 1


@pytest.mark.parametrize("pattern,path", [
    ("a/**/**/b", "a/b"), ("a/**/**/b", "a/x/y/b"), ("a/**/**/b", "a/x/c"),
    ("a**b", "ab"), ("a**b", "axxb"), ("a**b", "a/b"),
    ("*?*", "x"), ("*?*", ""), ("*?*", "xyz"),
])
def test_normalisation_keeps_meaning(pattern, path):
    # A pattern and its normal form are the same matcher.
    assert match(pattern, path) is match(testpaths.normalise(pattern), path)


def test_normalisation_equivalences():
    assert testpaths._compile("a/**/**/b") == testpaths._compile("a/**/b")
    assert testpaths._compile("a**b") == testpaths._compile("a*b")
    assert testpaths._compile("a***?*b") == testpaths._compile("a?*b")
    assert testpaths._compile("**/**/**") == testpaths._compile("**")


# push_path_globs is untrusted input: the matcher must stay fast against a
# pattern built to make a backtracking engine crawl, at every shape it can
# take — repeated `**` segments, star runs, and stars alternating with
# literals, which no amount of run-collapsing helps. (Each is the largest
# shape the pattern bounds admit; anything bigger is refused outright.)
@pytest.mark.parametrize("pattern", [
    "/".join(["**"] * 15) + "/z",
    "*" * 100 + "z",
    "*a" * 40 + "b",
    "*?" * 30 + "*z",
    "**/" * 8 + "*a" * 20 + "z",
])
def test_hostile_patterns_finish_fast(pattern):
    path = "a" * 200
    deep = "/".join(["a"] * 15)
    for p in (path, deep, deep + "/" + path):
        start = time.perf_counter()
        assert match(pattern, p) is False
        assert time.perf_counter() - start < 0.05


@pytest.mark.parametrize("pattern", [
    "a" * 201,
    "/".join(["a"] * 17),
    "",
])
def test_oversized_or_empty_pattern_is_rejected(pattern):
    with pytest.raises(ValueError):
        match(pattern, "a")


def test_pattern_bounds_are_inclusive():
    assert match("a" * 200, "a" * 200)
    assert match("/".join(["a"] * 16), "/".join(["a"] * 16))


def test_test_path_globs_are_the_design_list():
    assert TEST_PATH_GLOBS == [
        "services/backend/tests/**",
        "services/web/tests/**",
        "services/claude-proxy/tests/**",
        "services/*/test_*.py",
        "apps/*/backend/test_*.py",
        "apps/*/backend/tests/**",
        "tools/*/test_run.py",
        "tcms/cases/**",
    ]


def test_publish_deny_globs_are_the_design_list():
    assert PUBLISH_DENY_GLOBS == [
        ".github/**",
        ".pre-commit-config.yaml",
        "bin/forbid-secret-files.sh",
    ]


# One real path from the tree per glob (the last two are the shapes design 25
# introduces; the tree has no file there yet), and a near-miss for each.
@pytest.mark.parametrize("glob,hit,miss", [
    ("services/backend/tests/**", "services/backend/tests/conftest.py",
     "services/backend/agentplatform/tickets.py"),
    ("services/web/tests/**", "services/web/tests/smoke.spec.ts",
     "services/web/playwright.config.ts"),
    ("services/claude-proxy/tests/**", "services/claude-proxy/tests/test_proxy_quota.py",
     "services/claude-proxy/nginx.conf"),
    ("services/*/test_*.py", "services/runner/test_runner.py",
     "services/runner/runner.py"),
    ("apps/*/backend/test_*.py", "apps/news/backend/test_newsapp.py",
     "apps/news/backend/pyproject.toml"),
    ("apps/*/backend/tests/**", "apps/news/backend/tests/test_gates.py",
     "apps/news/backend/newsapp.py"),
    ("tools/*/test_run.py", "tools/strava/test_run.py",
     "tools/strava/run.py"),
    ("tcms/cases/**", "tcms/cases/relay/dm-reply.md",
     "tcms/README.md"),
])
def test_each_test_glob_hits_a_real_path_and_misses_a_neighbour(glob, hit, miss):
    assert match(glob, hit)
    assert not match(glob, miss)
    assert is_test_path(hit)


@pytest.mark.parametrize("path", [
    "services/web/playwright.config.ts",
    ".github/workflows/ci.yaml",
    "bin/ap-verify",
    "services/backend/pyproject.toml",
    "services/runner/Dockerfile.dev",
    "services/backend/tests",
])
def test_not_test_code(path):
    assert not is_test_path(path)


def test_conftest_under_tests_is_test_code():
    assert is_test_path("services/backend/tests/conftest.py")
    assert is_test_path("services/web/tests/mock-api.ts")


# --- check_policy -----------------------------------------------------------

def ch(path, status="M", old_path=None, additions=0, deletions=0):
    return Change(path, status, old_path, additions, deletions)


def test_empty_change_list_is_ok():
    v = check_policy([], push_path_globs=[], may_delete_tests=False)
    assert v.ok and v.reason is None
    assert v.tests_removed == [] and v.test_lines_removed == 0


def test_empty_globs_allow_any_non_denied_path():
    v = check_policy(
        [ch("services/backend/agentplatform/relay.py", "M", additions=3, deletions=1),
         ch("docs/new.md", "A", additions=10),
         ch("services/web/src/App.tsx", "D", deletions=40)],
        push_path_globs=[], may_delete_tests=False)
    assert v.ok


def test_deny_list_wins_over_everything():
    # The fence would allow it and the flag is set: still refused, by name.
    v = check_policy(
        [ch("services/backend/agentplatform/relay.py"),
         ch(".github/workflows/ci.yaml", "M", additions=1, deletions=1)],
        push_path_globs=[".github/**", "services/**"], may_delete_tests=True)
    assert not v.ok
    assert ".github/workflows/ci.yaml" in v.reason


@pytest.mark.parametrize("path", [
    ".github/CODEOWNERS", ".pre-commit-config.yaml", "bin/forbid-secret-files.sh",
])
def test_every_deny_entry_refuses(path):
    v = check_policy([ch(path, "A", additions=1)], push_path_globs=[], may_delete_tests=False)
    assert not v.ok and path in v.reason


def test_deny_list_covers_the_old_side_of_a_rename():
    v = check_policy(
        [ch("ci/workflow.yaml", "R", old_path=".github/workflows/ci.yaml")],
        push_path_globs=[], may_delete_tests=False)
    assert not v.ok and ".github/workflows/ci.yaml" in v.reason


def test_deny_list_is_checked_before_the_fence():
    # Both paths would be refused; the deny-listed one is named even though
    # the fence miss comes first in the list.
    v = check_policy(
        [ch("services/backend/agentplatform/relay.py"),
         ch(".pre-commit-config.yaml")],
        push_path_globs=["tcms/cases/**"], may_delete_tests=False)
    assert not v.ok and ".pre-commit-config.yaml" in v.reason


def test_non_empty_globs_refuse_the_first_miss_by_name():
    v = check_policy(
        [ch("tcms/cases/a.md", "A", additions=5),
         ch("services/backend/agentplatform/relay.py", "M", additions=1),
         ch("services/backend/agentplatform/tickets.py", "M", additions=1)],
        push_path_globs=["tcms/cases/**", "services/web/tests/**"],
        may_delete_tests=False)
    assert not v.ok
    assert "services/backend/agentplatform/relay.py" in v.reason
    assert "tickets.py" not in v.reason


def test_non_empty_globs_allow_when_every_path_matches_one():
    v = check_policy(
        [ch("tcms/cases/a.md", "A", additions=5),
         ch("services/web/tests/new.spec.ts", "A", additions=30)],
        push_path_globs=["tcms/cases/**", "services/web/tests/**"],
        may_delete_tests=False)
    assert v.ok


def test_fence_covers_the_old_side_of_a_rename():
    v = check_policy(
        [ch("services/web/tests/relay.py", "R", old_path="services/backend/agentplatform/relay.py")],
        push_path_globs=["services/web/tests/**"], may_delete_tests=False)
    assert not v.ok and "services/backend/agentplatform/relay.py" in v.reason


def test_deleted_test_refused_without_the_flag():
    v = check_policy(
        [ch("services/backend/tests/test_relay_api.py", "D", deletions=120)],
        push_path_globs=[], may_delete_tests=False)
    assert not v.ok
    assert "services/backend/tests/test_relay_api.py" in v.reason
    assert v.tests_removed == ["services/backend/tests/test_relay_api.py"]


def test_deleted_test_allowed_with_the_flag_and_still_flagged():
    v = check_policy(
        [ch("services/backend/tests/test_relay_api.py", "D", deletions=120)],
        push_path_globs=[], may_delete_tests=True)
    assert v.ok
    assert v.tests_removed == ["services/backend/tests/test_relay_api.py"]
    assert v.test_lines_removed == 120


def test_rename_away_from_a_test_path_counts_as_removed():
    v = check_policy(
        [ch("services/backend/agentplatform/old_tests.py", "R",
            old_path="services/backend/tests/test_old.py")],
        push_path_globs=[], may_delete_tests=False)
    assert not v.ok
    assert v.tests_removed == ["services/backend/tests/test_old.py"]


def test_rename_within_test_paths_is_not_a_removal():
    v = check_policy(
        [ch("services/backend/tests/test_new.py", "R",
            old_path="services/backend/tests/test_old.py")],
        push_path_globs=[], may_delete_tests=False)
    assert v.ok and v.tests_removed == []


def test_deleting_a_non_test_file_is_not_a_removal():
    v = check_policy(
        [ch("services/web/playwright.config.ts", "D", deletions=30)],
        push_path_globs=[], may_delete_tests=False)
    assert v.ok and v.tests_removed == [] and v.test_lines_removed == 0


def test_net_negative_test_lines_are_flagged_not_refused():
    v = check_policy(
        [ch("services/backend/tests/test_relay_api.py", "M", additions=2, deletions=40),
         ch("services/backend/tests/test_tickets.py", "M", additions=10, deletions=2),
         ch("services/backend/agentplatform/relay.py", "M", additions=0, deletions=500)],
        push_path_globs=[], may_delete_tests=False)
    assert v.ok
    assert v.tests_removed == []
    # Only test paths count, and it is the net across them: 42 - 12.
    assert v.test_lines_removed == 30


def test_net_positive_test_lines_report_zero():
    v = check_policy(
        [ch("services/backend/tests/test_relay_api.py", "M", additions=50, deletions=3)],
        push_path_globs=[], may_delete_tests=False)
    assert v.ok and v.test_lines_removed == 0


@pytest.mark.parametrize("bad", [
    "/etc/passwd", "../x.py", "a/../b.py", "a/./b.py", "a//b.py", "a/b/", "",
    "a\\b.py", "a.py\x00", "a\x00/b.py",
])
def test_unsafe_paths_are_refused_before_any_rule(bad):
    # The flag is on and the fence is open; the path shape alone refuses it.
    v = check_policy([ch(bad, "A", additions=1)], push_path_globs=[], may_delete_tests=True)
    assert not v.ok and v.tests_removed == []


def test_unsafe_old_path_is_refused_too():
    v = check_policy([ch("a.py", "R", old_path="../a.py")],
                     push_path_globs=[], may_delete_tests=True)
    assert not v.ok


def test_unsafe_path_beats_the_deny_list_in_order():
    v = check_policy([ch("/x", "A"), ch(".github/x", "A")],
                     push_path_globs=[], may_delete_tests=False)
    assert not v.ok and "/x" in v.reason and ".github" not in v.reason


def test_copy_is_checked_on_both_sides_like_a_rename():
    denied = check_policy(
        [ch("ci.yaml", "C", old_path=".github/workflows/ci.yaml", additions=10)],
        push_path_globs=[], may_delete_tests=False)
    assert not denied.ok and ".github/workflows/ci.yaml" in denied.reason
    fenced = check_policy(
        [ch("tcms/cases/relay.py", "C", old_path="services/backend/agentplatform/relay.py",
            additions=10)],
        push_path_globs=["tcms/cases/**"], may_delete_tests=False)
    assert not fenced.ok and "services/backend/agentplatform/relay.py" in fenced.reason


def test_copy_never_counts_as_a_test_removal():
    v = check_policy(
        [ch("services/backend/agentplatform/fixture.py", "C",
            old_path="services/backend/tests/conftest.py", additions=30)],
        push_path_globs=[], may_delete_tests=False)
    assert v.ok and v.tests_removed == []


@pytest.mark.parametrize("status", ["R", "C"])
def test_rename_or_copy_without_old_path_is_refused_before_any_rule(status):
    v = check_policy([ch("a.py", status)], push_path_globs=[], may_delete_tests=True)
    assert not v.ok and "old path" in v.reason


def test_unknown_status_is_a_programming_error():
    with pytest.raises(ValueError):
        check_policy([ch("a.py", "T")], push_path_globs=[], may_delete_tests=False)


def test_bad_push_path_glob_is_a_refusal_not_a_crash():
    v = check_policy([ch("a.py", "A")], push_path_globs=["a" * 201],
                     may_delete_tests=False)
    assert not v.ok and "push path glob" in v.reason
    v = check_policy([ch("a.py", "A")], push_path_globs=["**/*.py", ""],
                     may_delete_tests=False)
    assert not v.ok and "push path glob" in v.reason


def test_module_is_pure():
    # The publish route imports this beside the ORM; the policy itself must
    # never need a session, a file or a process to answer.
    with open(testpaths.__file__) as f:
        src = f.read()
    for banned in ("import sqlalchemy", "from agentplatform import db",
                   "from agentplatform.db", "import os", "import subprocess"):
        assert banned not in src
