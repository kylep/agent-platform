"""Pure parsers for the three report files `ap-verify` leaves behind: pytest's
`--junitxml` (xunit2), Playwright's `--reporter=json`, and coverage.py's
Cobertura `coverage.xml`. Bytes in, rows out; nothing here touches a database
or the environment.

Every file is untrusted input — an agent produced it and an artifact carried
it — so the guards live here rather than in the tool: a file over 8 MiB is
refused before it is looked at, a message is cut at 4 KiB, a name loses its
control characters, and an XML document is refused when its bytes contain
`<!DOCTYPE` or `<!ENTITY` BEFORE the stdlib parser sees it. That byte check
is the whole DTD defence: `xml.etree.ElementTree` expands internal entities
(the billion-laughs shape), and refusing the declaration up front keeps the
tool image free of a defusedxml dependency.

A ref is `pytest:<path>::<name>` or `playwright:<path>::<title>` exactly as
`cases.py` defines it, with `<path>` relative to the reporter's own root
(pytest's rootdir, Playwright's testDir): `tests/test_config.py`, not
`services/backend/tests/test_config.py`. Neither reporter writes the checkout
path, so the tool resolves the root against the checkout it runs in (see
`run.resolve_root`) and rewrites the path before the join to `cases`.
"""
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace

from cases import split_ref

MAX_INPUT = 8 * 1024 * 1024
MAX_MESSAGE = 4096
MAX_NAME = 200
MAX_PACKAGES = 2000
# describe() blocks nest a handful deep in any real spec; a document nested
# thousands deep is a 200 KB recursion bomb, not a report.
MAX_SUITE_DEPTH = 64

_DTD_RE = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
# Playwright's own JUnit reporter names its classname after the spec file;
# pytest's is a dotted module path and can never look like one.
_SPEC_FILE_RE = re.compile(r"(/|\.(spec|test)\.[cm]?[jt]sx?$|\.[cm]?[jt]sx?$)")

STATUSES = ("pass", "fail", "skip", "flaky", "error")
_PLAYWRIGHT_STATUS = {"expected": "pass", "unexpected": "fail", "flaky": "flaky",
                      "skipped": "skip"}


class Refused(ValueError):
    """The file is not something this tool will ingest; the message says why
    and is safe to show the model."""


@dataclass(frozen=True)
class Result:
    kind: str           # pytest | playwright
    path: str           # reporter-root-relative until the tool resolves it
    name: str
    status: str
    duration_ms: int
    message: str

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.path}::{self.name}"

    def with_root(self, root: str) -> "Result":
        return replace(self, path=f"{root}/{self.path}" if root else self.path)


@dataclass(frozen=True)
class CoverageRow:
    package: str
    lines_covered: int
    lines_total: int
    branch_rate: float | None


def _cap(text, limit: int = MAX_MESSAGE) -> str:
    """Bytes-bounded, NUL-free text (Postgres rejects NUL in `text`)."""
    s = (text or "").replace("\x00", "")
    b = s.encode("utf-8")
    if len(b) <= limit:
        return s
    return b[:limit].decode("utf-8", errors="ignore")


def _name(text) -> str:
    s = _CONTROL_RE.sub(" ", str(text or "")).strip()
    return s[:MAX_NAME]


def _size_ok(data: bytes) -> None:
    if len(data) > MAX_INPUT:
        raise Refused(f"file is larger than 8 MiB ({len(data)} bytes)")


def _xml_root(data: bytes):
    _size_ok(data)
    m = _DTD_RE.search(data)
    if m:
        raise Refused(f"XML document declares a {m.group(1).decode().upper()}; "
                      "entity declarations are refused")
    try:
        return ET.fromstring(data)
    except ET.ParseError as e:
        raise Refused(f"not well-formed XML ({e})") from None


def _strip(data: bytes) -> bytes:
    head = data[:4096]
    if head.startswith(b"\xef\xbb\xbf"):
        head = head[3:]
    return head.lstrip()


def sniff(name: str, data: bytes) -> str | None:
    """`junit` | `playwright` | `cobertura` | None, from the bytes alone: the
    broker stores an XML claim as application/octet-stream and the name is
    whatever the uploader chose, so neither is evidence. `name` is here for
    the error the caller prints."""
    if len(data) > MAX_INPUT:
        return None
    head = _strip(data)
    if head.startswith(b"<"):
        # A DTD is the parser's refusal, not the sniffer's: the kind is still
        # what the root element says, so the error names the right parser.
        if re.search(rb"<testsuites?[\s>/]", head):
            return "junit"
        if re.search(rb"<coverage[\s>/]", head):
            return "cobertura"
        return None
    if head.startswith(b"{"):
        try:
            doc = json.loads(data)
        except (ValueError, UnicodeDecodeError):
            return None
        if isinstance(doc, dict) and isinstance(doc.get("suites"), list) \
                and isinstance(doc.get("config"), dict):
            return "playwright"
    return None


def parse(kind: str, data: bytes):
    return {"junit": parse_junit, "playwright": parse_playwright_json,
            "cobertura": parse_cobertura}[kind](data)


# --- JUnit -------------------------------------------------------------------

