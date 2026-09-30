"""One-time scheduled agent runs. The caller never supplies its own identity."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, or_, select

from agentplatform.agentspec import CODEX_MODELS, KNOWN_MODELS
from agentplatform.api.auth import authenticate, require_admin
from agentplatform.db import (AgentDef, Conversation, Principal, Run, RunState, ScheduledTask, ScheduledTaskEvent,
                              TaskScheduleGrant, utcnow)
from agentplatform.scheduler import as_utc

router = APIRouter()
TOOL = "mcp__platform__tasks"
DEFAULT_MODELS = {"claude": "claude-sonnet-5-5", "codex": "gpt-6-sol"}


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str
    prompt: str = Field(min_length=1, max_length=20000)
    title: str = Field(default="One-time run", min_length=1, max_length=128)
    run_at: datetime | None = None
    delay_minutes: int | None = Field(default=None, ge=1, le=525600)
    timezone: str = "UTC"
    late_minutes: int = Field(default=60, ge=1, le=1440)
    model: str = ""
    idempotency_key: str | None = Field(default=None, max_length=128)
    source_conversation_id: str | None = None
    source_message_id: str | None = None
    delivery: str = "log"

    @model_validator(mode="after")
    def valid_time(self):
        if (self.run_at is None) == (self.delay_minutes is None):
            raise ValueError("supply exactly one of run_at or delay_minutes")
        if self.run_at is not None and self.run_at.utcoffset() is None:
            raise ValueError("run_at needs an explicit UTC offset")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be a known IANA zone") from None
        if self.delivery not in ("log", "relay"):
            raise ValueError("delivery must be log or relay")
        return self


class TaskPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int
    title: str | None = Field(default=None, min_length=1, max_length=128)
    prompt: str | None = Field(default=None, min_length=1, max_length=20000)
    run_at: datetime | None = None
    timezone: str | None = None
    late_minutes: int | None = Field(default=None, ge=1, le=1440)
    model: str | None = None


class Caller(BaseModel):
    name: str
    agent: str | None = None
    run_id: str | None = None


async def caller(request: Request) -> Caller:
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    name, role = ident
    agent = getattr(request.state, "api_key_agent", None)
    if not agent:
        if role != "admin":
            raise HTTPException(403)
        return Caller(name=name)
    run_id = getattr(request.state, "api_key_run_id", None)
    if not run_id:
        raise HTTPException(403, "Tasks need a run-scoped agent identity")
    frozen = getattr(request.state, "frozen_tools", None)
    if frozen is not None:
        granted = frozen
    else:
        async with request.app.state.session_factory() as s:
            row = await s.get(AgentDef, agent)
            granted = row.platform_tools if row else []
    if TOOL not in (granted or []):
        raise HTTPException(403, "Schedule Tasks tool is not granted")
    return Caller(name=name, agent=agent, run_id=run_id)


def _outcome(task: ScheduledTask, run: Run | None) -> str:
    if task.status in ("blocked", "expired", "cancelled"):
        return "didnt_run"
    if task.status == "scheduled":
        return "upcoming"
    if task.status == "claiming":
        return "starting"
    if run is None or run.state == RunState.QUEUED:
        return "starting"
    if run.state in (RunState.DISPATCHED, RunState.RUNNING):
        return "running"
    return "done" if run.state == RunState.SUCCEEDED else "failed"


def view(task: ScheduledTask, run: Run | None = None) -> dict:
    notice = ("The result will appear in the source Relay conversation and the Task log."
              if task.delivery == "relay" else
              "The result will appear in the Task log; no automatic external-chat reply is sent.")
    return {"id": task.id, "title": task.title, "agent": task.agent,
            "prompt": task.prompt, "runtime": task.runtime, "model": task.model,
            "run_at": as_utc(task.run_at).isoformat(),
            "expires_at": as_utc(task.expires_at).isoformat(),
            "timezone": task.timezone, "creator": task.creator,
            "creator_agent": task.creator_agent, "creator_run_id": task.creator_run_id,
            "last_editor": task.last_editor, "depth": task.depth,
            "source_conversation_id": task.source_conversation_id,
            "source_message_id": task.source_message_id, "delivery": task.delivery,
            "team_id": task.team_id, "project_id": task.project_id, "ticket_id": task.ticket_id,
            "delivery_notice": notice,
            "status": task.status, "outcome": _outcome(task, run),
            "run_id": run.id if run else None,
            "run_state": run.state if run else None, "reason": task.last_reason,
            "version": task.version, "created_at": as_utc(task.created_at).isoformat(),
            "fired_at": as_utc(task.fired_at).isoformat() if task.fired_at else None}


async def _target(s, agent: str, creator: Caller, model: str) -> tuple[AgentDef, str]:
    target = await s.get(AgentDef, agent)
    if target is None or not target.enabled or target.system_source:
        raise HTTPException(422, "target agent is missing, disabled, or code-owned")
    if not target.accept_scheduled_tasks:
        raise HTTPException(403, "target does not accept scheduled Tasks")
    if creator.agent and creator.agent != agent:
        source = await s.get(AgentDef, creator.agent)
        link = await s.get(TaskScheduleGrant, (creator.agent, agent))
        if not source or not source.can_invoke and link is None:
            raise HTTPException(403, "creator is not allowed to schedule this agent")
    allowed = {m["id"] for m in (CODEX_MODELS if target.runtime == "codex" else KNOWN_MODELS)}
    if target.model:
        allowed.add(target.model)  # agent definitions allow saved custom models
    if target.backup_runtime == target.runtime and target.backup_model:
        allowed.add(target.backup_model)
    resolved = model or target.model or DEFAULT_MODELS.get(target.runtime, "")
    if not resolved or resolved not in allowed:
        raise HTTPException(422, f"model is not available for {target.runtime}")
    if creator.agent:
        configured = {target.model or DEFAULT_MODELS.get(target.runtime, "")}
        if target.backup_runtime == target.runtime and target.backup_model:
            configured.add(target.backup_model)
        if resolved not in configured:
            raise HTTPException(403, "agents may select only this target's primary or same-runtime backup model")
    return target, resolved


async def _visible(s, task: ScheduledTask, who: Caller):
    if who.agent and who.agent not in (task.creator_agent, task.agent):
        raise HTTPException(404, "unknown Task")


@router.get("/api/tasks/models")
async def task_models(request: Request, agent: str, who: Caller = Depends(caller)):
    async with request.app.state.session_factory() as s:
        target = await s.get(AgentDef, agent)
        if target is None:
            raise HTTPException(404, "unknown agent")
        # A model catalog is not itself authority to schedule the target.
        all_models = list(CODEX_MODELS if target.runtime == "codex" else KNOWN_MODELS)
        for custom in (target.model, target.backup_model if target.backup_runtime == target.runtime else ""):
            if custom and all(m["id"] != custom for m in all_models):
                all_models.append({"id": custom, "label": custom + " — configured custom model"})
        if who.agent:
            allowed = {target.model or DEFAULT_MODELS[target.runtime]}
            if target.backup_runtime == target.runtime and target.backup_model:
                allowed.add(target.backup_model)
            all_models = [m for m in all_models if m["id"] in allowed]
        return {"runtime": target.runtime, "default": target.model or DEFAULT_MODELS[target.runtime],
                "models": all_models}


@router.get("/api/tasks")
async def list_tasks(request: Request, who: Caller = Depends(caller),
                     status: str | None = None, agent: str | None = None,
                     creator: str | None = None, limit: int = Query(50, ge=1, le=200),
                     offset: int = Query(0, ge=0)):
    stmt = select(ScheduledTask, Run).outerjoin(Run, Run.id == ScheduledTask.run_id)
    if who.agent:
        stmt = stmt.where(or_(ScheduledTask.creator_agent == who.agent,
                              ScheduledTask.agent == who.agent))
    if status:
        stmt = stmt.where(ScheduledTask.status == status)
    if agent:
        stmt = stmt.where(ScheduledTask.agent == agent)
    if creator:
        stmt = stmt.where(ScheduledTask.creator == creator)
    stmt = stmt.order_by(ScheduledTask.run_at.desc()).offset(offset).limit(limit)
    async with request.app.state.session_factory() as s:
        return [view(t, r) for t, r in (await s.execute(stmt)).all()]


@router.post("/api/tasks", status_code=201)
async def create_task(request: Request, body: TaskIn, who: Caller = Depends(caller)):
    now = utcnow()
    due = (now + timedelta(minutes=body.delay_minutes) if body.delay_minutes is not None
           else body.run_at.astimezone(timezone.utc))
    if due < now + timedelta(seconds=45) or due > now + timedelta(days=365):
        raise HTTPException(422, "time must be at least one minute and at most one year ahead")
    creator = f"agent:{who.agent}" if who.agent else f"user:{who.name}"
    digest = hashlib.sha256(json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()
    async with request.app.state.session_factory() as s:
        # Serialize the creator's caps and idempotency checks on its stable row.
        if who.agent:
            source = await s.get(AgentDef, who.agent, with_for_update=True)
            if source is None or not source.enabled or TOOL not in (source.platform_tools or []):
                raise HTTPException(403, "creator disabled or Tasks grant revoked")
        else:
            await s.execute(select(Principal.id).where(Principal.name == who.name).with_for_update())
        if body.idempotency_key:
            existing = (await s.execute(select(ScheduledTask).where(
                ScheduledTask.creator == creator,
                ScheduledTask.idempotency_key == body.idempotency_key))).scalar_one_or_none()
            if existing:
                if existing.request_hash != digest:
                    raise HTTPException(409, "idempotency key was used for a different request")
                return view(existing, await s.get(Run, existing.run_id))
        target, model = await _target(s, body.agent, who, body.model)
        parent = await s.get(Run, who.run_id) if who.run_id else None
        source_conversation_id = body.source_conversation_id
        source_message_id = body.source_message_id
        delivery = body.delivery
        if who.agent:
            # An agent's run identity is the only authority for its chat origin.
            # Tool callers cannot nominate another room or another message.
            if source_conversation_id and source_conversation_id != (parent.conversation_id if parent else None):
                raise HTTPException(403, "source conversation must match the current run")
            if source_message_id and source_message_id != (parent.trigger_message_id if parent else None):
                raise HTTPException(403, "source message must match the current run")
            source_conversation_id = parent.conversation_id if parent else None
            source_message_id = parent.trigger_message_id if parent else None
            if source_conversation_id and source_message_id:
                room = await s.get(Conversation, source_conversation_id)
                delivery = "relay" if room and room.home == "relay" else "log"
            else:
                delivery = "log"
        elif delivery == "relay" and not source_conversation_id:
            raise HTTPException(422, "Relay delivery needs a source conversation")
        depth = ((parent.depth or 0) + 1) if parent else 0
        if depth > request.app.state.settings.max_run_chain_depth:
            raise HTTPException(429, "run-chain depth limit; use a recurring Job for recurring work")
        if who.agent:
            day_ago = now - timedelta(days=1)
            created = (await s.execute(select(func.count(ScheduledTask.id)).where(
                ScheduledTask.creator_agent == who.agent,
                ScheduledTask.created_at >= day_ago))).scalar() or 0
            pending = (await s.execute(select(func.count(ScheduledTask.id)).where(
                ScheduledTask.creator_agent == who.agent,
                ScheduledTask.status.in_(("scheduled", "claiming"))))).scalar() or 0
            if created >= 20 or pending >= 50:
                raise HTTPException(429, "Task creation limit reached")
        task = ScheduledTask(title=body.title.strip(), agent=body.agent,
            prompt=body.prompt.strip(), runtime=target.runtime, model=model,
            run_at=due, expires_at=due + timedelta(minutes=body.late_minutes),
            timezone=body.timezone, creator=creator, creator_agent=who.agent,
            creator_run_id=who.run_id, initiated_by=(parent.initiated_by if parent else who.name),
            depth=depth, last_editor=creator, source_conversation_id=source_conversation_id,
            source_message_id=source_message_id, delivery=delivery,
            team_id=parent.team_id if parent else None,
            project_id=parent.project_id if parent else None,
            ticket_id=parent.ticket_id if parent else None,
            run_id=uuid.uuid4().hex, idempotency_key=body.idempotency_key,
            request_hash=digest)
        s.add(task)
        await s.flush()
        s.add(ScheduledTaskEvent(task_id=task.id, kind="created", actor=creator))
        await s.commit()
        return view(task)


@router.get("/api/tasks/grants", dependencies=[Depends(require_admin)])
async def list_grants(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(TaskScheduleGrant).order_by(
            TaskScheduleGrant.creator, TaskScheduleGrant.target))).scalars().all()
        return [{"creator": x.creator, "target": x.target} for x in rows]


class TaskGrantIn(BaseModel):
    creator: str
    target: str


@router.post("/api/tasks/grants", status_code=201, dependencies=[Depends(require_admin)])
async def add_grant(request: Request, body: TaskGrantIn):
    async with request.app.state.session_factory() as s:
        if not await s.get(AgentDef, body.creator) or not await s.get(AgentDef, body.target):
            raise HTTPException(404, "unknown agent")
        if await s.get(TaskScheduleGrant, (body.creator, body.target)) is None:
            s.add(TaskScheduleGrant(creator=body.creator, target=body.target))
            await s.commit()
    return body.model_dump()


@router.delete("/api/tasks/grants/{creator}/{target}",
               dependencies=[Depends(require_admin)])
async def remove_grant(request: Request, creator: str, target: str):
    async with request.app.state.session_factory() as s:
        row = await s.get(TaskScheduleGrant, (creator, target))
        if row:
            await s.delete(row)
            await s.commit()
    return {"ok": True}


@router.get("/api/tasks/report", dependencies=[Depends(require_admin)])
async def task_report(request: Request, days: int = Query(30, ge=1, le=365)):
    since = utcnow() - timedelta(days=days)
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(ScheduledTask, Run).outerjoin(
            Run, Run.id == ScheduledTask.run_id).where(
            ScheduledTask.created_at >= since))).all()
    result = {"window_days": days, "total": len(rows), "upcoming": 0,
              "running": 0, "succeeded": 0, "failed": 0,
              "blocked": 0, "expired": 0, "cancelled": 0,
              "tokens_in": 0, "tokens_out": 0, "by_model": {}}
    for task, run in rows:
        if task.status in ("blocked", "expired", "cancelled"):
            result[task.status] += 1
        elif not run:
            result["upcoming"] += 1
        elif run.state == RunState.SUCCEEDED:
            result["succeeded"] += 1
        elif run.state in (RunState.QUEUED, RunState.DISPATCHED, RunState.RUNNING):
            result["running"] += 1
        else:
            result["failed"] += 1
        if run:
            result["tokens_in"] += run.tokens_in or 0
            result["tokens_out"] += run.tokens_out or 0
            key = run.model or task.model
            result["by_model"][key] = result["by_model"].get(key, 0) + 1
    return result


@router.get("/api/tasks/{task_id}")
async def get_task(request: Request, task_id: str, who: Caller = Depends(caller)):
    async with request.app.state.session_factory() as s:
        task = await s.get(ScheduledTask, task_id)
        if task is None:
            raise HTTPException(404, "unknown Task")
        await _visible(s, task, who)
        return view(task, await s.get(Run, task.run_id))


@router.get("/api/tasks/{task_id}/events")
async def task_events(request: Request, task_id: str, who: Caller = Depends(caller)):
    async with request.app.state.session_factory() as s:
        task = await s.get(ScheduledTask, task_id)
        if task is None:
            raise HTTPException(404, "unknown Task")
        await _visible(s, task, who)
        rows = (await s.execute(select(ScheduledTaskEvent).where(
            ScheduledTaskEvent.task_id == task_id).order_by(ScheduledTaskEvent.created_at,
                                                            ScheduledTaskEvent.id))).scalars().all()
        return [{"kind": e.kind, "actor": e.actor, "reason": e.reason,
                 "run_id": e.run_id, "created_at": as_utc(e.created_at).isoformat()} for e in rows]


@router.patch("/api/tasks/{task_id}")
async def edit_task(request: Request, task_id: str, body: TaskPatch,
                    who: Caller = Depends(caller)):
    async with request.app.state.session_factory() as s:
        task = await s.get(ScheduledTask, task_id, with_for_update=True)
        if task is None:
            raise HTTPException(404, "unknown Task")
        if who.agent and task.creator_agent != who.agent:
            raise HTTPException(403)
        if task.status != "scheduled":
            raise HTTPException(409, "Task is already starting; reload its Run")
        if task.version != body.version:
            raise HTTPException(409, "Task changed; reload the latest version")
        if body.run_at is not None:
            if body.run_at.utcoffset() is None:
                raise HTTPException(422, "run_at needs an explicit offset")
            due = body.run_at.astimezone(timezone.utc)
            if due < utcnow() + timedelta(seconds=45) or due > utcnow() + timedelta(days=365):
                raise HTTPException(422, "time must be at least one minute and at most one year ahead")
            late = body.late_minutes or int((as_utc(task.expires_at)-as_utc(task.run_at)).total_seconds()/60)
            task.run_at, task.expires_at = due, due + timedelta(minutes=late)
        elif body.late_minutes is not None:
            task.expires_at = as_utc(task.run_at) + timedelta(minutes=body.late_minutes)
        if body.model is not None:
            _, task.model = await _target(s, task.agent, who, body.model)
        if body.title is not None:
            task.title = body.title.strip()
        if body.prompt is not None:
            task.prompt = body.prompt.strip()
        if body.timezone is not None:
            try:
                ZoneInfo(body.timezone)
            except (ZoneInfoNotFoundError, ValueError):
                raise HTTPException(422, "unknown timezone") from None
            task.timezone = body.timezone
        task.version += 1
        task.last_editor = f"agent:{who.agent}" if who.agent else f"user:{who.name}"
        s.add(ScheduledTaskEvent(task_id=task.id, kind="edited", actor=task.last_editor))
        await s.commit()
        return view(task)


@router.post("/api/tasks/{task_id}/cancel")
async def cancel_task(request: Request, task_id: str, who: Caller = Depends(caller)):
    async with request.app.state.session_factory() as s:
        task = await s.get(ScheduledTask, task_id, with_for_update=True)
        if task is None:
            raise HTTPException(404, "unknown Task")
        if who.agent and task.creator_agent != who.agent:
            raise HTTPException(403)
        run = await s.get(Run, task.run_id, with_for_update=True)
        if task.status != "scheduled" and not (task.status == "launched" and run and run.state == RunState.QUEUED):
            raise HTTPException(409, "Task already starting or finished; see its Run")
        if run:
            run.state, run.error, run.finished_at = RunState.KILLED, "Task cancelled before dispatch", utcnow()
        task.status = "cancelled"
        task.version += 1
        actor = f"agent:{who.agent}" if who.agent else f"user:{who.name}"
        s.add(ScheduledTaskEvent(task_id=task.id, kind="cancelled", actor=actor,
                                 run_id=run.id if run else None))
        await s.commit()
        return view(task, run)
