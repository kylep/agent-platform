import subprocess

import pytest

from agentplatform.gitservice import compute_changes
from agentplatform.tiers import TIER_DIRECT, TIER_PR, classify_tier


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    d = tmp_path / "agents" / "hello-world"
    d.mkdir(parents=True)
    (d / "agent.md").write_text("You are hello-world.\n")
    (d / "manifest.yaml").write_text("description: greet\nconcurrency: 1\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _by_path(changes):
    return {c.path: c for c in changes}


def test_modified_body_is_direct(repo):
    (repo / "agents/hello-world/agent.md").write_text("You are hello-world. Be nice.\n")
    changes = compute_changes(repo)
    assert _by_path(changes)["agents/hello-world/agent.md"].kind == "modified"
    assert classify_tier(changes) == TIER_DIRECT


def test_safe_manifest_field_change_is_direct(repo):
    (repo / "agents/hello-world/manifest.yaml").write_text("description: greet warmly\nconcurrency: 1\n")
    changes = compute_changes(repo)
    c = _by_path(changes)["agents/hello-world/manifest.yaml"]
    assert c.manifest_fields == frozenset({"description"})
    assert classify_tier(changes) == TIER_DIRECT


def test_sensitive_manifest_field_change_is_pr(repo):
    (repo / "agents/hello-world/manifest.yaml").write_text("description: greet\nconcurrency: 5\n")
    changes = compute_changes(repo)
    c = _by_path(changes)["agents/hello-world/manifest.yaml"]
    assert c.manifest_fields == frozenset({"concurrency"})
    assert classify_tier(changes) == TIER_PR


def test_new_agent_is_added_and_pr(repo):
    d = repo / "agents" / "brandnew"
    d.mkdir()
    (d / "agent.md").write_text("You are new.\n")
    (d / "manifest.yaml").write_text("description: new\n")
    changes = compute_changes(repo)
    kinds = {c.path: c.kind for c in changes}
    assert kinds["agents/brandnew/agent.md"] == "added"
    assert classify_tier(changes) == TIER_PR


def test_deleted_file_is_pr(repo):
    (repo / "agents/hello-world/agent.md").unlink()
    changes = compute_changes(repo)
    assert _by_path(changes)["agents/hello-world/agent.md"].kind == "deleted"
    assert classify_tier(changes) == TIER_PR


def test_clean_tree_has_no_changes(repo):
    assert compute_changes(repo) == []
    assert classify_tier(compute_changes(repo)) == TIER_DIRECT
