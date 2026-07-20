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


async def test_quick_edit_prompt_is_tier1_and_lands_on_main(sh_client):
    c, selfhost = sh_client
    r = await c.post("/api/agents/demo/quick-edit",
                     json={"field": "prompt", "value": "You are demo. Improved.\n"})
    assert r.status_code == 200
    body = r.json()
    assert body["tier"] == 1 and body["branch"] == "main" and body["pr"] is None
    assert body["changes"] == ["agents/demo/agent.md"]
    # The edit is present on the remote's main branch.
    shown = subprocess.run(["git", "-C", str(selfhost["bare"]), "show", "main:agents/demo/agent.md"],
                           capture_output=True, text=True, check=True).stdout
    assert shown == "You are demo. Improved.\n"


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
