"""Git-side of the self-hosting loop.

`compute_changes` inspects a workspace checkout (where an edit — by the
platform-coder agent or a deterministic quick-edit — has already been
written) and returns the structured change set that `tiers.classify_tier`
consumes. The actual commit / branch / push / PR steps live in
`GitWriter` and require a repo write credential (supplied as a secret,
like claude-credentials); they are intentionally separated so tier
classification is testable without any GitHub access.
"""
import os
import shlex
import stat
import subprocess
import tempfile
from pathlib import Path

import yaml

from agentplatform.tiers import TIER_DIRECT, FileChange, classify_tier

# GitHub's published SSH host keys (from https://api.github.com/meta -> ssh_keys).
# Pinned so deploy-key pushes verify the host instead of trusting on first use;
# refresh if GitHub rotates them (rare — last in 2023).
GITHUB_KNOWN_HOSTS = (
    "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl\n"
    "github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uRgg6CLrbo5wAdT/y6v0mKV0U2w0WZ2YB/++Tpockg=\n"
    "github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDbC7qtbW8gIkhL7aGCsOr/C56SJMy/BCZfxd1nWzAOxSDPgVsmerOBYfNqltV9/hWCqBywINIR+5dIg6JTJ72pcEpEjcYgXkE2YEFXV1JHnsKgbLWNlhScqb2UmyRkQyytRLtL+38TGxkxCflmO+5Z8CSSNY7GidjMIZ7Q4zMjA2n1nGrlTDkzwDCsw+wqFPGQA179cnfGWOWRVruj16z6XyvxvjJwbz0wQZ75XK5tKSb7FNyeIEs4TT4jk+S4dhPeAUC5y+bDYirYgM4GC7uEnztnZyaVWQ7B381AK4Qdrwt51ZqExKbQpTUNn+EjqoTwvqNj4kqx5QUCI0ThS/YkOxJCXmPUWZbhjpCg56i+2aB6CmK2JGhn57K5mj0MNdBXA4/WnwH6XoPWJzK5Nyu2zB3nAZp+S5hpQs+p1vN1/wsjk=\n"
)


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


def _manifest_field_changes(repo: Path, path: str) -> frozenset[str]:
    """Keys whose values differ between HEAD and the working manifest."""
    try:
        old = yaml.safe_load(_git(repo, "show", f"HEAD:{path}")) or {}
    except subprocess.CalledProcessError:
        old = {}
    new = yaml.safe_load((repo / path).read_text()) or {}
    return frozenset(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def compute_changes(repo: Path) -> list[FileChange]:
    """Structured diff of the working tree vs HEAD (staged, unstaged, and
    untracked), ready for tier classification."""
    repo = Path(repo)
    out = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    changes: list[FileChange] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        xy, path = line[:2], line[3:]
        kind = _kind_from_status(xy)
        fields: frozenset[str] = frozenset()
        if path.endswith("manifest.yaml") and kind == "modified":
            fields = _manifest_field_changes(repo, path)
        changes.append(FileChange(path=path, kind=kind, manifest_fields=fields))
    return changes


class GitWriter:
    """Performs the git-level writes for the self-hosting loop against a
    workspace clone. Clone/commit/branch/push work with any git remote
    (a local bare repo in tests, GitHub over HTTPS in prod). Opening the
    actual pull request is a separate GitHub-API step (see PR client) and is
    the only part that needs a repo write credential."""

    def __init__(self, remote_url: str, *, token: str | None = None,
                 ssh_key_path: str | None = None,
                 default_branch: str = "main",
                 author_name: str = "platform-coder",
                 author_email: str = "platform-coder@agent-platform.local"):
        self.remote_url = remote_url
        # Auth is supplied to git out-of-band so no secret appears in a URL,
        # argv, or subprocess error (which would leak it to logs):
        #   - ssh_key_path: a deploy-key private key, used via GIT_SSH_COMMAND
        #     (preferred: repo-scoped);
        #   - token: an https token, used via GIT_ASKPASS (remote_url then
        #     carries only the `x-access-token@` username).
        self.token = token.strip() if token else None
        self.ssh_key_path = ssh_key_path
        self.default_branch = default_branch
        self.author_name = author_name
        self.author_email = author_email
        self._askpass: str | None = None

    def _auth_env(self) -> dict:
        if self.ssh_key_path:
            kh = Path(self.ssh_key_path).parent / "known_hosts"
            if not kh.exists():
                kh.write_text(GITHUB_KNOWN_HOSTS)
            return {**os.environ, "GIT_SSH_COMMAND":
                    f"ssh -i {shlex.quote(self.ssh_key_path)} -o IdentitiesOnly=yes "
                    f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={shlex.quote(str(kh))}"}
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

    def push(self, repo: Path, branch: str) -> None:
        subprocess.run(["git", "-C", str(Path(repo)), "push", "origin", f"HEAD:{branch}"],
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
    """Orchestrates a self-edit end to end: clone, apply the edit, classify the
    tier, then either commit straight to the default branch (tier 1) or push a
    branch and open a pull request (tier 2). The PR client is optional so the
    git path is exercisable without any GitHub access."""

    def __init__(self, writer: "GitWriter", pr_client=None):
        self.writer = writer
        self.pr_client = pr_client

    def apply(self, workspace: Path, files: dict[str, str | None], *,
              message: str, branch: str, pr_title: str | None = None,
              pr_body: str = "") -> dict:
        repo = self.writer.clone(workspace)
        _write_files(repo, files)
        changes = compute_changes(repo)
        paths = [c.path for c in changes]
        if not changes:
            # The edit matches what's already committed — nothing to do (a
            # bare `git commit` would fail with "nothing to commit").
            return {"tier": 0, "branch": None, "sha": None, "changes": [], "pr": None}
        if classify_tier(changes) == TIER_DIRECT:
            sha = self.writer.commit(repo, message)
            self.writer.push(repo, self.writer.default_branch)
            return {"tier": 1, "branch": self.writer.default_branch, "sha": sha,
                    "changes": paths, "pr": None}
        self.writer.create_branch(repo, branch)
        sha = self.writer.commit(repo, message)
        self.writer.push(repo, branch)
        pr = None
        if self.pr_client is not None:
            pr = self.pr_client.open_pull_request(
                head=branch, base=self.writer.default_branch,
                title=pr_title or message, body=pr_body)
        return {"tier": 2, "branch": branch, "sha": sha, "changes": paths, "pr": pr}
