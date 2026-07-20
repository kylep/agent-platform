"""Git-side of the self-hosting loop.

`compute_changes` inspects a workspace checkout (where an edit — by the
platform-coder agent or a deterministic quick-edit — has already been
written) and returns the structured change set that `tiers.classify_tier`
consumes. The actual commit / branch / push / PR steps live in
`GitWriter` and require a repo write credential (supplied as a secret,
like claude-credentials); they are intentionally separated so tier
classification is testable without any GitHub access.
"""
import subprocess
from pathlib import Path

import yaml

from agentplatform.tiers import TIER_DIRECT, FileChange, classify_tier


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

    def __init__(self, remote_url: str, *, default_branch: str = "main",
                 author_name: str = "platform-coder",
                 author_email: str = "platform-coder@agent-platform.local"):
        self.remote_url = remote_url
        self.default_branch = default_branch
        self.author_name = author_name
        self.author_email = author_email

    def clone(self, dest: Path) -> Path:
        dest = Path(dest)
        subprocess.run(["git", "clone", self.remote_url, str(dest)],
                       check=True, capture_output=True, text=True)
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
        _git(Path(repo), "push", "origin", f"HEAD:{branch}")


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
