"""The schema of `tcms/cases/<suite>.yaml` and the one loader for it.

Both the `tcms` tool (`sync_cases`) and the backend's `test_tcms_cases.py`
read the case tree through this module, so the schema has exactly one home.
It runs inside the tool image, where only the stdlib and PyYAML exist: no
`agentplatform` import may ever land here.

A case file is untrusted input in the same sense a tool argument is (an
agent writes it and publishes it as a PR): every string and list is capped,
a ref may not climb out of the checkout, and a file with any error is dropped
whole — the other files still load, and every error is reported as a
`(file, key-or-None, message)` triple so the tool can print them by file.
"""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

LAYERS = ("unit", "integration", "e2e", "manual")
PRIORITIES = ("p0", "p1", "p2", "p3")

KEY_RE = re.compile(r"^[a-z0-9-]+\.[a-z0-9-]+$")
SUITE_RE = re.compile(r"^[a-z0-9-]+$")
# The name group excludes every C0 control: a `\r` in a title would let one
# ref print as two lines in the tool's output.
REF_RE = re.compile(r"^(pytest|playwright):([A-Za-z0-9_./-]+)::([^\x00-\x1f]{1,200})$")
# The tickets block's key shape, anchored — provenance is a list of QA-n keys.
TICKET_RE = re.compile(r"^[A-Z][A-Z0-9]{1,5}-[0-9]+$")

MAX_TITLE = 160
MAX_EXPECTED = 2000
MAX_STEP = 500
MAX_STEPS = 20
MAX_PRECONDITIONS = 20
MAX_REFS = 10
MAX_TAGS = 10
MAX_TAG = 40
MAX_TICKETS = 20
MAX_CASES = 500
MAX_FILE_BYTES = 1024 * 1024

Error = tuple[str, str | None, str]  # (file, key or None, message)


@dataclass(frozen=True)
class Case:
    key: str
    title: str
    layer: str
    priority: str
    preconditions: tuple
    steps: tuple
    expected: str
    automation: tuple
    tags: tuple
    tickets: tuple


@dataclass(frozen=True)
class Suite:
    name: str
    area: str
    path: Path
    cases: tuple


class _Bad(Exception):
    """One validation failure; the message is what the error triple carries."""


def split_ref(ref: str) -> tuple:
    """`kind:path::name` → (kind, path, name). The split is on the FIRST `::`
    so a Playwright title (or a `Class::test_x[param]` node id) may carry its
    own double colons."""
    kind, rest = ref.split(":", 1)
    path, name = rest.split("::", 1)
    return kind, path, name


def _str(d: dict, field: str, cap: int, *, required: bool) -> str:
    v = d.get(field)
    if v is None:
        if required:
            raise _Bad(f"{field} is required")
        return ""
    if not isinstance(v, str) or not v.strip():
        raise _Bad(f"{field} must be a non-empty string")
    if len(v) > cap:
        raise _Bad(f"{field} is longer than {cap} characters")
    return v.strip()


def _strs(d: dict, field: str, cap: int, each: int) -> tuple:
    v = d.get(field)
    if v is None:
        return ()
    if not isinstance(v, list):
        raise _Bad(f"{field} must be a list")
    if len(v) > cap:
        raise _Bad(f"{field} has more than {cap} entries")
    out = []
    for item in v:
        if not isinstance(item, str) or not item.strip():
            raise _Bad(f"{field} entries must be non-empty strings")
        if len(item) > each:
            raise _Bad(f"a {field} entry is longer than {each} characters")
        out.append(item.strip())
    return tuple(out)


def _ref(ref: str) -> str:
    m = REF_RE.match(ref)
    if not m:
        raise _Bad(f"automation ref {ref!r} is not pytest:<path>::<name> "
                   "or playwright:<path>::<title>")
    # The same rule set as the publish policy's `_unsafe`: `REPO_ROOT / path`
    # would silently discard the root for a leading `/`, and an empty, `.` or
    # `..` segment is a path that names nothing in the checkout as written.
    path = m.group(2)
    if path.startswith("/") or any(seg in ("", ".", "..") for seg in path.split("/")):
        raise _Bad(f"automation ref {ref!r} is not a repository path")
    return ref


