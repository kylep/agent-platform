import subprocess

import httpx
import pytest

from agentplatform.agents import AgentStore
from agentplatform.api.app import create_app
from agentplatform.config import Settings
from agentplatform.events import FakeProducer
from agentplatform.secrets import InMemorySecretStore


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def selfhost(tmp_path):
    """A bare origin holding one agent, plus a checkout the AgentStore reads."""
    bare = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", "-q", str(bare))
    seed = tmp_path / "seed"
    git(tmp_path, "clone", "-q", str(bare), str(seed))
    git(seed, "config", "user.email", "s@s"); git(seed, "config", "user.name", "s")
    d = seed / "agents" / "demo"
    d.mkdir(parents=True)
    (d / "agent.md").write_text("You are demo.\n")
    (d / "manifest.yaml").write_text("description: demo\n")
    git(seed, "add", "-A"); git(seed, "commit", "-qm", "init")
    git(seed, "branch", "-M", "main"); git(seed, "push", "-q", "origin", "main")
    return {"bare": bare, "agents_root": seed / "agents"}


@pytest.fixture
async def sh_client(sf, selfhost):
    settings = Settings(agents_root=str(selfhost["agents_root"]),
                        git_remote_url=str(selfhost["bare"]), default_branch="main")
    app = create_app(settings, sf, FakeProducer(),
                     secret_store=InMemorySecretStore(),
                     agent_store=AgentStore(selfhost["agents_root"]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/setup", json={"password": "pw12345678"})
        await c.post("/api/login", json={"password": "pw12345678"})
        yield c, selfhost


async def test_quick_edit_always_opens_pending_change(sh_client):
    """A raw definition save is review-gated even when the change would
    classify tier 1 (body-only): it lands on the agent's deterministic branch,
    never straight on main — the UI's pending-change lock depends on this."""
    c, selfhost = sh_client
    r = await c.post("/api/agents/demo/quick-edit",
                     json={"field": "prompt", "value": "You are demo. Improved.\n"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == 2 and body["branch"] == "coder/agent-demo"
    assert body["changes"] == ["agents/demo/agent.md"]
    # main is untouched; the edit sits on the branch awaiting review.
    on_main = subprocess.run(["git", "-C", str(selfhost["bare"]), "show", "main:agents/demo/agent.md"],
                             capture_output=True, text=True, check=True).stdout
    assert on_main == "You are demo.\n"
    on_branch = subprocess.run(["git", "-C", str(selfhost["bare"]),
                                "show", "coder/agent-demo:agents/demo/agent.md"],
                               capture_output=True, text=True, check=True).stdout
    assert on_branch == "You are demo. Improved.\n"


async def test_quick_edit_noop_save_is_tier0(sh_client):
    c, _ = sh_client
    r = await c.post("/api/agents/demo/quick-edit",
                     json={"field": "prompt", "value": "You are demo.\n"})
    assert r.status_code == 200
    assert r.json()["tier"] == 0


async def test_quick_edit_unknown_agent_404(sh_client):
    c, _ = sh_client
    r = await c.post("/api/agents/ghost/quick-edit", json={"field": "prompt", "value": "x"})
    assert r.status_code == 404


async def test_quick_edit_unsupported_field_422(sh_client):
    c, _ = sh_client
    r = await c.post("/api/agents/demo/quick-edit", json={"field": "role", "value": "admin"})
    assert r.status_code == 422


async def test_quick_edit_disabled_without_config(admin_client):
    # The default test app has no git_remote_url configured.
    r = await admin_client.post("/api/agents/hello-world/quick-edit",
                                json={"field": "prompt", "value": "x"})
    assert r.status_code == 409


def test_push_url_never_embeds_token():
    from agentplatform.api.agents import _push_url
    url = "https://github.com/kylep/agent-platform.git"
    out = _push_url(url, "gho_secret")
    assert out == "https://x-access-token@github.com/kylep/agent-platform.git"
    assert "gho_secret" not in out           # token is NOT in the URL
    # No token, or non-github URL (local bare remote in tests) → unchanged.
    assert _push_url(url, None) == url
    assert _push_url("/tmp/bare.git", "gho_secret") == "/tmp/bare.git"


def test_gitwriter_strips_token_whitespace():
    from agentplatform.gitservice import GitWriter
    w = GitWriter("https://x-access-token@github.com/o/r.git", token="gho_x\n")
    assert w.token == "gho_x"                # trailing newline stripped
    assert GitWriter("u", token="  ").token is None or GitWriter("u", token="  ").token == ""


class _S:
    def __init__(self, **kw):
        self.git_remote_url = kw.get("git_remote_url", "")
        self.github_repo = kw.get("github_repo", "")
        self.default_branch = "main"


def test_build_writer_uses_token():
    from agentplatform.api.agents import _build_writer
    s = _S(git_remote_url="https://github.com/o/r.git", github_repo="o/r")
    writer, pr = _build_writer(s, {"token": "gho_x"})
    assert writer.token == "gho_x"
    # The token never lands in the remote URL (it would leak into git's argv).
    assert "gho_x" not in writer.remote_url and pr is not None


def test_build_writer_local_remote_needs_no_cred():
    from agentplatform.api.agents import _build_writer
    s = _S(git_remote_url="/tmp/bare.git")
    writer, pr = _build_writer(s, None)
    assert writer.token is None and pr is None


def test_build_writer_none_without_anything():
    from agentplatform.api.agents import _build_writer
    assert _build_writer(_S(), None) is None