def parse_junit_report(data: bytes) -> tuple:
    """(results, testcases skipped for having no classname). pytest's xunit2:
    `classname` is the dotted module path, `name` the test id (parameters in
    brackets), children `failure` / `error` / `skipped` carry the outcome."""
    root = _xml_root(data)
    results, skipped = [], 0
    for tc in root.iter("testcase"):
        classname = (tc.get("classname") or "").strip()
        name = _name(tc.get("name"))
        if not classname or not name:
            skipped += 1
            continue
        if _SPEC_FILE_RE.search(classname):
            raise Refused("this is Playwright's JUnit reporter output; record the "
                          "JSON report (--reporter=json) instead so results are "
                          "not counted twice")
        path = classname.replace(".", "/") + ".py"
        status, message = "pass", ""
        for tag, st in (("error", "error"), ("failure", "fail"), ("skipped", "skip")):
            child = tc.find(tag)
            if child is not None:
                status = st
                message = _cap("\n".join(
                    p for p in ((child.get("message") or "").strip(),
                                (child.text or "").strip()) if p))
                break
        try:
            ms = round(float(tc.get("time") or 0) * 1000)
        except ValueError:
            ms = 0
        results.append(Result("pytest", path, name, status, max(ms, 0), message))
    return results, skipped


def parse_junit(data: bytes) -> list:
    return parse_junit_report(data)[0]


def skipped_testcases(data: bytes) -> int:
    return parse_junit_report(data)[1]


# --- Playwright --------------------------------------------------------------

def parse_playwright_json(data: bytes) -> list:
    """Playwright's JSON reporter: `suites[]` nest (the file suite, then each
    `describe`), `specs[].tests[]` carry `status` (expected | unexpected |
    flaky | skipped) and `results[]` per attempt. The title path below the
    file suite is joined with Playwright's own ` › `, so a bare test's ref is
    `playwright:<file>::<title>`."""
    _size_ok(data)
    try:
        doc = json.loads(data)
    except (ValueError, UnicodeDecodeError) as e:
        raise Refused(f"not valid JSON ({str(e)[:100]})") from None
    if not isinstance(doc, dict) or not isinstance(doc.get("suites"), list):
        raise Refused("not a Playwright JSON report (no suites list)")
    results = []

    def walk(suite: dict, titles: tuple, file: str) -> None:
        if len(titles) > MAX_SUITE_DEPTH:
            raise Refused(f"suites nested deeper than {MAX_SUITE_DEPTH}")
        file = str(suite.get("file") or file)
        for spec in suite.get("specs") or []:
            if not isinstance(spec, dict):
                continue
            spec_file = str(spec.get("file") or file).strip()
            title = " › ".join(t for t in (*titles, _name(spec.get("title"))) if t)
            if not spec_file or not title:
                continue
            for test in spec.get("tests") or []:
                if not isinstance(test, dict):
                    continue
                attempts = [a for a in (test.get("results") or []) if isinstance(a, dict)]
                status = _PLAYWRIGHT_STATUS.get(str(test.get("status")), "error")
                ms = 0
                errors = []
                for a in attempts:
                    try:
                        ms += round(float(a.get("duration") or 0))
                    except (TypeError, ValueError):
                        pass
                    for err in a.get("errors") or []:
                        if isinstance(err, dict) and err.get("message"):
                            errors.append(str(err["message"]))
                message = _cap("\n".join(errors)) if status in ("fail", "flaky", "error") else ""
                results.append(Result("playwright", spec_file, _name(title), status,
                                      max(ms, 0), message))
        # The top suite is the file (its title is the file name); every
        # suite below it is a describe block and belongs in the title path.
        for child in suite.get("suites") or []:
            if isinstance(child, dict):
                walk(child, titles + (_name(child.get("title")),), file)

    for top in doc["suites"]:
        if isinstance(top, dict):
            walk(top, (), str(top.get("file") or ""))
    return results


# --- Cobertura ---------------------------------------------------------------

def parse_cobertura(data: bytes) -> list:
    """coverage.py's Cobertura: one row per `package`, with the line counts
    summed from each class's `<line hits>` elements (the package's own
    `line-rate` is a ratio, not a count). coverage.py names the top-level
    package "." and puts the measured directory under `<sources>`, so packages
    are reported under that directory's name: `agentplatform`,
    `agentplatform.api`. `branch_rate` is None unless branches were measured."""
    root = _xml_root(data)
    if root.tag != "coverage":
        raise Refused("not a Cobertura coverage report (root is not <coverage>)")
    sources = [s.text.strip() for s in root.iter("source") if s.text and s.text.strip()]
    prefix = sources[0].rstrip("/").rsplit("/", 1)[-1] if len(sources) == 1 else ""
    try:
        branches = int(float(root.get("branches-valid") or 0))
    except ValueError:
        branches = 0
    rows = []
    for pkg in root.iter("package"):
        name = _name(pkg.get("name")) or "."
        if name == ".":
            label = prefix or "."
        else:
            label = f"{prefix}.{name}" if prefix else name
        total = covered = 0
        for line in pkg.iter("line"):
            total += 1
            if (line.get("hits") or "0") != "0":
                covered += 1
        rate = None
        if branches > 0:
            try:
                rate = float(pkg.get("branch-rate") or 0)
            except ValueError:
                rate = None
        rows.append(CoverageRow(label, covered, total, rate))
        if len(rows) >= MAX_PACKAGES:
            break
    return rows


# --- layers ------------------------------------------------------------------

_UNIT_RE = re.compile(r"^tools/[^/]+/test_run\.py$")


def layer_of(ref: str) -> str:
    """The pyramid layer a ref's path implies, so a run draws with no case
    linked: the web suite is e2e, a tool's own test file is unit, everything
    else is integration (a linked case that says `unit` overrides this)."""
    _, path, _ = split_ref(ref)
    if path.startswith("services/web/tests/"):
        return "e2e"
    if _UNIT_RE.match(path):
        return "unit"
    return "integration"
