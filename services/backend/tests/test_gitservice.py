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


@pytest.fixture
def bare_remote(tmp_path):
    """A bare origin with one commit on main, plus a seeded working clone."""
    from agentplatform.gitservice import GitWriter
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-q", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    git(seed, "config", "user.email", "s@s"); git(seed, "config", "user.name", "s")
    (seed / "agents").mkdir()
    (seed / "agents" / "x.txt").write_text("hi\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "init")
    git(seed, "branch", "-M", "main"); git(seed, "push", "-q", "origin", "main")
    return bare


def _remote_branches(bare):
    out = subprocess.run(["git", "-C", str(bare), "branch", "--format=%(refname:short)"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_gitwriter_tier1_commit_and_push_to_main(bare_remote, tmp_path):
    from agentplatform.gitservice import GitWriter
    w = GitWriter(str(bare_remote))
    repo = w.clone(tmp_path / "ws")
    (repo / "agents" / "x.txt").write_text("edited\n")
    sha = w.commit(repo, "tier-1 edit")
    w.push(repo, "main")
    # The bare remote's main now points at our new commit.
    remote_head = subprocess.run(["git", "-C", str(bare_remote), "rev-parse", "main"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    assert remote_head == sha


def test_gitwriter_tier2_branch_push(bare_remote, tmp_path):
    from agentplatform.gitservice import GitWriter
    w = GitWriter(str(bare_remote))
    repo = w.clone(tmp_path / "ws2")
    w.create_branch(repo, "coder/edit-1")
    (repo / "agents" / "x.txt").write_text("proposed\n")
    w.commit(repo, "tier-2 proposal")
    w.push(repo, "coder/edit-1")
    assert "coder/edit-1" in _remote_branches(bare_remote)
    # main is untouched on the remote.
    assert "main" in _remote_branches(bare_remote)
