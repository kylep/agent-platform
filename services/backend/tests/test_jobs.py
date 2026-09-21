from datetime import timedelta

import pytest

from sqlalchemy import select

from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import (Conversation, RelayInvocation, RelayMessage, Run,
                              ScheduledJob, utcnow)
from agentplatform.events import (FakeProducer, TOPIC_RELAY_MESSAGES,
                                  TOPIC_RUN_INBOUND)
from agentplatform.relay_router import RelayRouter
from agentplatform.relay_store import relay_message_payload
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


async def test_job_timezone_defaults_to_utc_and_is_validated(admin_client):
    assert (await _mk(admin_client)).json()["timezone"] == ""
    job = (await _mk(admin_client, timezone="America/Toronto")).json()
    assert job["timezone"] == "America/Toronto"
    assert (await _mk(admin_client, timezone="Mars/Olympus")).status_code == 422
    # Editing the zone re-arms next_fire: same wall clock, different instant.
    r = await admin_client.patch(f"/api/jobs/{job['id']}", json={"timezone": "UTC"})
    assert r.status_code == 200 and r.json()["timezone"] == "UTC"
    assert r.json()["next_fire"] is None


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


# --- relay jobs: the #standup summons (docs/design/19) ----------------------
# A job that summons the room cannot be authored by an agent — `parse_mentions`
# strips an agent's `@all` and its posts carry a hop — so these assert the two
# things that makes true: the message is the PLATFORM's, and it makes no Run.

async def _standup(sf, **kw) -> str:
    """A relay job, already past due."""
    job_id = kw.pop("id", "jrelay")
    async with sf() as s:
        s.add(ScheduledJob(id=job_id, name="relay-standup", agent=None,
                           relay_channel=kw.pop("relay_channel", "standup"),
                           cron="* * * * *", prompt="@all — what did you do?",
                           enabled=True, next_fire=utcnow() - timedelta(minutes=1),
                           **kw))
        await s.commit()
    return job_id


async def _standup_room(sf):
    async with sf() as s:
        conv = (await s.execute(select(Conversation).where(
            Conversation.kind == "channel", Conversation.name == "standup"))).scalar_one()
        msgs = (await s.execute(select(RelayMessage).where(
            RelayMessage.channel_id == conv.id)
            .order_by(RelayMessage.created_at, RelayMessage.id))).scalars().all()
        runs = (await s.execute(select(Run))).scalars().all()
    return conv, msgs, runs


async def test_scheduler_relay_job_posts_as_the_platform(sf, agent_store):
    producer = FakeProducer()
    await _standup(sf)
    await Scheduler(sf, agent_store, producer).tick(utcnow())

    conv, msgs, runs = await _standup_room(sf)
    assert len(msgs) == 1
    msg = msgs[0]
    assert msg.author == "system:scheduler" and msg.kind == "text" and msg.hop == 0
    # `*`, not a list of names: the room is addressed, and the router is what
    # expands that to whoever is in it at the time.
    assert msg.mentions == ["*"]
    # No Run, and no run.requested event that would become one.
    assert runs == []
    assert all(topic != TOPIC_RUN_INBOUND for topic, _, _ in producer.published)
    assert [topic for topic, _, _ in producer.published] == [TOPIC_RELAY_MESSAGES]


async def test_scheduler_skips_a_relay_job_whose_room_is_gone(sf, agent_store):
    producer = FakeProducer()
    await _standup(sf, relay_channel="no-such-room")
    await Scheduler(sf, agent_store, producer).tick(utcnow())
    _, msgs, runs = await _standup_room(sf)
    assert msgs == [] and runs == [] and producer.published == []
    # The job still advanced: a missing room must not make it fire twice a tick.
    async with sf() as s:
        assert (await s.get(ScheduledJob, "jrelay")).last_fire is not None


