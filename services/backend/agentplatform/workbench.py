"""The Workbench's publish door (docs/design/24): the ONE place a dev run's
code leaves the platform, and the API owns it.

The runner enforces nothing. It bundles what the model committed and POSTs
the bundle with a head sha, a base sha, the verifier's record and the model's
notes — and every one of those is untrusted here. This module re-derives
everything from the bundle in its own fresh clone of the real remote: that
the bundle carries exactly the run's branch, that its head descends from the
branch's current tip (or from `main` for a new branch), what the changed
paths are, that none is a symlink or a traversal, and that the path policy
(`testpaths.check_policy`) lets them through. Only then does it push — never
forced — open or update the pull request, move the ticket, post the thread
card and publish the audit envelope, in that order, and a later step never
runs when an earlier one refused.

Two pieces of runner-written text reach a human through here. `verify` is
shape-checked and its tails capped, because the runner's `verify.json` sat in
a workspace the model could write to. `notes_md` is the model's own words:
HTML comments come out (a hidden `<!-- -->` is the documented injection
vector, and the platform's summary marker is one), it is capped, and it goes
in a section labelled agent-authored, under the evidence the runner captured.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from agentplatform.api.gitedit import _push_url
from agentplatform.db import Conversation, Ticket
from agentplatform.events import TOPIC_WORKBENCH_EVENTS
from agentplatform.gitservice import GitWriter
from agentplatform.prsummarizer import MARKER
from agentplatform.relay import SYSTEM_AUTHOR
from agentplatform.relay_store import (channel_by_name, outbound_for_message,
                                       post_relay_message, publish_relay_message)
from agentplatform.testpaths import Change, check_policy, is_test_path
from agentplatform.ticket_store import TicketRuleError, move_ticket
from agentplatform.tickets import KEY_RE, REASON_LIMIT, TITLE_LIMIT, one_line

log = logging.getLogger("workbench")

# The one rule for a branch name, checked at the API before the name is used
# anywhere (the runner re-checks it where the name becomes argv).
BRANCH_RE = re.compile(r"^(coder|qa)/[a-z0-9][a-z0-9-]{0,40}$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
MAX_FILES = 200
NOTES_MAX_BYTES = 32 * 1024
# A suite's tail in the PR body: the last lines the verifier kept, bounded
# again here because the record came out of the workspace.
TAIL_MAX_LINES, TAIL_MAX_CHARS = 40, 4096
MAX_SUITES = 50
GIT_TIMEOUT = 600
LS_REMOTE_TIMEOUT = 60
# Where a publish with no ticket announces itself.
ENG_CHANNEL = "eng"
# A closed comment, or an unclosed one to the end: GitHub hides everything
# after an unclosed `<!--`, which is the same trick with the bracket left off.
_HTML_COMMENT_RE = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)
# `@handle` notifies a person and `closes #12` closes an issue on merge, both
# under the platform's identity — so in the agent's notes they become code.
# Not after a word character (`kyle@pericak.com`) and not already in code.
_GITHUB_TOKEN_RE = re.compile(r"(?<![\w`])(@[\w-]+|#\d+)(?!`)")
_AGENT_HEADER_RE = re.compile(r"^\*\*Platform:\*\* agent `([A-Za-z0-9][A-Za-z0-9_-]{0,63})`")


class PublishRefused(Exception):
    """A publish that must not land. `status` is the HTTP answer (409 for the
    remote disagreeing, 413 over a cap, 422 for policy, 502 for git itself)
    and `reason` the one sentence the runner, the thread and the envelope all
    get — never a command's output."""

    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


@dataclass
class PublishResult:
    status: int                       # 201 landed, 200 already on the remote
    branch: str
    pr: dict | None
    paths: list[str]
    tests_removed: list[str]
    ticket_state: str | None
    auto_merge: bool
    verify_ok: bool | None
    warnings: list[str] = field(default_factory=list)

    def body(self) -> dict:
        return {"branch": self.branch, "pr": self.pr, "paths": self.paths,
                "tests_removed": self.tests_removed, "ticket_state": self.ticket_state,
                "auto_merge": self.auto_merge, "verify_ok": self.verify_ok,
                "warnings": self.warnings}


