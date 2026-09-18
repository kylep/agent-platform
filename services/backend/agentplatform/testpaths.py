"""The path policy a publish must pass before a byte reaches GitHub.

Three things live here and nowhere else: the one glob matcher every path
fence uses, the platform's one definition of "test code", and the ordered
rules the publish route runs over a bundle's changed paths. The module is
pure on purpose — no session, no file, no process — so the route can answer
"would this be refused" from a list it already holds, and the tests can pin
the rules with tuples. Paths are checked as strings; file type (a symlink in
the bundle) is the caller's job, and the publish route rejects those before
it builds a `Change`.

The matcher follows the fnmatch semantics GitHub's rulesets use: `**` is
zero or more whole segments, `*` and `?` never cross a `/`, matching is
case-sensitive and anchored to the whole path. That is the point of having
one matcher rather than `fnmatch`/`pathlib`: the platform's fence and the
ruleset backstop on the repository have to agree about what a test path is,
and they can only agree if they read a glob the same way.

It is deliberately not a regex. An agent's `push_path_globs` is untrusted
input, and a backtracking engine handed `*a*a*a…` or a run of `**` segments
goes exponential against a path that does not match — a publish could stall
the API. Each pattern is normalised once into a tuple of segments and
matched by the two-pointer wildcard walk, which is O(path × pattern) at
worst, and the pattern itself is bounded (`MAX_PATTERN_CHARS`,
`MAX_PATTERN_SEGMENTS`) so even that product stays small.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import NamedTuple

# The CI gate and the secret-leak prevention stay human-authored, for every
# agent, always. Nothing an agent holds can lift this.
PUBLISH_DENY_GLOBS = [
    ".github/**",
    ".pre-commit-config.yaml",
    "bin/forbid-secret-files.sh",
]

# "Test code", inventoried from the tree. A conftest.py is test code because
# it lives under tests/; CI yaml, playwright.config.ts, pyproject.toml and
# bin/ap-verify are not. The last two entries are design 25's shapes.
TEST_PATH_GLOBS = [
    "services/backend/tests/**",
    "services/web/tests/**",
    "services/claude-proxy/tests/**",
    "services/*/test_*.py",
    "apps/*/backend/test_*.py",
    "apps/*/backend/tests/**",
    "tools/*/test_run.py",
    "tcms/cases/**",
]

MAX_PATTERN_CHARS = 200
MAX_PATTERN_SEGMENTS = 16

# `R` and `C` carry an old side; a copy is never a removal.
STATUSES = frozenset("AMDRC")
TWO_SIDED = frozenset("RC")


def _normalise_segment(seg: str) -> str:
    # Inside a segment a `**` is just `*`; a run of `*` is one `*`, and any
    # `?` in that run moves ahead of it (`*?*` is `?*`: one-or-more). The
    # walk below is polynomial either way; the normal form is so equal
    # patterns compare equal and the cache holds one entry for them.
    out = []
    i = 0
    while i < len(seg):
        c = seg[i]
        if c in "*?":
            j = i
            qs = 0
            star = False
            while j < len(seg) and seg[j] in "*?":
                if seg[j] == "?":
                    qs += 1
                else:
                    star = True
                j += 1
            out.append("?" * qs + ("*" if star else ""))
            i = j
        else:
            out.append(c)
            i += 1
    return "".join(out)


def normalise(pattern: str) -> str:
    return "/".join(_compile(pattern))


@lru_cache(maxsize=1024)
def _compile(pattern: str) -> tuple[str, ...]:
    if not pattern:
        raise ValueError("empty glob")
    if len(pattern) > MAX_PATTERN_CHARS:
        raise ValueError(f"glob longer than {MAX_PATTERN_CHARS} characters")
    raw = pattern.split("/")
    if len(raw) > MAX_PATTERN_SEGMENTS:
        raise ValueError(f"glob has more than {MAX_PATTERN_SEGMENTS} segments")
    segments: list[str] = []
    for seg in raw:
        if seg == "**":
            # Consecutive `**` segments mean the same as one.
            if segments and segments[-1] == "**":
                continue
            segments.append("**")
        else:
            segments.append(_normalise_segment(seg))
    return tuple(segments)


def _segment_matches(pat: str, s: str) -> bool:
    # The classic wildcard walk: on a mismatch, back up to the most recent
    # `*` and let it eat one more character. Each (pattern, path) position
    # is revisited at most once per star, so the cost is bounded by the
    # product of the two lengths, never by the number of stars.
    p = i = 0
    star = -1
    mark = 0
    while i < len(s):
        if p < len(pat) and (pat[p] == "?" or pat[p] == s[i]):
            p += 1
            i += 1
        elif p < len(pat) and pat[p] == "*":
            star = p
            mark = i
            p += 1
        elif star != -1:
            p = star + 1
            mark += 1
            i = mark
        else:
            return False
    while p < len(pat) and pat[p] == "*":
        p += 1
    return p == len(pat)


def _segments_match(pat: tuple[str, ...], segs: list[str]) -> bool:
    # The same walk one level up, with `**` as the star over whole segments.
    p = i = 0
    star = -1
    mark = 0
    while i < len(segs):
        if p < len(pat) and pat[p] != "**" and _segment_matches(pat[p], segs[i]):
            p += 1
            i += 1
        elif p < len(pat) and pat[p] == "**":
            star = p
            mark = i
            p += 1
        elif star != -1:
            p = star + 1
            mark += 1
            i = mark
        else:
            return False
    while p < len(pat) and pat[p] == "**":
        p += 1
    return p == len(pat)


def match(pattern: str, path: str) -> bool:
    """True when `path` matches the glob. Raises ValueError for an empty or
    oversized pattern — a data error the write path should have refused."""
    pat = _compile(pattern)
    segs = path.split("/")
    # A path never has an empty segment, and `tests/**` means something
    # *under* tests: a trailing `**` needs at least one segment to take.
    if "" in segs:
        return False
    if pat[-1] == "**" and len(pat) > 1:
        pat = pat + ("*",)
    return _segments_match(pat, segs)


def is_test_path(path: str) -> bool:
    return any(match(g, path) for g in TEST_PATH_GLOBS)


class Change(NamedTuple):
    path: str
    status: str  # A | M | D | R | C
    old_path: str | None
    additions: int
    deletions: int


@dataclass(frozen=True)
class PolicyVerdict:
    ok: bool
    reason: str | None = None
    # Test files deleted or renamed out of the test tree — the red chip on
    # the thread card, populated whether or not the flag let them through.
    tests_removed: list[str] = field(default_factory=list)
    # Net test lines lost across test paths (never below zero). Flagged in
    # the PR body and the card, never a refusal.
    test_lines_removed: int = 0

    @classmethod
    def refused(cls, reason: str, **flags) -> PolicyVerdict:
        return cls(False, reason, **flags)


def _unsafe(path: str) -> bool:
    # The route lists paths from its own clone, so git never hands it one of
    # these; the check is the belt under the braces, before any rule. A NUL
    # or a backslash is refused outright rather than reasoned about.
    if not path or path.startswith("/") or "\x00" in path or "\\" in path:
        return True
    return any(seg in ("", ".", "..") for seg in path.split("/"))


def _sides(change: Change) -> list[str]:
    # A rename or copy touches both names: the fence and the deny list must
    # see the side being taken from as well as the one being created.
    if change.status in TWO_SIDED:
        return [change.old_path or "", change.path]
    return [change.path]


def _glob_error(globs: list[str]) -> str | None:
    for g in globs:
        try:
            _compile(g)
        except ValueError as e:
            return f"{g[:40]!r} is not a usable push path glob ({e})"
    return None


def check_policy(changes: list[Change], *, push_path_globs: list[str],
                 may_delete_tests: bool) -> PolicyVerdict:
    for c in changes:
        if c.status not in STATUSES:
            raise ValueError(f"unknown change status {c.status!r} for {c.path!r}")
        if c.status in TWO_SIDED and not c.old_path:
            return PolicyVerdict.refused(f"{c.path} is a rename or copy without an old path")
        for p in _sides(c):
            if _unsafe(p):
                return PolicyVerdict.refused(f"{p} is not a repository path")
    # The agent's globs are its own row's data, not the platform's: a bad
    # one is a refusal that names it, never an exception out of the route.
    if (err := _glob_error(push_path_globs)) is not None:
        return PolicyVerdict.refused(err)

    for c in changes:
        for p in _sides(c):
            if any(match(g, p) for g in PUBLISH_DENY_GLOBS):
                return PolicyVerdict.refused(
                    f"{p} is platform-owned and no agent may change it")

    if push_path_globs:
        for c in changes:
            for p in _sides(c):
                if not any(match(g, p) for g in push_path_globs):
                    return PolicyVerdict.refused(f"{p} is outside its push paths")

    tests_removed = []
    net = 0
    for c in changes:
        if c.status == "D" and is_test_path(c.path):
            tests_removed.append(c.path)
        elif (c.status == "R" and is_test_path(c.old_path)
              and not is_test_path(c.path)):
            tests_removed.append(c.old_path)
        if any(is_test_path(p) for p in _sides(c)):
            net += c.deletions - c.additions
    flags = {"tests_removed": tests_removed, "test_lines_removed": max(0, net)}

    if tests_removed and not may_delete_tests:
        return PolicyVerdict.refused(
            f"{tests_removed[0]} is a test and the agent may not delete tests", **flags)
    return PolicyVerdict(True, **flags)
