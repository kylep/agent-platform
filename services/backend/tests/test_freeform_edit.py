import httpx
import pytest

from agentplatform.agents import AgentStore
from agentplatform.api.app import create_app
from agentplatform.config import Settings
from agentplatform.db import Run
from agentplatform.events import FakeProducer, TOPIC_RUN_REQUESTS
from agentplatform.secrets import InMemorySecretStore
from sqlalchemy import select


async def test_freeform_unknown_target_404(admin_client):
    assert (await admin_client.post("/api/agents/ghost/edit",
                                    json={"instruction": "x"})).status_code == 404


async def test_freeform_without_platform_coder_409(admin_client):
    # default test store has only hello-world, no platform-coder
    r = await admin_client.post("/api/agents/hello-world/edit", json={"instruction": "x"})
    assert r.status_code == 409


@pytest.fixture
def coder_agents(tmp_path):
    for n, role in [("hello-world", "operator"), ("platform-coder", "coder")]:
        d = tmp_path / n; d.mkdir()
        (d / "agent.md").write_text(f"# {n}")
        (d / "manifest.yaml").write_text(f"role: {role}\n")
    return tmp_path


@pytest.fixture
async def coder_client(sf, coder_agents):
    producer = FakeProducer()
    app = create_app(Settings(agents_root=str(coder_agents)), sf, producer,
                     secret_store=InMemorySecretStore(), agent_store=AgentStore(coder_agents))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/setup", json={"password": "pw12345678"})
        await c.post("/api/login", json={"password": "pw12345678"})
        yield c, producer, sf


async def test_freeform_dispatches_platform_coder_run(coder_client):
    c, producer, sf = coder_client
    r = await c.post("/api/agents/hello-world/edit",
                     json={"instruction": "Make the greeting friendlier."})
    assert r.status_code == 202
    body = r.json()
    assert body["target_agent"] == "hello-world" and body["state"] == "queued"
    # a platform-coder run was created with the target + instruction baked in
    async with sf() as s:
        run = (await s.execute(select(Run))).scalars().one()
    assert run.agent == "platform-coder" and run.trigger == "self-edit"
    assert "hello-world" in run.prompt and "friendlier" in run.prompt
    # and it was published to the dispatcher
    assert any(t == TOPIC_RUN_REQUESTS and v["run_id"] == run.id for t, _, v in producer.published)
