"""Scheduled Jobs API — first-class recurring tasks (1:many with agents).

A job binds a cron + prompt to ONE action; the scheduler fires it when due.
Unlike an agent's own declared entrypoint crons (part of its definition,
read-only here), jobs are created and tuned from the UI.

Two actions, exactly one per job. `agent` runs that agent with the prompt, and
`Run Now` materializes that run immediately. `relay_channel` posts the prompt
into a Relay room as the platform (docs/design/19) — the #standup summons is
one of these — and `Run Now` posts it immediately instead. The exclusivity is
validated here rather than in the model because "neither" and "both" are both
user input, and a 422 that names the problem beats a NOT NULL violation.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform.api.auth import require_admin
from agentplatform.db import ScheduledJob
from agentplatform.materialize import materialize_run
from agentplatform.relay import SCHEDULER_AUTHOR
from agentplatform.relay_store import summon_channel
from agentplatform.scheduler import is_valid_cron, is_valid_timezone

from agentplatform.api import schemas as S
router = APIRouter(dependencies=[Depends(require_admin)])


def _view(j: ScheduledJob) -> dict:
    return {"id": j.id, "name": j.name, "agent": j.agent, "cron": j.cron,
            "relay_channel": j.relay_channel,
            "timezone": j.timezone or "", "prompt": j.prompt, "enabled": j.enabled,
            "last_fire": j.last_fire.isoformat() if j.last_fire else None,
            "next_fire": j.next_fire.isoformat() if j.next_fire else None}


class JobIn(BaseModel):
    name: str
    cron: str
    prompt: str
    # Exactly one of these: run an agent, or post into a Relay channel.
    agent: str | None = None
    relay_channel: str | None = None
    timezone: str = ""          # IANA zone; empty = UTC


class JobPatch(BaseModel):
    name: str | None = None
    agent: str | None = None
    cron: str | None = None
    timezone: str | None = None
    prompt: str | None = None
    enabled: bool | None = None


async def _check(request: Request, *, cron: str | None, agent: str | None,
                 timezone: str | None = None) -> None:
    if cron is not None and not is_valid_cron(cron):
        raise HTTPException(422, "invalid cron expression (5 fields)")
    if timezone is not None and not is_valid_timezone(timezone):
        raise HTTPException(422, f"unknown timezone: {timezone!r} "
                                 f"(IANA name, e.g. America/Toronto)")
    if agent is not None:
        await request.app.state.agent_store.reload()
        info = request.app.state.agent_store.get(agent)
        if info is None:
            raise HTTPException(422, f"unknown agent: {agent}")


@router.get("/api/jobs", response_model=list[S.JobView])
async def list_jobs(request: Request):
    async with request.app.state.session_factory() as s:
        jobs = (await s.execute(select(ScheduledJob).order_by(ScheduledJob.name))).scalars().all()
        return [_view(j) for j in jobs]


@router.post("/api/jobs", status_code=201, response_model=S.JobView)
async def create_job(request: Request, body: JobIn):
    if bool(body.agent) == bool(body.relay_channel):
        raise HTTPException(422, "a job needs exactly one of agent or relay_channel")
    # `_check` resolves the AGENT but never the room: a channel can be archived
    # or renamed long after the job is written, so the only honest answer about
    # where it posts is the one the scheduler gets at fire time — and a
    # create-time check would promise a guarantee it cannot keep.
    await _check(request, cron=body.cron, agent=body.agent, timezone=body.timezone)
    async with request.app.state.session_factory() as s:
        job = ScheduledJob(name=body.name, agent=body.agent, cron=body.cron,
                           relay_channel=body.relay_channel,
                           timezone=body.timezone, prompt=body.prompt)
        s.add(job)
        await s.commit()
        return _view(job)


@router.patch("/api/jobs/{job_id}", response_model=S.JobView)
async def edit_job(request: Request, job_id: str, body: JobPatch):
    await _check(request, cron=body.cron, agent=body.agent, timezone=body.timezone)
    async with request.app.state.session_factory() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        # A job's ACTION is fixed at creation. Editing a relay job onto an agent
        # would leave it holding both, and "exactly one" has to stay true of the
        # row, not only of the request that created it.
        if body.agent is not None and job.relay_channel:
            raise HTTPException(422, "a relay job has no agent; delete it and "
                                     "create an agent job instead")
        for field in ("name", "agent", "cron", "timezone", "prompt", "enabled"):
            val = getattr(body, field)
            if val is not None:
                setattr(job, field, val)
        # A changed cron (or zone — same wall clock, different instant)
        # re-arms next_fire from the scheduler's next tick.
        if body.cron is not None or body.timezone is not None:
            job.next_fire = None
        await s.commit()
        return _view(job)


@router.delete("/api/jobs/{job_id}", status_code=204)
async def delete_job(request: Request, job_id: str):
    async with request.app.state.session_factory() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        await s.delete(job)
        await s.commit()


@router.post("/api/jobs/{job_id}/run", response_model=S.JobRunAccepted)
async def run_job_now(request: Request, job_id: str, principal: str = Depends(require_admin)):
    """Run Now: do immediately whatever this job's cron would have done —
    materialize a run, or post its prompt into its Relay channel."""
    async with request.app.state.session_factory() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        agent, prompt, channel = job.agent, job.prompt, job.relay_channel
    if channel:
        # Authored `system:scheduler`, exactly as the tick would write it: a
        # summons a human could distinguish from the 09:00 one would be a
        # different message, and the room's agents would answer it differently.
        msg = await summon_channel(request.app.state.session_factory,
                                   request.app.state.producer, channel,
                                   author=SCHEDULER_AUTHOR, body=prompt)
        if msg is None:
            raise HTTPException(409, f"no live relay channel #{channel}")
        return {"id": msg.id, "agent": None, "relay_channel": channel}
    # The soft off-switch (docs/design/15) applies to every way a run starts,
    # and Run Now is one of them — a disabled agent gets no work queued in its
    # name, whatever its jobs say.
    await request.app.state.agent_store.reload()
    info = request.app.state.agent_store.get(agent)
    if info is not None and not info.enabled:
        raise HTTPException(409, "agent is disabled")
    run_id = uuid.uuid4().hex
    await materialize_run(request.app.state.session_factory, request.app.state.producer, {
        "run_id": run_id, "agent": agent, "prompt": prompt,
        "trigger": "manual", "requested_by": f"{principal} (job:{job_id})",
        "initiated_by": principal,
    })
    return {"id": run_id, "agent": agent, "relay_channel": None}