@dataclass
class _Landed:
    """What the git-and-GitHub thread hands back."""
    changes: list[Change]
    commits: int
    already: bool
    pr: dict | None
    node_id: str | None
    tests_removed: list[str]
    test_lines_removed: int


# --- naming -------------------------------------------------------------------------

def branch_for(run, ticket, *, prefix: str = "coder") -> str:
    """The run's branch: the platform names it, the runner only receives it.
    `prefix` is design 25's seam (a QA run publishes under `qa/`)."""
    name = f"{prefix}/{ticket.key.lower()}" if ticket is not None else f"{prefix}/run-{run.id[:12]}"
    if not BRANCH_RE.match(name):
        raise ValueError(f"not a publishable branch name: {name!r}")
    return name


def ticket_key_of(branch: str) -> str | None:
    """`coder/eng-12` → `ENG-12`; None for a branch that is not a ticket's."""
    _, _, tail = branch.partition("/")
    m = KEY_RE.fullmatch(tail.upper())
    return m.group(0) if m else None


def workbench_view(run, ticket, existing: bool, open_pr: dict | None, *,
                   base: str = "main", remote_url: str | None = None) -> dict:
    """The `GET /api/runs/{id}/workbench` body — the facts the runner's prepare
    step and its `<workbench>` block are built from, and nothing free-text."""
    return {"branch": branch_for(run, ticket), "base": base, "remote_url": remote_url or None,
            "ticket_key": ticket.key if ticket is not None else None,
            "existing": bool(existing),
            "open_pr": ({"number": int(open_pr["number"]), "url": str(open_pr["html_url"])}
                        if open_pr else None)}


