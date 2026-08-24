"""Picking the git credential for a self-edit (api/gitedit.py).

These moved out of the agent quick-edit suite when agent definitions became
rows: the helpers are shared by the skill/tool/secret change loop, which is
still a git flow, and the token-never-lands-in-the-URL rule is the security
property worth keeping pinned.
"""
from agentplatform.api.gitedit import _build_writer, _push_url
from agentplatform.gitservice import GitWriter


class _S:
    def __init__(self, **kw):
        self.git_remote_url = kw.get("git_remote_url", "")
        self.github_repo = kw.get("github_repo", "")
        self.default_branch = "main"


def test_push_url_never_embeds_token():
    url = "https://github.com/kylep/agent-platform.git"
    out = _push_url(url, "gho_secret")
    assert out == "https://x-access-token@github.com/kylep/agent-platform.git"
    assert "gho_secret" not in out           # token is NOT in the URL
    # No token, or non-github URL (local bare remote in tests) → unchanged.
    assert _push_url(url, None) == url
    assert _push_url("/tmp/bare.git", "gho_secret") == "/tmp/bare.git"


def test_gitwriter_strips_token_whitespace():
    w = GitWriter("https://x-access-token@github.com/o/r.git", token="gho_x\n")
    assert w.token == "gho_x"                # trailing newline stripped
    assert GitWriter("u", token="  ").token is None or GitWriter("u", token="  ").token == ""


def test_build_writer_uses_token():
    s = _S(git_remote_url="https://github.com/o/r.git", github_repo="o/r")
    writer, pr = _build_writer(s, {"token": "gho_x"})
    assert writer.token == "gho_x"
    # The token never lands in the remote URL (it would leak into git's argv).
    assert "gho_x" not in writer.remote_url and pr is not None


def test_build_writer_local_remote_needs_no_cred():
    s = _S(git_remote_url="/tmp/bare.git")
    writer, pr = _build_writer(s, None)
    assert writer.token is None and pr is None


def test_build_writer_none_without_anything():
    assert _build_writer(_S(), None) is None
