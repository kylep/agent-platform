"""One-time Tasks are durable requests, not cron cursors."""
from datetime import timedelta

from sqlalchemy import select

from agentplatform.db import Run, ScheduledTask, ScheduledTaskEvent, utcnow
from agentplatform.task_scheduler import fire_due_tasks


async def test_task_fires_once_and_links_its_run(admin_client, sf, producer, seed_agent):
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    r = await admin_client.post("/api/tasks", json={
        "agent": "reminder", "title": "Check in", "prompt": "Check the weather.",
        "delay_minutes": 2, "model": "gpt-6-sol"})
    assert r.status_code == 201, r.text
    task = r.json()
    assert task["outcome"] == "upcoming"
    async with sf() as s:
        row = await s.get(ScheduledTask, task["id"])
        row.run_at = utcnow() - timedelta(minutes=1)
        row.expires_at = utcnow() + timedelta(minutes=30)
        await s.commit()
    assert await fire_due_tasks(sf, producer) == 1
    assert await fire_due_tasks(sf, producer) == 0
    async with sf() as s:
        runs = list((await s.execute(select(Run).where(Run.task_id == task["id"]))).scalars())
        events = list((await s.execute(select(ScheduledTaskEvent).where(
            ScheduledTaskEvent.task_id == task["id"]))).scalars())
    assert len(runs) == 1
    assert runs[0].trigger == "task"
    assert runs[0].requested_model == "gpt-6-sol"
    assert runs[0].requested_runtime == "codex"
    assert [e.kind for e in events] == ["created", "claimed", "run_queued"]
    detail = await admin_client.get(f"/api/tasks/{task['id']}")
    assert detail.json()["run_id"] == runs[0].id


async def test_task_cancel_and_deadline_leave_no_run(admin_client, sf, producer, seed_agent):
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    create = lambda: admin_client.post("/api/tasks", json={
        "agent": "reminder", "prompt": "Say hello", "delay_minutes": 2})
    cancelled = (await create()).json()
    r = await admin_client.post(f"/api/tasks/{cancelled['id']}/cancel")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "cancelled"
    expired = (await create()).json()
    async with sf() as s:
        for tid in (cancelled["id"], expired["id"]):
            row = await s.get(ScheduledTask, tid)
            row.run_at = utcnow() - timedelta(hours=2)
            row.expires_at = utcnow() - timedelta(hours=1)
        await s.commit()
    assert await fire_due_tasks(sf, producer) == 0
    async with sf() as s:
        assert (await s.get(ScheduledTask, expired["id"])).status == "expired"
        assert (await s.execute(select(Run).where(
            Run.task_id.in_((cancelled["id"], expired["id"]))))).first() is None


async def test_cross_agent_schedule_requires_link(admin_client, sf, producer, seed_agent):
    await seed_agent("one", runtime="codex", model="gpt-6-sol",
                     platform_tools=["mcp__platform__tasks"])
    await seed_agent("two", runtime="codex", model="gpt-6-sol")
    # A creator->target permission is a separate admin decision, not implied
    # by merely owning the Tasks Tool.
    from agentplatform.api.tasks import Caller, _target
    from fastapi import HTTPException
    async with sf() as s:
        try:
            await _target(s, "two", Caller(name="one", agent="one"), "")
        except HTTPException as e:
            assert e.status_code == 403
        else:
            assert False, "cross-agent Task unexpectedly allowed"
    r = await admin_client.post("/api/tasks/grants", json={"creator": "one", "target": "two"})
    assert r.status_code == 201
    async with sf() as s:
        target, model = await _target(s, "two", Caller(name="one", agent="one"), "")
        assert target.name == "two" and model == "gpt-6-sol"


