"""Git-side of the self-hosting loop.

`compute_changes` inspects a workspace checkout (where an edit — by the
platform-coder agent or a deterministic quick-edit — has already been written)
and reports what it touched. The actual commit / branch / push / PR steps live
in `GitWriter` and require a repo write credential (supplied as a secret, like
claude-credentials); they are intentionally separated so the change computation
is testable without any GitHub access.

Every edit that reaches here is now CAPABILITY-AS-CODE — a skill, a secret
declaration, a tool — and every one of them goes through a pull request. The
tier classifier that used to wave through an agent's own prompt edit went out
with the `agents/` tree (docs/design/15): definitions are rows, edited through
the API and logged in `agent_versions`, so nothing left in this path is a
low-risk in-place edit.
"""
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileChange:
    path: str                                   # repo-relative, e.g. skills/git/SKILL.md
    kind: str                                   # "added" | "modified" | "deleted"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True).stdout


def _kind_from_status(xy: str) -> str:
    """Map a porcelain XY status pair to added/modified/deleted."""
    if xy == "??" or "A" in xy:
        return "added"
    if "D" in xy:
        return "deleted"
    return "modified"


def compute_changes(repo: Path) -> list[FileChange]:
    """What the working tree changed vs HEAD (staged, unstaged, and untracked).
    Drives the commit message's file list and the "did anything change at all"
    check."""
    repo = Path(repo)
    out = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    changes: list[FileChange] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        xy, path = line[:2], line[3:]
        changes.append(FileChange(path=path, kind=_kind_from_status(xy)))
    return changes


class GitWriter:
    """Performs the git-level writes for the self-hosting loop against a
    workspace clone. Clone/commit/branch/push work with any git remote
    (a local bare repo in tests, GitHub over HTTPS in prod). Opening the
    actual pull request is a separate GitHub-API step (see PR client) and is
    the only part that needs a repo write credential."""

    def __init__(self, remote_url: str, *, token: str | None = None,
                 default_branch: str = "main",
                 author_name: str = "platform-coder",
                 author_email: str = "platform-coder@agent-platform.local"):
        self.remote_url = remote_url
        # Auth is supplied to git out-of-band so no secret appears in a URL,
        # argv, or subprocess error (which would leak it to logs): the token is
        # an https credential used via GIT_ASKPASS, and remote_url then carries
        # only the `x-access-token@` username.
        self.token = token.strip() if token else None
        self.default_branch = default_branch
        self.author_name = author_name
        self.author_email = author_email
        self._askpass: str | None = None

    def _auth_env(self) -> dict:
        if not self.token:
            return dict(os.environ)
        if self._askpass is None:
            fd, path = tempfile.mkstemp(prefix="ap-askpass-")
            os.write(fd, b'#!/bin/sh\nprintf "%s" "$AP_GIT_TOKEN"\n')
            os.close(fd)
            os.chmod(path, stat.S_IRWXU)
            self._askpass = path
        return {**os.environ, "AP_GIT_TOKEN": self.token,
                "GIT_ASKPASS": self._askpass, "GIT_TERMINAL_PROMPT": "0"}

    def clone(self, dest: Path) -> Path:
        dest = Path(dest)
        subprocess.run(["git", "clone", self.remote_url, str(dest)],
                       check=True, capture_output=True, text=True, env=self._auth_env())
        return dest

    def create_branch(self, repo: Path, branch: str) -> None:
        _git(Path(repo), "checkout", "-b", branch)

    def commit(self, repo: Path, message: str) -> str:
        """Stage every change in the workspace and commit it; returns the SHA."""
        repo = Path(repo)
        _git(repo, "add", "-A")
        _git(repo, "-c", f"user.name={self.author_name}",
             "-c", f"user.email={self.author_email}", "commit", "-m", message)
        return _git(repo, "rev-parse", "HEAD").strip()

    def push(self, repo: Path, branch: str, *, force: bool = False) -> None:
        # `+` forces: a deterministic per-agent branch is overwritten so there
        # is only ever one open PR per agent.
        refspec = f"{'+' if force else ''}HEAD:{branch}"
        subprocess.run(["git", "-C", str(Path(repo)), "push", "origin", refspec],
                       check=True, capture_output=True, text=True, env=self._auth_env())


def _write_files(repo: Path, files: dict[str, str | None]) -> None:
    """Apply an edit set to the workspace: str content writes/overwrites the
    file (creating parents); None deletes it."""
    for rel, content in files.items():
        target = repo / rel
        if content is None:
            target.unlink(missing_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)


class EditService:
    """Orchestrates a self-edit end to end: clone, apply the edit, push a
    branch and open a pull request. The PR client is optional so the git path
    is exercisable without any GitHub access.

    There is no direct-to-main path any more. Everything this service still
    edits is capability-as-code, which is reviewable by definition, and the one
    thing that used to qualify for a silent commit — an agent's own prompt —
    is a row now."""

    def __init__(self, writer: "GitWriter", pr_client=None):
        self.writer = writer
        self.pr_client = pr_client

    def apply(self, workspace: Path, files: dict[str, str | None], *,
              message: str, branch: str, pr_title: str | None = None,
              pr_body: str = "") -> dict:
        """Returns the standard edit result. `tier` survives as the wire's
        outcome code — 0 = the edit was a no-op, 2 = it is a pending change —
        because that is what the web reads to tell a save from a nothing."""
        repo = self.writer.clone(workspace)
        _write_files(repo, files)
        changes = compute_changes(repo)
        paths = [c.path for c in changes]
        if not changes:
            # The edit matches what's already committed — nothing to do (a
            # bare `git commit` would fail with "nothing to commit").
            return {"tier": 0, "branch": None, "sha": None, "changes": [], "pr": None}
        self.writer.create_branch(repo, branch)
        sha = self.writer.commit(repo, message)
        self.writer.push(repo, branch, force=True)  # deterministic branch → overwrite
        pr = None
        if self.pr_client is not None:
            import urllib.error
            try:
                pr = self.pr_client.open_pull_request(
                    head=branch, base=self.writer.default_branch,
                    title=pr_title or message, body=pr_body)
            except urllib.error.HTTPError as e:
                if e.code != 422:  # 422 = a PR already exists for this branch
                    raise
                pr = self.pr_client.find_open_pull_request(branch)  # updated by the force-push
        return {"tier": 2, "branch": branch, "sha": sha, "changes": paths, "pr": pr}
