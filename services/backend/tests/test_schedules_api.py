import httpx
import pytest

from agentplatform.agents import AgentStore
from agentplatform.api.app import create_app
from agentplatform.config import Settings
from agentplatform.db import AgentDef
from agentplatform.events import FakeProducer
from agentplatform.secrets import InMemorySecretStore


@pytest.fixture
async def sched_agents(sf, seed_agent):
    await seed_agent("cronbot", description="cronbot",
                     entrypoints={"crons": [{"schedule": "*/10 * * * *"}]})
    await seed_agent("plain", description="plain")
    # A cron that no longer parses is a quarantined definition, not a schedule.
    async with sf() as s:
        row = await s.get(AgentDef, "badcron") or AgentDef(name="badcron")
        row.entrypoints = {"crons": [{"schedule": "not-a-cron"}]}
        s.add(row)
        await s.commit()


@pytest.fixture
async def sched_client(sf, sched_agents):
    store = AgentStore(sf)
    await store.reload()
    app = create_app(Settings(), sf, FakeProducer(),
                     secret_store=InMemorySecretStore(), agent_store=store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/api/setup", json={"password": "pw12345678"})
        await c.post("/api/login", json={"password": "pw12345678"})
        yield c


async def test_list_only_valid_cron_agents(sched_client):
    r = await sched_client.get("/api/schedules")
    assert r.status_code == 200
    body = r.json()
    assert [s["agent"] for s in body] == ["cronbot"]     # plain + badcron excluded
    assert body[0]["cron"] == "*/10 * * * *" and body[0]["enabled"] is True


async def test_disable_then_enable(sched_client):
    assert (await sched_client.post("/api/schedules/cronbot/disable")).json()["enabled"] is False
    assert (await sched_client.get("/api/schedules")).json()[0]["enabled"] is False
    assert (await sched_client.post("/api/schedules/cronbot/enable")).json()["enabled"] is True
    assert (await sched_client.get("/api/schedules")).json()[0]["enabled"] is True


async def test_schedule_action_validation(sched_client):
    assert (await sched_client.post("/api/schedules/cronbot/bogus")).status_code == 404
    assert (await sched_client.post("/api/schedules/plain/disable")).status_code == 404   # no schedule
