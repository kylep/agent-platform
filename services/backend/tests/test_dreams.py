"""Nightly dreaming (docs/design/44 B)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from agentplatform import dreams as D
from agentplatform.config import Settings
from agentplatform.db import (Conversation, Memory, RelayMessage, RelayParticipant, Run, RunState,
                              ScheduledJob, utcnow)
from agentplatform.persona_memory import source_hash
from agentplatform.scheduler import Scheduler

from .test_relay_api import _agent_token

MEM = ["mcp__platform__memory"]


async def _agent(seed_agent, name="pai", **fields):
    fields.setdefault("platform_tools", [*MEM, "mcp__platform__relay"])
    fields.setdefault("agent_type", "persona")
    await seed_agent(name, **fields)


async def _start(sf, producer, agent="pai", **kw):
    return await D.start(sf, producer, agent, requested_by="test", **kw)


async def _materialize(sf, dream, state=RunState.RUNNING):
    async with sf() as s:
        s.add(Run(id=dream.run_id, agent=dream.agent, trigger="schedule", requested_by="dreamer",
                  prompt=D.DREAM_PROMPT, state=state,
                  definition_snapshot={"name": dream.agent, "prompt": "p",
                                       "platform_tools": [*MEM, "mcp__platform__relay"]}))
        await s.commit()


async def _review(client, sf, dream, section, **body):
    headers = await _agent_token(sf, dream.agent, role="reader", run_id=dream.run_id)
    return await client.post("/api/memory-review", headers=headers, json={"section": section, **body})


async def test_start_requests_the_agents_own_run_with_a_budget(sf, producer, seed_agent):
    await _agent(seed_agent)
    async with sf() as s:
        s.add_all(Memory(agent="pai", content=f"note {i}") for i in range(9))
        await s.commit()
    d = await _start(sf, producer)
    assert d.state == "scheduled" and d.memory_count == 9 and d.loss_budget == 2
    assert d.window_end - d.window_start == timedelta(hours=24)
    topic, key, data = producer.published[-1]
    assert key == d.run_id and data["agent"] == "pai" and data["trigger"] == "schedule"
    assert "model" not in data and data["prompt"].startswith("You are dreaming.")
    # One dream at a time.
    assert await _start(sf, producer) is None


@pytest.mark.parametrize("fields", [{"system": True}, {"result_topic": "app.x.inbound"},
                                    {"platform_tools": []}, {"enabled": False}])
async def test_ineligible_agents_never_dream(sf, producer, seed_agent, fields):
    await _agent(seed_agent, "w", **fields)
    assert await _start(sf, producer, "w") is None


async def test_window_advances_only_past_dreams_that_read_it(sf, producer, seed_agent):
    await _agent(seed_agent)
    first = await _start(sf, producer)
    async with sf() as s:
        row = await s.get(D.Dream, first.id)
        row.state = "failed"
        await s.commit()
    second = await _start(sf, producer, force=True)
    # The failed dream's window is carried into the next one.
    assert D._utc(second.window_start) == D._utc(first.window_start)
    async with sf() as s:
        row = await s.get(D.Dream, second.id)
        row.state = "done"
        await s.commit()
    # Nothing happened since: an idle night costs no run.
    quiet = await _start(sf, producer)
    assert quiet.state == "skipped" and quiet.run_id is None
    async with sf() as s:
        s.add(RelayMessage(channel_id="x" * 32, author="agent:pai", body="hi"))
        await s.commit()
    busy = await _start(sf, producer)
    assert busy.state == "scheduled"
    assert D._utc(busy.window_start) == D._utc(quiet.window_end)


async def test_review_shows_only_rooms_the_agent_can_read_now(token_client, sf, producer, seed_agent):
    await _agent(seed_agent)
    async with sf() as s:
        group = Conversation(connector="web", kind="group", title="team")
        private = Conversation(connector="web", kind="group", title="not-pai")
        laptop = Conversation(connector="web", kind="dm", title="local", home="local_persona")
        s.add_all([group, private, laptop])
        await s.flush()
        s.add(RelayParticipant(channel_id=group.id, participant="agent:pai"))
        s.add_all([
            RelayMessage(channel_id=group.id, author="user:kyle", body="bring the model question to the roundtable"),
            RelayMessage(channel_id=group.id, author="agent:pai", body="I'll bring it"),
            RelayMessage(channel_id=group.id, author="user:kyle", body="retracted", deleted_at=utcnow()),
            RelayMessage(channel_id=private.id, author="user:kyle", body="secret elsewhere"),
            # Pai's turns are recorded in a laptop room it has no Relay access to.
            RelayMessage(channel_id=laptop.id, author="agent:pai", body="local only"),
            RelayMessage(channel_id=laptop.id, author="user:kyle", body="laptop private"),
        ])
        s.add(Run(agent="pai", trigger="schedule", requested_by="t", prompt="morning digest",
                  state=RunState.SUCCEEDED, result="digest sent", finished_at=utcnow()))
        await s.commit()
    d = await _start(sf, producer)
    await _materialize(sf, d)
    page = (await _review(token_client, sf, d, "conversations")).json()
    texts = [i.get("text") or i.get("result") for i in page["items"]]
    assert "bring the model question to the roundtable" in texts and "I'll bring it" in texts
    assert "digest sent" in texts
    assert not {"retracted", "secret elsewhere", "local only", "laptop private"} & set(texts)
    assert page["done"] is True
    async with sf() as s:
        row = await s.get(D.Dream, d.id)
        assert row.state == "running" and row.pages_served == {"conversations": True}


async def test_review_and_delete_exist_only_while_dreaming(token_client, sf, producer, seed_agent):
    await _agent(seed_agent)
    async with sf() as s:
        run = Run(agent="pai", trigger="manual", requested_by="t", prompt="x", state=RunState.RUNNING,
                  definition_snapshot={"platform_tools": MEM})
        s.add(run)
        await s.commit()
    headers = await _agent_token(sf, "pai", role="reader", run_id=run.id)
    r = await token_client.post("/api/memory-review", headers=headers, json={"section": "memories"})
    assert r.status_code == 403
    r = await token_client.post("/api/memory-delete", headers=headers,
                                json={"key": "k", "expected_revision": "0" * 64})
    assert r.status_code == 403


async def test_whoami_narrows_a_dream_run_to_the_memory_tool(token_client, sf, producer, seed_agent):
    await _agent(seed_agent)
    d = await _start(sf, producer)
    await _materialize(sf, d)
    headers = await _agent_token(sf, "pai", role="reader", run_id=d.run_id)
    me = (await token_client.get("/api/whoami", headers=headers)).json()
    assert me["dream"] is True and me["tools"] == MEM


async def test_delete_requires_the_current_revision_and_pages_cover_memories(token_client, sf, producer, seed_agent):
    await _agent(seed_agent)
    async with sf() as s:
        keep = Memory(agent="pai", key="preferences.tea", content="Kyle likes green tea")
        stale = Memory(agent="pai", content="receipt: posted digest")
        other = Memory(agent="olu", key="mine", content="olu's")
        s.add_all([keep, stale, other])
        await s.commit()
        other_rev = source_hash(other)
    d = await _start(sf, producer)
    await _materialize(sf, d)
    page = (await _review(token_client, sf, d, "memories")).json()
    assert {i["content"] for i in page["items"]} == {"Kyle likes green tea", "receipt: posted digest"}
    stale_rev = next(i["revision"] for i in page["items"] if i["memory_id"] == stale.id)
    headers = await _agent_token(sf, "pai", role="reader", run_id=d.run_id)
    wrong = await token_client.post("/api/memory-delete", headers=headers,
                                    json={"memory_id": stale.id, "expected_revision": "0" * 64})
    assert wrong.status_code == 409
    foreign = await token_client.post("/api/memory-delete", headers=headers,
                                      json={"memory_id": other.id, "expected_revision": other_rev})
    assert foreign.status_code == 404
    ok = await token_client.post("/api/memory-delete", headers=headers,
                                 json={"memory_id": stale.id, "expected_revision": stale_rev})
    assert ok.status_code == 200
    async with sf() as s:
        assert await s.get(Memory, stale.id) is None and await s.get(Memory, other.id) is not None


async def test_finalize_waits_for_the_diary_and_records_coverage(sf, producer, seed_agent):
    await _agent(seed_agent)
    d = await _start(sf, producer)
    await _materialize(sf, d, state=RunState.SUCCEEDED)
    async with sf() as s:
        run = await s.get(Run, d.run_id)
        run.finished_at = utcnow()
        await s.commit()
    assert await D.finalize(sf) == 0  # no result yet: the diary frame may still be coming
    async with sf() as s:
        run = await s.get(Run, d.run_id)
        run.result = "I merged two notes."
        row = await s.get(D.Dream, d.id)
        row.pages_served = {"conversations": True}
        await s.commit()
    assert await D.finalize(sf) == 1
    async with sf() as s:
        row = await s.get(D.Dream, d.id)
        assert row.state == "incomplete" and row.diary == "I merged two notes."
        assert set(row.stats["memories"]) == {"insert", "update", "delete"}


async def test_finalize_fails_dreams_whose_run_never_appeared(sf, producer, seed_agent):
    await _agent(seed_agent)
    d = await _start(sf, producer)
    async with sf() as s:
        row = await s.get(D.Dream, d.id)
        row.created_at = utcnow() - timedelta(hours=1)
        await s.commit()
    assert await D.finalize(sf) == 1
    async with sf() as s:
        assert (await s.get(D.Dream, d.id)).state == "failed"


async def test_enrollment_seeds_staggered_jobs_once(sf, producer, agent_store, seed_agent):
    await _agent(seed_agent, "kai")
    await _agent(seed_agent, "pai")
    await seed_agent("health-monitor", system=True, platform_tools=MEM)
    settings = Settings(dream_enabled=True, dream_exclude="kai")
    seeded = await D.enroll(sf, settings)
    assert "pai" in seeded and "kai" not in seeded and "health-monitor" not in seeded
    async with sf() as s:
        job = (await s.execute(select(ScheduledJob).where(ScheduledJob.name == "dream:pai"))).scalar_one()
        assert (job.cron.endswith(" * * *"), job.timezone, job.run_when) == (True, "America/Toronto", "dream")
        await s.delete(job)
        await s.commit()
    # A deleted dream job stays deleted.
    assert await D.enroll(sf, settings) == []
    assert D.job_cron(settings, 5) == "15 4 * * *"
    assert await D.enroll(sf, Settings(dream_enabled=False)) == []


async def test_a_due_dream_job_opens_a_dream_and_run_now_forces_one(admin_client, sf, producer, agent_store, seed_agent):
    await _agent(seed_agent)
    await agent_store.reload()
    async with sf() as s:
        job = ScheduledJob(name="dream:pai", agent="pai", cron="0 3 * * *", timezone="America/Toronto",
                           prompt=D.JOB_PROMPT, run_when="dream", next_fire=utcnow() - timedelta(minutes=1))
        s.add(job)
        await s.commit()
    await Scheduler(sf, agent_store, producer).tick(utcnow())
    async with sf() as s:
        dreams = (await s.execute(select(D.Dream))).scalars().all()
        assert len(dreams) == 1 and dreams[0].requested_by == f"job:{job.id}"
        dreams[0].state = "done"
        await s.commit()
    r = await admin_client.post(f"/api/jobs/{job.id}/run")
    assert r.status_code == 200, r.text
    async with sf() as s:
        assert (await s.execute(select(D.Dream).where(D.Dream.state == "scheduled"))).scalars().first().run_id == r.json()["id"]
    listed = (await admin_client.get("/api/dreams?agent=pai")).json()
    assert len(listed) == 2
    assert (await admin_client.post("/api/dreams", json={"agent": "pai"})).status_code == 409


async def test_a_dream_runs_credential_reaches_only_memory_routes(token_client, sf, producer, seed_agent):
    """Every broker Tool calls the API on the run's credential, so the fence
    there covers relay, discord, tickets and the rest, on both runtimes."""
    await _agent(seed_agent)
    d = await _start(sf, producer)
    await _materialize(sf, d)
    headers = await _agent_token(sf, "pai", role="relay", run_id=d.run_id)
    assert (await token_client.get("/api/relay/channels", headers=headers)).status_code == 403
    assert (await token_client.get("/api/whoami", headers=headers)).status_code == 200
    assert (await _review(token_client, sf, d, "commitments")).status_code == 200
    # Rails outlive a dream that finalize gave up on.
    async with sf() as s:
        (await s.get(D.Dream, d.id)).state = "failed"
        await s.commit()
    assert (await token_client.get("/api/relay/channels", headers=headers)).status_code == 403


async def test_a_far_cursor_does_not_count_as_reading(token_client, sf, producer, seed_agent):
    await _agent(seed_agent)
    d = await _start(sf, producer)
    await _materialize(sf, d)
    page = (await _review(token_client, sf, d, "memories", cursor="999")).json()
    assert page["done"] is True
    async with sf() as s:
        assert "memories" not in (await s.get(D.Dream, d.id)).pages_served
