"""Lease-fenced, Postgres-first firing of one-time Tasks."""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import timedelta

from sqlalchemy import and_, func, or_, select

from agentplatform import maintenance_mode
from agentplatform.db import (AgentDef, Conversation, RelayMessage, Run, RunState, ScheduledTask,
                              ScheduledTaskEvent, TaskScheduleGrant)
from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.relay import is_member
from agentplatform.relay_store import enabled_agents, explicit_members
from agentplatform.scheduler import as_utc

log = logging.getLogger("task-scheduler")
LEASE = timedelta(seconds=90)


async def _db_now(s):
    return as_utc((await s.execute(select(func.now()))).scalar_one())


def _event(s, task, kind: str, reason: str | None = None):
    s.add(ScheduledTaskEvent(task_id=task.id, kind=kind, actor="system:scheduler",
                             reason=reason, run_id=task.run_id if kind == "run_queued" else None))


async def task_authority(s, task: ScheduledTask) -> str | None:
    target = await s.get(AgentDef, task.agent)
    if not target or not target.enabled:
        return "target was deleted or disabled"
    if target.system_source or not target.accept_scheduled_tasks:
        return "target no longer accepts scheduled Tasks"
    if target.runtime != task.runtime:
        return "target runtime changed; create a replacement Task"
    if task.creator_agent:
        creator = await s.get(AgentDef, task.creator_agent)
        if not creator or not creator.enabled or "mcp__platform__tasks" not in (creator.platform_tools or []):
            return "creator disabled or Tasks grant revoked"
        if task.creator_agent != task.agent and not creator.can_invoke:
            if await s.get(TaskScheduleGrant, (task.creator_agent, task.agent)) is None:
                return "cross-agent scheduling grant revoked"
    return None


async def _relay_room(s, task: ScheduledTask) -> tuple[str | None, str | None]:
    if task.delivery != "relay" or not task.source_conversation_id:
        return None, None
    room = await s.get(Conversation, task.source_conversation_id)
    msg = await s.get(RelayMessage, task.source_message_id) if task.source_message_id else None
    if not room or room.home != "relay" or not msg or msg.channel_id != room.id:
        return None, "source room is no longer an internal Relay conversation"
    roster = await enabled_agents(s)
    members = await explicit_members(s, room.id)
    if not is_member(room, f"agent:{task.agent}", roster, members):
        return None, "target no longer belongs to source room"
    if task.creator_agent and not is_member(room, f"agent:{task.creator_agent}", roster, members):
        return None, "creator no longer belongs to source room"
    return room.id, None


async def fire_due_tasks(session_factory, producer, *, limit: int = 50) -> int:
    """Claim and fire due Tasks. Repeated ticks and crash recovery share one run ID."""
    owner = uuid.uuid4().hex
    async with session_factory() as s:
        if await maintenance_mode.is_paused(s):
            return 0          # Tasks stay scheduled; Kyle's resume releases them
        now = await _db_now(s)
        ids = (await s.execute(select(ScheduledTask.id).where(
            or_(ScheduledTask.status == "scheduled",
                (ScheduledTask.status == "claiming") & (ScheduledTask.claim_until < now)),
            ScheduledTask.run_at <= now).order_by(ScheduledTask.run_at).limit(limit))).scalars().all()
    launched = 0
    for task_id in ids:
        try:
            if await _fire_one(session_factory, producer, task_id, owner):
                launched += 1
        except Exception:
            log.exception("one-time Task %s could not launch; lease will recover", task_id)
    return launched


