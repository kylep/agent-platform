from datetime import timedelta

import pytest

from agentplatform.db import ScheduledJob, utcnow
from agentplatform.events import FakeProducer
from agentplatform.scheduler import Scheduler


async def _mk(admin_client, **kw):
    body = {"name": "morning-news", "agent": "hello-world",
            "cron": "0 11 * * *", "prompt": "Do the news.", **kw}
    return await admin_client.post("/api/jobs", json=body)


async def test_job_crud(admin_client):
    r = await _mk(admin_client)
    assert r.status_code == 201
    job = r.json()
    assert job["name"] == "morning-news" and job["enabled"] is True

    rows = (await admin_client.get("/api/jobs")).json()
    assert any(j["id"] == job["id"] for j in rows)

    r = await admin_client.patch(f"/api/jobs/{job['id']}", json={"prompt": "Updated.", "enabled": False})
    assert r.status_code == 200 and r.json()["prompt"] == "Updated." and r.json()["enabled"] is False

    assert (await admin_client.delete(f"/api/jobs/{job['id']}")).status_code == 204
    assert all(j["id"] != job["id"] for j in (await admin_client.get("/api/jobs")).json())


async def test_job_rejects_bad_cron_and_unknown_agent(admin_client):
    assert (await _mk(admin_client, cron="not a cron")).status_code == 422
    assert (await _mk(admin_client, agent="ghost")).status_code == 422


async def test_run_now_materializes_a_run(admin_client):
    job = (await _mk(admin_client)).json()
    r = await admin_client.post(f"/api/jobs/{job['id']}/run")
    assert r.status_code == 200
    run_id = r.json()["id"]
    run = await admin_client.get(f"/api/runs/{run_id}")
    assert run.status_code == 200
    assert run.json()["agent"] == "hello-world" and run.json()["trigger"] == "manual"


# --- scheduler fires jobs (not just manifest schedules) ---------------------

async def test_scheduler_fires_due_job(sf, agent_store):
    producer = FakeProducer()
    sched = Scheduler(sf, agent_store, producer)
    now = utcnow()
    async with sf() as s:
        # A job already past due (next_fire in the past) fires this tick.
        s.add(ScheduledJob(id="j1", name="n", agent="hello-world", cron="* * * * *",
                           prompt="go", enabled=True, next_fire=now - timedelta(minutes=1)))
        # A disabled job does not fire.
        s.add(ScheduledJob(id="j2", name="n2", agent="hello-world", cron="* * * * *",
                           prompt="no", enabled=False, next_fire=now - timedelta(minutes=1)))
        await s.commit()
    await sched.tick(now)
    # published entries are (topic, key, data) tuples.
    fired = [data for _, _, data in producer.published if data.get("prompt") == "go"]
    assert len(fired) == 1
    assert fired[0]["agent"] == "hello-world" and fired[0]["trigger"] == "schedule"
    assert all(data.get("prompt") != "no" for _, _, data in producer.published)


async def test_scheduler_arms_new_job_without_firing(sf, agent_store):
    producer = FakeProducer()
    sched = Scheduler(sf, agent_store, producer)
    now = utcnow()
    async with sf() as s:
        s.add(ScheduledJob(id="j3", name="n", agent="hello-world", cron="* * * * *",
                           prompt="go", enabled=True, next_fire=None))
        await s.commit()
    await sched.tick(now)
    assert not producer.published            # first tick only arms next_fire
    async with sf() as s:
        assert (await s.get(ScheduledJob, "j3")).next_fire is not None