def _case(raw, suite: str) -> Case:
    if not isinstance(raw, dict):
        raise _Bad("a case must be a mapping")
    key = _str(raw, "key", 120, required=True)
    if not KEY_RE.match(key):
        raise _Bad(f"key {key!r} must match {KEY_RE.pattern}")
    if not key.startswith(suite + "."):
        raise _Bad(f"key {key} must start with '{suite}.'")
    layer = _str(raw, "layer", 20, required=True)
    if layer not in LAYERS:
        raise _Bad(f"layer {layer!r} is not one of {', '.join(LAYERS)}")
    priority = _str(raw, "priority", 4, required=True)
    if priority not in PRIORITIES:
        raise _Bad(f"priority {priority!r} is not one of p0..p3")
    automation = tuple(_ref(r) for r in _strs(raw, "automation", MAX_REFS, 400))
    if (layer == "manual") != (not automation):
        raise _Bad("a case is manual exactly when it has no automation")
    tickets = _strs(raw, "tickets", MAX_TICKETS, 20)
    for t in tickets:
        if not TICKET_RE.match(t):
            raise _Bad(f"ticket {t!r} is not a ticket key")
    return Case(
        key=key,
        title=_str(raw, "title", MAX_TITLE, required=True),
        layer=layer,
        priority=priority,
        preconditions=_strs(raw, "preconditions", MAX_PRECONDITIONS, MAX_STEP),
        steps=_strs(raw, "steps", MAX_STEPS, MAX_STEP),
        expected=_str(raw, "expected", MAX_EXPECTED, required=True),
        automation=automation,
        tags=_strs(raw, "tags", MAX_TAGS, MAX_TAG),
        tickets=tickets,
    )


def _suite(path: Path) -> tuple:
    """(Suite, errors) for one file. A file-level fault is one error with no
    key; a case-level fault names its key (or its index when it has none).
    The suite comes back only when the error list is empty."""
    name = path.stem
    if path.stat().st_size > MAX_FILE_BYTES:
        return None, [(path.name, None, f"file is larger than {MAX_FILE_BYTES} bytes")]
    # safe_load shares an alias with its anchor by identity, so an anchor
    # bomb costs nothing to parse; a duplicate mapping key keeps the LAST
    # value, which is accepted — the file is reviewed in a PR, not trusted.
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        return None, [(path.name, None, f"not valid YAML: {str(e).splitlines()[0]}")]
    if not isinstance(doc, dict):
        return None, [(path.name, None, "a suite file must be a mapping")]
    try:
        if not SUITE_RE.match(name):
            raise _Bad(f"filename stem {name!r} must match {SUITE_RE.pattern}")
        suite = _str(doc, "suite", 60, required=True)
        if suite != name:
            raise _Bad(f"suite {suite!r} must equal the filename stem {name!r}")
        area = _str(doc, "area", 60, required=True)
    except _Bad as e:
        return None, [(path.name, None, str(e))]
    raw_cases = doc.get("cases")
    if not isinstance(raw_cases, list):
        return None, [(path.name, None, "cases must be a list")]
    if len(raw_cases) > MAX_CASES:
        return None, [(path.name, None, f"more than {MAX_CASES} cases")]
    errors, cases, seen = [], [], set()
    for i, raw in enumerate(raw_cases):
        key = raw.get("key") if isinstance(raw, dict) else None
        label = key if isinstance(key, str) else f"#{i}"
        try:
            case = _case(raw, name)
        except _Bad as e:
            errors.append((path.name, label, str(e)))
            continue
        if case.key in seen:
            errors.append((path.name, case.key, f"duplicate key {case.key}"))
            continue
        seen.add(case.key)
        cases.append(case)
    if errors:
        return None, errors
    return Suite(name=name, area=area, path=path, cases=tuple(cases)), []


def load_suites(cases_dir) -> tuple:
    """Every `*.yaml` under `cases_dir`, sorted by name → (suites, errors).
    Keys are unique across the whole tree by construction: each must start
    with its own file's stem, and stems are unique in a directory."""
    suites, errors = [], []
    for path in sorted(Path(cases_dir).glob("*.yaml")):
        suite, errs = _suite(path)
        if errs:
            errors.extend(errs)
        else:
            suites.append(suite)
    return suites, errors