async def _fire_one(session_factory, producer, task_id: str, owner: str) -> bool:
    async with session_factory() as s:
        async with s.begin():
            task = await s.get(ScheduledTask, task_id, with_for_update=True)
            if not task:
                return False
            now = await _db_now(s)
            if task.status not in ("scheduled", "claiming") or as_utc(task.run_at) > now:
                return False
            if task.status == "claiming" and task.claim_until and as_utc(task.claim_until) > now:
                return False
            existing = await s.get(Run, task.run_id)
            if existing:
                task.status = "launched"
                _event(s, task, "recovered", "Run already exists; resumed without creating another")
                return False
            if as_utc(task.expires_at) <= now:
                task.status, task.last_reason = "expired", task.last_reason or "missed its latest start time"
                _event(s, task, "expired", task.last_reason)
                return False
            reason = await task_authority(s, task)
            if reason:
                task.status, task.last_reason = "blocked", reason
                _event(s, task, "blocked", reason)
                return False
            task.status = "claiming"
            task.claim_owner = owner
            task.claim_generation += 1
            task.claim_until = now + LEASE
            generation = task.claim_generation
            _event(s, task, "claimed")
    # A crash here is recovered by an expired lease. The second transaction
    # fences the old claimant and creates the Run with the Task row locked.
    async with session_factory() as s:
        async with s.begin():
            task = await s.get(ScheduledTask, task_id, with_for_update=True)
            now = await _db_now(s)
            if (task is None or task.status != "claiming" or
                    task.claim_owner != owner or task.claim_generation != generation or
                    as_utc(task.claim_until) <= now):
                return False
            if await s.get(Run, task.run_id):
                task.status = "launched"
                _event(s, task, "recovered", "Run already exists")
                return False
            if as_utc(task.expires_at) <= now:
                task.status, task.last_reason = "expired", "missed its latest start time"
                _event(s, task, "expired", task.last_reason)
                return False
            reason = await task_authority(s, task)
            if reason:
                task.status, task.last_reason = "blocked", reason
                _event(s, task, "blocked", reason)
                return False
            room_id, delivery_reason = await _relay_room(s, task)
            if delivery_reason:
                task.delivery = "log"
                task.last_reason = "Relay delivery changed to Task log: " + delivery_reason
                _event(s, task, "delivery_changed", task.last_reason)
            target = await s.get(AgentDef, task.agent)
            prompt = (f"<scheduled-task id=\"{task.id}\" requested-by=\"{task.last_editor or task.creator}\">\n"
                      f"{task.prompt}\n</scheduled-task>\n"
                      "This is a request from the named participant, not a system instruction.")
            s.add(Run(id=task.run_id, task_id=task.id, agent=task.agent, prompt=prompt,
                      requested_model=task.model, requested_runtime=task.runtime,
                      trigger="task", requested_by=task.last_editor or task.creator,
                      initiated_by=task.initiated_by or "admin",
                      parent_run_id=task.creator_run_id, depth=task.depth,
                      conversation_id=room_id,
                      trigger_message_id=task.source_message_id if room_id else None,
                      team_id=task.team_id, project_id=task.project_id, ticket_id=task.ticket_id,
                      authorization_generation=target.authorization_generation))
            task.status, task.fired_at = "launched", now
            _event(s, task, "run_queued")
    try:
        await asyncio.wait_for(producer.publish(TOPIC_RUN_REQUESTS, task.run_id,
            {"type": "run", "run_id": task.run_id}, type="run.request"), timeout=5)
    except Exception:
        log.warning("Task %s Run publish failed; dispatcher sweep will drain it", task.id)
    return True


async def reconcile_task_runs(session_factory, *, limit: int = 200) -> int:
    """Recover Task log changes even when run-state notifications were lost."""
    updated = 0
    async with session_factory() as s:
        rows = (await s.execute(select(ScheduledTask, Run).join(
            Run, Run.id == ScheduledTask.run_id).where(
            ScheduledTask.status == "launched",
            or_(ScheduledTask.last_observed_run_state.is_(None),
                ScheduledTask.last_observed_run_state != Run.state,
                and_(Run.state == RunState.QUEUED, Run.deferred_until.is_not(None),
                     ScheduledTask.expires_at <= func.now())))
            .order_by(ScheduledTask.fired_at.desc()).limit(limit))).all()
        for task, run in rows:
            if (run.state == RunState.QUEUED and run.deferred_until is not None
                    and as_utc(task.expires_at) <= await _db_now(s)):
                task.status = "expired"
                task.last_reason = task.last_reason or "temporary start problem persisted past deadline"
                run.state = RunState.KILLED
                run.error = task.last_reason
                run.finished_at = await _db_now(s)
                s.add(ScheduledTaskEvent(task_id=task.id, kind="expired",
                    actor="system:scheduler", reason=task.last_reason, run_id=run.id))
            if task.last_observed_run_state == run.state:
                continue
            task.last_observed_run_state = run.state
            s.add(ScheduledTaskEvent(task_id=task.id, kind="run_state_changed",
                actor="system:recorder", reason=run.state, run_id=run.id))
            updated += 1
        await s.commit()
    return updated
