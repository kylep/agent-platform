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

from agentplatform.tiers import FileChange


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