async def test_task_idempotency_and_edit_conflict(admin_client, seed_agent):
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    body = {"agent": "reminder", "prompt": "Check in", "delay_minutes": 5,
            "idempotency_key": "one-request"}
    first = await admin_client.post("/api/tasks", json=body)
    assert first.status_code == 201, first.text
    again = await admin_client.post("/api/tasks", json=body)
    assert again.status_code == 201
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["run_at"] == first.json()["run_at"]
    changed = await admin_client.post("/api/tasks", json={**body, "prompt": "Different"})
    assert changed.status_code == 409
    task_id = first.json()["id"]
    updated = await admin_client.patch(f"/api/tasks/{task_id}", json={
        "version": first.json()["version"], "title": "New title"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == first.json()["version"] + 1
    stale = await admin_client.patch(f"/api/tasks/{task_id}", json={
        "version": first.json()["version"], "title": "Stale"})
    assert stale.status_code == 409


async def test_task_report_separates_unstarted_from_failed(admin_client, sf, producer, seed_agent):
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    body = {"agent": "reminder", "prompt": "Check in", "delay_minutes": 2}
    launched = (await admin_client.post("/api/tasks", json=body)).json()
    blocked = (await admin_client.post("/api/tasks", json=body)).json()
    async with sf() as s:
        a = await s.get(ScheduledTask, launched["id"])
        a.run_at, a.expires_at = utcnow() - timedelta(minutes=1), utcnow() + timedelta(minutes=30)
        b = await s.get(ScheduledTask, blocked["id"])
        b.run_at, b.expires_at = utcnow() - timedelta(hours=2), utcnow() - timedelta(hours=1)
        await s.commit()
    await fire_due_tasks(sf, producer)
    report = (await admin_client.get("/api/tasks/report")).json()
    assert report["total"] == 2
    assert report["running"] == 1
    assert report["expired"] == 1
    assert report["failed"] == 0


async def test_run_scoped_agent_can_self_schedule_but_not_cross_without_link(
        admin_client, token_client, sf, seed_agent, agent_store):
    from .test_relay_api import _agent_token, _seed
    from .test_wiki_api import _run_id
    await _seed(seed_agent, agent_store, "one", runtime="codex", model="gpt-6-sol",
                platform_tools=["mcp__platform__tasks"])
    await _seed(seed_agent, agent_store, "two", runtime="codex", model="gpt-6-sol")
    headers = await _agent_token(sf, "one", run_id=await _run_id(sf, "one"))
    body = {"agent": "one", "prompt": "Check in", "delay_minutes": 2}
    own = await token_client.post("/api/tasks", json=body, headers=headers)
    assert own.status_code == 201, own.text
    assert own.json()["creator"] == "agent:one"
    cross = await token_client.post("/api/tasks", json={**body, "agent": "two"}, headers=headers)
    assert cross.status_code == 403
    assert (await admin_client.post("/api/tasks/grants", json={
        "creator": "one", "target": "two"})).status_code == 201
    cross = await token_client.post("/api/tasks", json={**body, "agent": "two"}, headers=headers)
    assert cross.status_code == 201, cross.text
    spoofed = await token_client.post("/api/tasks", json={**body, "creator_agent": "two"}, headers=headers)
    assert spoofed.status_code == 422


async def test_relay_task_inherits_its_room_and_rejects_forged_origin(
        admin_client, token_client, sf, producer, seed_agent, agent_store):
    from .test_relay_api import _agent_token, _seed, _channel_id
    from agentplatform.db import RelayMessage
    await _seed(seed_agent, agent_store, "one", runtime="codex", model="gpt-6-sol",
                platform_tools=["mcp__platform__tasks"])
    cid = await _channel_id(sf, "general")
    async with sf() as s:
        message = RelayMessage(channel_id=cid, author="user:admin", body="remind me")
        s.add(message)
        await s.flush()
        parent = Run(agent="one", trigger="relay", prompt="remind me", requested_by="admin",
                     conversation_id=cid, trigger_message_id=message.id)
        s.add(parent)
        await s.commit()
        parent_id = parent.id
    headers = await _agent_token(sf, "one", run_id=parent_id)
    body = {"agent": "one", "prompt": "Check in", "delay_minutes": 2}
    forged = await token_client.post("/api/tasks", json={**body,
        "source_conversation_id": "a" * 32, "delivery": "relay"}, headers=headers)
    assert forged.status_code == 403
    response = await token_client.post("/api/tasks", json=body, headers=headers)
    assert response.status_code == 201, response.text
    task = response.json()
    assert task["delivery"] == "relay"
    assert task["source_conversation_id"] == cid
    assert "source Relay conversation" in task["delivery_notice"]
    async with sf() as s:
        row = await s.get(ScheduledTask, task["id"])
        row.run_at = utcnow() - timedelta(minutes=1)
        row.expires_at = utcnow() + timedelta(minutes=30)
        await s.commit()
    assert await fire_due_tasks(sf, producer) == 1
    async with sf() as s:
        run = await s.get(Run, task["run_id"] or (await s.get(ScheduledTask, task["id"])).run_id)
        assert run.conversation_id == cid
        assert run.trigger_message_id == message.id


async def test_target_revocation_after_fire_blocks_pod(admin_client, sf, producer,
                                                        seed_agent, agent_store):
    from agentplatform.config import Settings
    from agentplatform.db import AgentDef, RunState
    from agentplatform.dispatcher import Dispatcher, FakeLauncher
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    task = (await admin_client.post("/api/tasks", json={
        "agent": "reminder", "prompt": "Check in", "delay_minutes": 2})).json()
    async with sf() as s:
        row = await s.get(ScheduledTask, task["id"])
        row.run_at = utcnow() - timedelta(minutes=1)
        row.expires_at = utcnow() + timedelta(minutes=30)
        await s.commit()
    assert await fire_due_tasks(sf, producer) == 1
    async with sf() as s:
        target = await s.get(AgentDef, "reminder")
        target.accept_scheduled_tasks = False
        await s.commit()
    await agent_store.reload()
    launcher = FakeLauncher()
    d = Dispatcher(Settings(global_concurrency=2), sf, producer, agent_store, launcher)
    async with sf() as s:
        run_id = (await s.get(ScheduledTask, task["id"])).run_id
    await d.handle({"type": "run", "run_id": run_id})
    assert launcher.launched == []
    async with sf() as s:
        run = await s.get(Run, run_id)
        assert run.state == RunState.REJECTED
        assert "no longer accepts" in run.error


async def test_temporary_dispatch_block_defers_then_launches(admin_client, sf, producer,
                                                              seed_agent, agent_store):
    from agentplatform.config import Settings
    from agentplatform.db import RunState
    from agentplatform.dispatcher import Dispatcher, FakeLauncher
    await seed_agent("reminder", runtime="codex", model="gpt-6-sol")
    task = (await admin_client.post("/api/tasks", json={
        "agent": "reminder", "prompt": "Check in", "delay_minutes": 2})).json()
    async with sf() as s:
        row = await s.get(ScheduledTask, task["id"])
        row.run_at = utcnow() - timedelta(minutes=1)
        row.expires_at = utcnow() + timedelta(minutes=30)
        await s.commit()
    await fire_due_tasks(sf, producer)
    async with sf() as s:
        run_id = (await s.get(ScheduledTask, task["id"])).run_id
    await agent_store.reload()
    launcher = FakeLauncher()
    d = Dispatcher(Settings(global_concurrency=2), sf, producer, agent_store, launcher)
    async def unavailable(_manifest): return "credential temporarily unavailable"
    d._readiness_blocks = unavailable
    await d.handle({"type": "run", "run_id": run_id})
    async with sf() as s:
        run = await s.get(Run, run_id)
        assert run.state == RunState.QUEUED and run.deferred_until is not None
        run.deferred_until = utcnow() - timedelta(seconds=1)
        await s.commit()
    async def available(_manifest): return None
    d._readiness_blocks = available
    await d.handle({"type": "run", "run_id": run_id})
    assert launcher.launched == [run_id]
