import subprocess

import pytest

from agentplatform.gitservice import compute_changes


def git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A checkout holding the blocks the self-edit path still writes: skills,
    secret declarations, tools. `agents/` is gone (docs/design/15)."""
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "t@t")
    git(tmp_path, "config", "user.name", "t")
    d = tmp_path / "skills" / "git"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: git\n---\nUse git.\n")
    (tmp_path / "secrets" / "linear-api-key").mkdir(parents=True)
    (tmp_path / "secrets" / "linear-api-key" / "secret.yaml").write_text(
        "name: linear-api-key\n")
    git(tmp_path, "add", "-A")
    git(tmp_path, "commit", "-qm", "init")
    return tmp_path


def _by_path(changes):
    return {c.path: c for c in changes}


def test_an_edited_file_is_modified(repo):
    (repo / "skills/git/SKILL.md").write_text("---\nname: git\n---\nUse git well.\n")
    assert _by_path(compute_changes(repo))["skills/git/SKILL.md"].kind == "modified"


def test_a_new_file_is_added_even_untracked(repo):
    """Untracked files count: a wizard scaffolding a brand-new skill writes
    files git has never seen, and `--untracked-files=all` is what makes them
    show up as changes instead of an empty diff."""
    d = repo / "skills" / "brandnew"
    d.mkdir()
    (d / "SKILL.md").write_text("---\nname: brandnew\n---\nNew.\n")
    assert _by_path(compute_changes(repo))["skills/brandnew/SKILL.md"].kind == "added"


def test_a_removed_file_is_deleted(repo):
    (repo / "skills/git/SKILL.md").unlink()
    assert _by_path(compute_changes(repo))["skills/git/SKILL.md"].kind == "deleted"


def test_a_clean_tree_has_no_changes(repo):
    """The empty case is load-bearing: it is what EditService turns into the
    no-op result instead of a `git commit` that fails with nothing to commit."""
    assert compute_changes(repo) == []


@pytest.fixture
def bare_remote(tmp_path):
    """A bare origin with one commit on main, plus a seeded working clone."""
    from agentplatform.gitservice import GitWriter
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", "-q", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    git(seed, "config", "user.email", "s@s"); git(seed, "config", "user.name", "s")
    (seed / "skills" / "git").mkdir(parents=True)
    (seed / "skills" / "git" / "SKILL.md").write_text("---\nname: git\n---\nUse git.\n")
    (seed / "secrets" / "linear-api-key").mkdir(parents=True)
    (seed / "secrets" / "linear-api-key" / "secret.yaml").write_text("name: linear-api-key\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "init")
    git(seed, "branch", "-M", "main"); git(seed, "push", "-q", "origin", "main")
    return bare


def _remote_branches(bare):
    out = subprocess.run(["git", "-C", str(bare), "branch", "--format=%(refname:short)"],
                         capture_output=True, text=True, check=True).stdout
    return set(out.split())


def test_gitwriter_commits_and_pushes_a_branch_to_the_remote(bare_remote, tmp_path):
    """GitWriter's primitives are separable from EditService's policy, so this
    exercises the plumbing directly: clone, edit, commit, push."""
    from agentplatform.gitservice import GitWriter
    w = GitWriter(str(bare_remote))
    repo = w.clone(tmp_path / "ws")
    (repo / "skills" / "git" / "SKILL.md").write_text("---\nname: git\n---\nEdited.\n")
    sha = w.commit(repo, "edit the git skill")
    w.push(repo, "main")
    # The bare remote's main now points at our new commit.
    remote_head = subprocess.run(["git", "-C", str(bare_remote), "rev-parse", "main"],
                                 capture_output=True, text=True, check=True).stdout.strip()
    assert remote_head == sha


def test_gitwriter_branch_push_leaves_main_alone(bare_remote, tmp_path):
    from agentplatform.gitservice import GitWriter
    w = GitWriter(str(bare_remote))
    repo = w.clone(tmp_path / "ws2")
    w.create_branch(repo, "coder/edit-1")
    (repo / "skills" / "git" / "SKILL.md").write_text("---\nname: git\n---\nProposed.\n")
    w.commit(repo, "proposal")
    w.push(repo, "coder/edit-1")
    assert "coder/edit-1" in _remote_branches(bare_remote)
    # main is untouched on the remote.
    assert "main" in _remote_branches(bare_remote)


class FakePRClient:
    def __init__(self):
        self.calls = []
    def open_pull_request(self, **kw):
        self.calls.append(kw)
        return {"number": 42, "html_url": "https://github.com/o/r/pull/42"}


def test_editservice_never_commits_to_main(bare_remote, tmp_path):
    """The tier-1 fast path is gone with the `agents/` tree (docs/design/15).
    Everything this service still edits is capability-as-code, so even a
    one-word edit lands as a pending change and main is untouched."""
    from agentplatform.gitservice import EditService, GitWriter
    pr = FakePRClient()
    svc = EditService(GitWriter(str(bare_remote)), pr_client=pr)
    before = subprocess.run(["git", "-C", str(bare_remote), "rev-parse", "main"],
                            capture_output=True, text=True, check=True).stdout.strip()
    res = svc.apply(tmp_path / "ws",
                    {"skills/git/SKILL.md": "---\nname: git\n---\nUse git nicely.\n"},
                    message="tweak the git skill", branch="coder/skill-git")
    assert res["tier"] == 2 and res["branch"] == "coder/skill-git"
    assert len(pr.calls) == 1                       # it is a reviewable change
    after = subprocess.run(["git", "-C", str(bare_remote), "rev-parse", "main"],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert after == before                          # main never moved


def test_editservice_opens_a_pr(bare_remote, tmp_path):
    from agentplatform.gitservice import EditService, GitWriter
    pr = FakePRClient()
    svc = EditService(GitWriter(str(bare_remote)), pr_client=pr)
    res = svc.apply(tmp_path / "ws",
                    {"skills/newskill/SKILL.md": "---\nname: newskill\n---\nNew.\n"},
                    message="add newskill", branch="coder/skill-newskill",
                    pr_title="Add newskill skill")
    assert res["tier"] == 2 and res["branch"] == "coder/skill-newskill"
    assert "coder/skill-newskill" in _remote_branches(bare_remote)
    assert len(pr.calls) == 1
    call = pr.calls[0]
    assert call["head"] == "coder/skill-newskill" and call["base"] == "main"
    assert call["title"] == "Add newskill skill"
    assert res["pr"]["number"] == 42


def test_editservice_without_pr_client_still_pushes(bare_remote, tmp_path):
    from agentplatform.gitservice import EditService, GitWriter
    svc = EditService(GitWriter(str(bare_remote)))  # no PR client (no token yet)
    res = svc.apply(tmp_path / "ws",
                    {"secrets/linear-api-key/secret.yaml": "name: linear-api-key\nseverity: required\n"},
                    message="declare severity", branch="coder/bump")
    assert res["tier"] == 2 and res["pr"] is None
    assert "coder/bump" in _remote_branches(bare_remote)


def test_editservice_noop_when_no_change(bare_remote, tmp_path):
    from agentplatform.gitservice import EditService, GitWriter
    svc = EditService(GitWriter(str(bare_remote)))
    # Write the identical content that already exists -> no diff.
    res = svc.apply(tmp_path / "ws",
                    {"skills/git/SKILL.md": "---\nname: git\n---\nUse git.\n"},
                    message="noop", branch="coder/noop")
    assert res["tier"] == 0 and res["sha"] is None and res["changes"] == []