def remote_branch_exists(remote_url: str, branch: str) -> bool:
    """`git ls-remote --heads` with no credential — the Workbench keeps no
    table, the remote is the record. A remote that cannot be asked anonymously
    reads as "no branch"; the publish's own ancestry check is the guard."""
    if not remote_url or not BRANCH_RE.match(branch):
        return False
    env = {k: v for k, v in os.environ.items() if k != "GIT_ASKPASS"}
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        r = subprocess.run(["git", "ls-remote", "--heads", "--", remote_url,
                            f"refs/heads/{branch}"],
                           capture_output=True, text=True, env=env, timeout=LS_REMOTE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if r.returncode != 0:
        log.warning("ls-remote for %s failed (exit %s)", branch, r.returncode)
        return False
    return bool(r.stdout.strip())


# --- the untrusted inputs -----------------------------------------------------------

def clean_notes(notes_md) -> str:
    """The model's PR notes as the body may carry them: comments out, the
    summary marker's line out, capped. Nothing here is interpreted."""
    if not isinstance(notes_md, str):
        return ""
    raw = notes_md.encode("utf-8", "replace")
    if len(raw) > NOTES_MAX_BYTES:
        notes_md = raw[:NOTES_MAX_BYTES].decode("utf-8", "replace")
    text = _HTML_COMMENT_RE.sub("", notes_md)
    lines = [ln for ln in text.splitlines() if not ln.lstrip().startswith(MARKER)]
    return _GITHUB_TOKEN_RE.sub(r"`\1`", "\n".join(lines)).strip()


def _tail(text: str) -> str:
    lines = text.strip().splitlines()[-TAIL_MAX_LINES:]
    return "\n".join(lines)[-TAIL_MAX_CHARS:]


def normalise_verify(raw) -> dict:
    """The verifier's record as the publish will read it: `{ran, ok, suites,
    reason}`. `ran` False with `ok` None is "nothing to run" (the runner sent
    null); `ran` False with `ok` False is a verify that should have run and
    did not (a timeout, a crash, a record in the wrong shape). A field in the
    wrong type is dropped, never coerced — `ok: "yes"` is not a pass."""
    if raw is None:
        return {"ran": False, "ok": None, "suites": [], "reason": "no record"}
    if not isinstance(raw, dict):
        return {"ran": False, "ok": False, "suites": [], "reason": "malformed record"}
    ok = raw.get("ok") if isinstance(raw.get("ok"), bool) else False
    error = raw.get("error")
    error = one_line(error, REASON_LIMIT) if isinstance(error, str) and error.strip() else None
    suites = []
    items = raw.get("suites") if isinstance(raw.get("suites"), list) else []
    for item in items[:MAX_SUITES]:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        code = item.get("exit")
        code = code if isinstance(code, int) and not isinstance(code, bool) else None
        seconds = item.get("seconds")
        seconds = (round(float(seconds), 1)
                   if isinstance(seconds, (int, float)) and not isinstance(seconds, bool)
                   else None)
        skipped = item.get("skipped_reason")
        skipped = one_line(skipped, REASON_LIMIT) if isinstance(skipped, str) and skipped else None
        tail = item.get("tail")
        suites.append({"name": one_line(name, 80), "exit": code, "seconds": seconds,
                       "skipped_reason": skipped,
                       "tail": _tail(tail) if isinstance(tail, str) else ""})
    if not suites and not ok:
        return {"ran": False, "ok": False, "suites": [],
                "reason": error or ("malformed record" if not isinstance(raw.get("ok"), bool)
                                    else "no suites ran")}
    return {"ran": True, "ok": ok, "suites": suites, "reason": error}


def failing_suites(verify: dict) -> list[dict]:
    return [s for s in verify["suites"] if s["exit"] not in (0, None)]


def verify_failure(verify: dict) -> str | None:
    """The ticket's blocked reason, or None when verify passed or had nothing
    to run."""
    if verify["ok"] is not False:
        return None
    failed = failing_suites(verify)
    return f"verify failed: {failed[0]['name'] if failed else verify['reason'] or 'unknown'}"


# --- the pull request ----------------------------------------------------------------

def pr_header(agent: str, run_id: str, ticket_key: str | None, branch: str, commits: int) -> str:
    """The platform's first line of every Workbench PR — what `/api/pull-requests`
    parses the agent back out of."""
    parts = [f"**Platform:** agent `{agent}`", f"run [`{run_id[:12]}`](/runs/{run_id})"]
    if ticket_key:
        parts.append(f"ticket [{ticket_key}](/tickets/{ticket_key})")
    parts += [f"branch `{branch}`", f"{commits} commit{'' if commits == 1 else 's'}"]
    return " · ".join(parts)


def agent_from_body(body: str | None) -> str | None:
    m = _AGENT_HEADER_RE.match((body or "").lstrip().split("\n", 1)[0])
    return m.group(1) if m else None


def pr_title(agent: str, ticket, run, *, verify_ok: bool | None) -> str:
    what = (f"{ticket.key} {one_line(ticket.title, TITLE_LIMIT)}" if ticket is not None
            else f"run {run.id[:12]}")
    title = f"{agent}: {what}"
    return f"[verify ✗] {title}" if verify_ok is False else title


def _cell(path: str) -> str:
    return "`" + one_line(path, 200).replace("|", "\\|").replace("`", "'") + "`"


def _files_table(changes: list[Change], tests_removed: list[str]) -> str:
    rows = ["| status | path | +/− |", "|---|---|---|"]
    removed = set(tests_removed)
    for c in changes:
        path = (f"{_cell(c.old_path)} → {_cell(c.path)}" if c.status in "RC" and c.old_path
                else _cell(c.path))
        flag = " ⚠️ test" if (c.path in removed or (c.old_path or "") in removed) else ""
        rows.append(f"| {c.status} | {path} | +{c.additions} −{c.deletions}{flag} |")
    return "\n".join(rows)


def _verify_section(verify: dict) -> str:
    if not verify["ran"]:
        return f"### Verification\n\nverify did not run: {verify['reason']}"
    rows = ["### Verification (captured by the runner)", "",
            "| suite | exit | seconds | result |", "|---|---|---|---|"]
    details = []
    for s in verify["suites"]:
        if s["exit"] is None:
            result = f"skipped: {s['skipped_reason']}" if s["skipped_reason"] else "did not run"
        else:
            result = "✓" if s["exit"] == 0 else "✗"
        code = "–" if s["exit"] is None else str(s["exit"])
        secs = "–" if s["seconds"] is None else str(s["seconds"])
        rows.append(f"| {s['name']} | {code} | {secs} | {result} |")
        if s["exit"] not in (0, None) and s["tail"]:
            fence_safe = s["tail"].replace("```", "` ` `")
            details.append(f"<details><summary>{s['name']} — last lines</summary>\n\n"
                           f"```\n{fence_safe}\n```\n</details>")
    return "\n".join(rows + ([""] + details if details else []))


def pr_body(*, agent: str, run, ticket, branch: str, commits: int, changes: list[Change],
            tests_removed: list[str], test_lines_removed: int, verify: dict,
            notes: str) -> str:
    parts = [pr_header(agent, run.id, ticket.key if ticket is not None else None, branch, commits),
             "", "### Files", "", _files_table(changes, tests_removed)]
    if test_lines_removed:
        parts += ["", f"⚠️ net {test_lines_removed} test line(s) removed."]
    parts += ["", _verify_section(verify), "", f"### {agent}'s notes (agent-authored)", "",
              notes or "_none_"]
    return "\n".join(parts)


# --- git ------------------------------------------------------------------------------

def _git(repo: Path | None, env: dict, *args: str, ok=(0,)) -> subprocess.CompletedProcess:
    """One git call. A non-zero exit outside `ok` is a 502 naming the verb —
    never the output, which is where a remote's page (or a credential) lands."""
    cmd = ["git", *(["-C", str(repo)] if repo else []), *args]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise PublishRefused(502, f"git {args[0]} timed out") from None
    if r.returncode not in ok:
        raise PublishRefused(502, f"git {args[0]} failed (exit {r.returncode})")
    return r


def _parse_name_status(out: str) -> list[tuple[str, str | None, str]]:
    """`--name-status -z` → [(status letter, old path or None, path)]."""
    toks = out.split("\0")
    rows, i = [], 0
    while i < len(toks) and toks[i]:
        status = toks[i][0]
        if status in "RC":
            rows.append((status, toks[i + 1], toks[i + 2]))
            i += 3
        else:
            rows.append((status, None, toks[i + 1]))
            i += 2
    return rows


def _parse_numstat(out: str) -> dict[str, tuple[int, int]]:
    """`--numstat -z` → {path: (additions, deletions)}; a binary file counts
    as 0/0, and a rename's record names the new path."""
    toks = out.split("\0")
    counts, i = {}, 0
    while i < len(toks) and toks[i]:
        adds, dels, path = toks[i].split("\t", 2)
        if path == "":                      # rename: old and new follow as two tokens
            path = toks[i + 2]
            i += 3
        else:
            i += 1
        counts[path] = (int(adds) if adds.isdigit() else 0, int(dels) if dels.isdigit() else 0)
    return counts


def _changes(repo: Path, env: dict, merge_base: str, head: str) -> list[Change]:
    status = _git(repo, env, "diff", "--name-status", "-M", "-z", merge_base, head).stdout
    numstat = _git(repo, env, "diff", "--numstat", "-M", "-z", merge_base, head).stdout
    counts = _parse_numstat(numstat)
    out = []
    for st, old, path in _parse_name_status(status):
        if st not in "AMDRC":
            # `T` (type change) and `U` never come out of a merge-base diff of
            # ordinary commits; anything else is refused rather than guessed at.
            raise PublishRefused(422, f"{path} has an unsupported change type {st}")
        adds, dels = counts.get(path, (0, 0))
        out.append(Change(path=path, status=st, old_path=old, additions=adds, deletions=dels))
    return out


def _symlinks(repo: Path, env: dict, head: str) -> set[str]:
    out = _git(repo, env, "ls-tree", "-r", "-z", head).stdout
    links = set()
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        if meta.startswith("120000 "):
            links.add(path)
    return links


def _writer(settings, github_app_token: str | None) -> GitWriter:
    """The App token when there is one (the production path), else the
    no-auth path a local remote takes in tests. Reuses `GitWriter`: the
    credential travels only through its askpass, never a URL or argv."""
    remote = settings.git_remote_url
    if not remote:
        raise PublishRefused(409, "the workbench is not configured (no git remote)")
    if github_app_token:
        return GitWriter(_push_url(remote, github_app_token), token=github_app_token,
                         default_branch=settings.default_branch)
    if remote.startswith("https://"):
        raise PublishRefused(409, "no git credential configured (github-app)")
    return GitWriter(remote, default_branch=settings.default_branch)


def _land(settings, github_app_token, gh_client, *, agent: str, run, ticket, branch: str,
          bundle: bytes, head_sha: str, agent_def, verify: dict, notes: str) -> _Landed:
    """Everything that touches git or GitHub, in one worker thread: clone,
    check the bundle, derive the changes, run the policy, push, PR."""
    base = settings.default_branch
    writer = _writer(settings, github_app_token)
    env = writer._auth_env()
    with tempfile.TemporaryDirectory(prefix="ap-publish-") as tmp:
        repo = Path(tmp) / "repo"
        try:
            writer.clone(repo)
        except subprocess.CalledProcessError as e:
            raise PublishRefused(502, f"git clone failed (exit {e.returncode})") from None
        bundle_path = Path(tmp) / "publish.bundle"
        bundle_path.write_bytes(bundle)

        heads = _git(repo, env, "bundle", "list-heads", str(bundle_path), ok=(0, 1, 128))
        if heads.returncode != 0:
            raise PublishRefused(422, "the upload is not a git bundle")
        listed = [ln.split() for ln in heads.stdout.splitlines() if ln.strip()]
        if len(listed) != 1 or len(listed[0]) != 2 or listed[0][1] != f"refs/heads/{branch}":
            raise PublishRefused(422, f"the bundle must carry exactly one branch, "
                                      f"refs/heads/{branch}")
        if listed[0][0] != head_sha:
            raise PublishRefused(422, "head_sha does not name the bundle's head")
        if _git(repo, env, "bundle", "verify", str(bundle_path), ok=(0, 1)).returncode != 0:
            raise PublishRefused(422, "the bundle's base commits are not on the remote")
        _git(repo, env, "fetch", "--no-tags", "--", str(bundle_path),
             f"refs/heads/{branch}:refs/bundle/head")
        head = "refs/bundle/head"

        remote_tip = _git(repo, env, "rev-parse", "--verify", "-q",
                          f"refs/remotes/origin/{branch}", ok=(0, 1)).stdout.strip()
        if (remote_tip and remote_tip != head_sha
                and _git(repo, env, "merge-base", "--is-ancestor", f"origin/{branch}", head,
                         ok=(0, 1)).returncode != 0):
            raise PublishRefused(409, f"branch moved: {branch} has commits this run "
                                      "did not start from — rebase next run")
        if _git(repo, env, "merge-base", "--is-ancestor", f"origin/{base}", head,
                ok=(0, 1)).returncode != 0:
            raise PublishRefused(409, f"{head_sha[:12]} does not descend from {base}")
        merge_base = _git(repo, env, "merge-base", f"origin/{base}", head).stdout.strip()
        commits = int(_git(repo, env, "rev-list", "--count", f"{merge_base}..{head}").stdout
                      .strip() or 0)

        changes = _changes(repo, env, merge_base, head)
        if len(changes) > MAX_FILES:
            raise PublishRefused(422, f"{len(changes)} files changed; a publish may change "
                                      f"at most {MAX_FILES}")
        if not changes:
            raise PublishRefused(422, "the bundle changes nothing against " + base)
        links = _symlinks(repo, env, head)
        for c in changes:
            if c.status != "D" and c.path in links:
                raise PublishRefused(422, f"{c.path} is a symlink")
        verdict = check_policy(changes, push_path_globs=list(agent_def.push_path_globs or []),
                               may_delete_tests=bool(agent_def.may_delete_tests))
        if not verdict.ok:
            raise PublishRefused(422, verdict.reason)

        if remote_tip == head_sha:
            # The same head again (a retried POST): nothing to push, the PR is
            # whatever is open for the branch.
            return _Landed(changes, commits, True, gh_client.find_open_pull_request(branch),
                           None, verdict.tests_removed, verdict.test_lines_removed)

        # No `+`: a branch that moved between the clone and here is refused,
        # never overwritten.
        r = subprocess.run(["git", "-C", str(repo), "push", "origin",
                            f"{head}:refs/heads/{branch}"],
                           capture_output=True, text=True, env=env, timeout=GIT_TIMEOUT)
        if r.returncode != 0:
            raise PublishRefused(409, f"push refused by the remote: branch moved — "
                                      f"rebase next run (git push exit {r.returncode})")

        title = pr_title(agent, ticket, run, verify_ok=verify["ok"])
        body = pr_body(agent=agent, run=run, ticket=ticket, branch=branch, commits=commits,
                       changes=changes, tests_removed=verdict.tests_removed,
                       test_lines_removed=verdict.test_lines_removed, verify=verify,
                       notes=notes)
        pr = gh_client.find_open_pull_request(branch)
        if pr is not None:
            pr = gh_client.update_pull_request(pr["number"], title=title, body=body) or pr
        else:
            import urllib.error
            try:
                pr = gh_client.open_pull_request(head=branch, base=base, title=title, body=body)
            except urllib.error.HTTPError as e:
                if e.code != 422:           # 422: a PR for the branch appeared meanwhile
                    raise PublishRefused(502, f"GitHub refused the pull request ({e.code})") \
                        from None
                pr = gh_client.find_open_pull_request(branch)
                if pr is None:
                    raise PublishRefused(502, "GitHub refused the pull request (422)") from None
        return _Landed(changes, commits, False, pr, pr.get("node_id"), verdict.tests_removed,
                       verdict.test_lines_removed)


# --- the record: ticket, card, envelope ---------------------------------------------

def _pr_ref(pr: dict | None) -> dict | None:
    return {"number": int(pr["number"]), "url": str(pr.get("html_url") or pr.get("url") or "")} \
        if pr else None


def _verify_phrase(verify: dict) -> str:
    if not verify["ran"]:
        return "verify did not run" if verify["ok"] is None else f"verify ✗ ({verify['reason']})"
    if verify["ok"]:
        passed = [s["name"] for s in verify["suites"] if s["exit"] == 0]
        return "verify " + " ".join(f"✓ {n}" for n in passed) if passed \
            else "verify ✓ (nothing to run)"
    failed = failing_suites(verify)
    if failed:
        return "verify ✗ " + " ".join(f"{s['name']} (exit {s['exit']})" for s in failed)
    return f"verify ✗ ({verify['reason'] or 'unknown'})"


def card_body(agent: str, branch: str, pr: dict | None, files: int, verify: dict,
              tests_removed: list[str], warnings: list[str]) -> str:
    icon = "🔀" if verify["ok"] is not False else "⚠️"
    parts = [f"{icon} {agent} published {branch} → PR #{pr['number'] if pr else '?'}",
             f"{files} file{'' if files == 1 else 's'}", _verify_phrase(verify)]
    if tests_removed:
        parts.append("removes tests: " + ", ".join(one_line(p, 120) for p in tests_removed[:5]))
    parts += [f"⚠️ {w}" for w in warnings]
    return " · ".join(parts)


def _card(*, run, agent, branch, pr, files, tests_removed, verify, refused_reason,
          warnings) -> dict:
    return {"type": "publish", "pr": pr["number"] if pr else None,
            "url": pr["url"] if pr else None, "branch": branch, "files": files,
            "tests_removed": list(tests_removed), "verify_ok": verify["ok"],
            "refused_reason": refused_reason, "run_id": run.id, "agent": agent,
            "warnings": list(warnings)}


async def _post_card(session_factory, producer, *, run, ticket, body: str, card: dict) -> str | None:
    """The card in the ticket's thread, or in #eng for a run with no ticket.
    Returns a warning when there is nowhere to post — a missing #eng is not a
    failed publish."""
    async with session_factory() as s:
        conv, reply_to = None, None
        if ticket is not None:
            conv = await s.get(Conversation, ticket.channel_id)
            reply_to = ticket.root_message_id
        else:
            conv = await channel_by_name(s, ENG_CHANNEL)
        if conv is None:
            return f"no #{ENG_CHANNEL} channel; the publish card was not posted"
        msg = await post_relay_message(s, conv, author=SYSTEM_AUTHOR, body=body, kind="event",
                                       card=card, reply_to=reply_to, mentions=[],
                                       run_id=run.id)
        await s.commit()
        outbound = await outbound_for_message(s, conv, msg)
    await publish_relay_message(producer, conv, msg, outbound=outbound)
    return None


async def _publish_event(producer, *, event: str, run, agent: str, ticket, branch: str,
                         pr: dict | None, changes: list[Change], tests_removed: list[str],
                         verify: dict | None, reason: str | None) -> None:
    """`workbench.events`, best-effort and post-commit: the audit that
    `platform.tool.audit`'s args hash cannot give — a publish needs its file
    list on the record."""
    if producer is None:
        return
    data = {"event": event, "agent": agent, "run_id": run.id,
            "ticket_key": ticket.key if ticket is not None else None, "branch": branch,
            "pr": pr,
            "paths": [{"path": c.path, "status": c.status, "additions": c.additions,
                       "deletions": c.deletions,
                       "test": is_test_path(c.path) or bool(c.old_path and is_test_path(c.old_path))}
                      for c in changes],
            "tests_removed": list(tests_removed),
            "verify": ({"ok": verify["ok"], "suites": [{"name": s["name"], "exit": s["exit"],
                                                        "seconds": s["seconds"]}
                                                       for s in verify["suites"]]}
                       if verify and verify["ran"] else None),
            "reason": reason}
    try:
        await producer.publish(TOPIC_WORKBENCH_EVENTS, run.id, data, type="workbench.event")
    except Exception:
        log.warning("workbench.events publish failed for run %s", run.id, exc_info=True)


async def _move(session_factory, producer, *, run, ticket, agent: str, verify: dict) -> tuple[str | None, str | None]:
    """The ticket after the publish: `review`, or `blocked` naming the failing
    suite. A move the board refuses (already there) is a warning, never a
    failed publish — the code is on the remote by now."""
    if ticket is None:
        return None, None
    reason = verify_failure(verify)
    to_state = "blocked" if reason else "review"
    async with session_factory() as s:
        row = await s.get(Ticket, ticket.id)
        state = str(row.state)
        try:
            await move_ticket(s, producer, row, actor=f"agent:{agent}", to_state=to_state,
                              reason=reason, run=run)
        except TicketRuleError as e:
            # The row is expired by the rollback; the state it had is the
            # state it keeps.
            await s.rollback()
            return state, f"ticket not moved: {one_line(str(e), REASON_LIMIT)}"
        return str(row.state), None


# One publish at a time per run. A retried POST racing its first attempt (the
# runner retries a dropped connection) must see the first's result — the same
# head already on the remote, a 200 — rather than push and announce it twice.
# Keyed lazily and dropped when the last waiter leaves, so the dict is as long
# as the number of publishes in flight.
_locks: dict[str, list] = {}


class _serialised:
    def __init__(self, run_id: str):
        self.run_id = run_id

    async def __aenter__(self):
        entry = _locks.setdefault(self.run_id, [asyncio.Lock(), 0])
        entry[1] += 1
        self.entry = entry
        await entry[0].acquire()

    async def __aexit__(self, *exc):
        self.entry[0].release()
        self.entry[1] -= 1
        if self.entry[1] == 0:
            _locks.pop(self.run_id, None)


async def publish(session_factory, producer, settings, github_app_token, gh_client, *,
                  run, agent_def, bundle: bytes, head_sha: str, base_sha: str, verify,
                  notes_md) -> PublishResult:
    """The publish, start to finish, one at a time per run. Raises
    `PublishRefused` — after the ⛔ card and the `refused` envelope — when
    nothing may land."""
    async with _serialised(run.id):
        return await _publish(session_factory, producer, settings, github_app_token,
                              gh_client, run=run, agent_def=agent_def, bundle=bundle,
                              head_sha=head_sha, base_sha=base_sha, verify=verify,
                              notes_md=notes_md)


async def _publish(session_factory, producer, settings, github_app_token, gh_client, *,
                   run, agent_def, bundle: bytes, head_sha: str, base_sha: str, verify,
                   notes_md) -> PublishResult:
    agent = run.agent
    async with session_factory() as s:
        ticket = await s.get(Ticket, run.ticket_id) if run.ticket_id else None
    branch = branch_for(run, ticket)
    verify_rec = normalise_verify(verify)
    notes = clean_notes(notes_md)
    try:
        if not (isinstance(head_sha, str) and SHA_RE.match(head_sha)
                and isinstance(base_sha, str) and SHA_RE.match(base_sha)):
            raise PublishRefused(422, "head_sha and base_sha must be 40-hex commit ids")
        landed = await asyncio.to_thread(
            _land, settings, github_app_token, gh_client, agent=agent, run=run, ticket=ticket,
            branch=branch, bundle=bundle, head_sha=head_sha, agent_def=agent_def,
            verify=verify_rec, notes=notes)
    except PublishRefused as e:
        card = _card(run=run, agent=agent, branch=branch, pr=None, files=0, tests_removed=[],
                     verify=verify_rec, refused_reason=e.reason, warnings=[])
        await _post_card(session_factory, producer, run=run, ticket=ticket,
                         body=f"⛔ publish refused for {agent}: {e.reason}", card=card)
        await _publish_event(producer, event="refused", run=run, agent=agent, ticket=ticket,
                             branch=branch, pr=None, changes=[], tests_removed=[],
                             verify=verify_rec, reason=e.reason)
        raise

    pr = _pr_ref(landed.pr)
    paths = [c.path for c in landed.changes]
    if landed.already:
        state = None
        if ticket is not None:
            async with session_factory() as s:
                state = str((await s.get(Ticket, ticket.id)).state)
        return PublishResult(200, branch, pr, paths, landed.tests_removed, state, False,
                             verify_rec["ok"], ["already published: this head is the remote tip"])

    warnings: list[str] = []
    auto_merge = False
    if agent_def.push_path_globs and landed.node_id:
        try:
            await asyncio.to_thread(gh_client.enable_auto_merge, landed.node_id)
            auto_merge = True
        except Exception as e:
            warnings.append(f"auto-merge could not be enabled: {one_line(str(e), REASON_LIMIT)}")

    state, warn = await _move(session_factory, producer, run=run, ticket=ticket, agent=agent,
                              verify=verify_rec)
    if warn:
        warnings.append(warn)
    card = _card(run=run, agent=agent, branch=branch, pr=pr, files=len(paths),
                 tests_removed=landed.tests_removed, verify=verify_rec, refused_reason=None,
                 warnings=warnings)
    warn = await _post_card(session_factory, producer, run=run, ticket=ticket,
                            body=card_body(agent, branch, pr, len(paths), verify_rec,
                                           landed.tests_removed, warnings),
                            card=card)
    if warn:
        warnings.append(warn)
    await _publish_event(producer, event="published" if verify_rec["ok"] is not False
                         else "verify_failed",
                         run=run, agent=agent, ticket=ticket, branch=branch, pr=pr,
                         changes=landed.changes, tests_removed=landed.tests_removed,
                         verify=verify_rec, reason=verify_failure(verify_rec))
    return PublishResult(201, branch, pr, paths, landed.tests_removed, state, auto_merge,
                         verify_rec["ok"], warnings)