async def test_relay_job_message_summons_every_enabled_agent(sf, producer, seed_agent):
    """End to end through the REAL router: the scheduler's post is just a
    message, and `@all` from a non-agent author is what wakes the room."""
    for name in ("ada", "bob"):
        await seed_agent(name, description="t")
    # A PLATFORM agent in the room. `@all` must walk past it: the health monitor
    # answers to its own name, not to a question addressed to everybody, and a
    # standup that woke it every morning would buy a Claude run to report work
    # nobody asked it about.
    await seed_agent("health-monitor", description="t", system=True)
    store = AgentStore(sf)
    await store.reload()
    await _standup(sf)
    await Scheduler(sf, store, FakeProducer()).tick(utcnow())

    conv, msgs, _ = await _standup_room(sf)
    async with sf() as s:
        payload = relay_message_payload(await s.get(RelayMessage, msgs[0].id), conv)
    await RelayRouter(Settings(), sf, producer, store).handle(payload)

    async with sf() as s:
        decided = [(i.agent, i.decision) for i in
                   (await s.execute(select(RelayInvocation))).scalars()]
        runs = (await s.execute(select(Run))).scalars().all()
        # The provider artist and engineer are participants; the Codex artist
        # is Studio infrastructure and is deliberately skipped by `@all`.
        assert sorted(decided) == [("ada", "invoked"), ("artist", "invoked"),
                                   ("bob", "invoked"), ("engineer", "invoked")]
    assert sorted(r.agent for r in runs) == [
        "ada", "artist", "bob", "engineer"]
    assert all(r.trigger == "mention" for r in runs)
    # The summons still ADDRESSES the room — `*`, not a roster — so who it wakes
    # stays the router's decision and can change without rewriting the message.
    assert msgs[0].mentions == ["*"]


async def test_a_failed_relay_post_does_not_sink_the_rest_of_the_tick(
        sf, agent_store, monkeypatch):
    """`tick` fires every due job in one pass, so a database blip on the standup
    must not cost every job after it in the list its turn."""
    async def boom(*a, **kw):
        raise RuntimeError("kafka is having a day")
    monkeypatch.setattr("agentplatform.scheduler.summon_channel", boom)
    # Inserted first, so the failing job is the one the tick reaches first.
    await _standup(sf)
    async with sf() as s:
        s.add(ScheduledJob(id="jafter", name="after", agent="hello-world",
                           cron="* * * * *", prompt="go", enabled=True,
                           next_fire=utcnow() - timedelta(minutes=1)))
        await s.commit()

    producer = FakeProducer()
    await Scheduler(sf, agent_store, producer).tick(utcnow())
    assert [data["prompt"] for _, _, data in producer.published] == ["go"]
    # The failed fire is still a fire: the job advanced, so it retries on its
    # own cron rather than firing again on the very next tick.
    async with sf() as s:
        assert (await s.get(ScheduledJob, "jrelay")).last_fire is not None


async def test_run_now_on_a_relay_job_posts_the_same_summons(admin_client, sf):
    r = await admin_client.post("/api/jobs", json={
        "name": "relay-standup", "relay_channel": "standup", "cron": "0 9 * * *",
        "prompt": "@all — what did you do?"})
    assert r.status_code == 201
    job = r.json()
    assert job["agent"] is None and job["relay_channel"] == "standup"

    r = await admin_client.post(f"/api/jobs/{job['id']}/run")
    assert r.status_code == 200 and r.json()["relay_channel"] == "standup"
    _, msgs, runs = await _standup_room(sf)
    assert [(m.id, m.author, m.mentions) for m in msgs] == \
        [(r.json()["id"], "system:scheduler", ["*"])]
    assert runs == []


async def test_run_now_on_a_relay_job_with_no_room_is_a_conflict(admin_client):
    job = (await admin_client.post("/api/jobs", json={
        "name": "ghost", "relay_channel": "no-such-room", "cron": "0 9 * * *",
        "prompt": "hi"})).json()
    assert (await admin_client.post(f"/api/jobs/{job['id']}/run")).status_code == 409


async def test_a_job_needs_exactly_one_action(admin_client):
    base = {"name": "n", "cron": "0 9 * * *", "prompt": "p"}
    assert (await admin_client.post("/api/jobs", json=base)).status_code == 422
    assert (await admin_client.post("/api/jobs", json={
        **base, "agent": "hello-world", "relay_channel": "standup"})).status_code == 422


async def test_a_relay_job_cannot_be_patched_onto_an_agent(admin_client):
    job = (await admin_client.post("/api/jobs", json={
        "name": "relay-standup", "relay_channel": "standup", "cron": "0 9 * * *",
        "prompt": "p"})).json()
    r = await admin_client.patch(f"/api/jobs/{job['id']}", json={"agent": "hello-world"})
    assert r.status_code == 422
    # The off-switch still works: pausing it is how an admin says no.
    r = await admin_client.patch(f"/api/jobs/{job['id']}", json={"enabled": False})
    assert r.status_code == 200 and r.json()["enabled"] is False


async def test_agent_jobs_still_report_no_relay_channel(admin_client):
    job = (await _mk(admin_client)).json()
    assert job["relay_channel"] is None and job["agent"] == "hello-world"
    rows = (await admin_client.get("/api/jobs")).json()
    assert all("relay_channel" in j for j in rows)
