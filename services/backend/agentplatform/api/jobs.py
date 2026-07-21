"""Scheduled Jobs API — first-class recurring tasks (1:many with agents).

A job binds an agent to a cron + prompt; the scheduler fires it when due. Unlike
the manifest `schedule:` field (1:1, read-only here), jobs are created and tuned
from the UI. `Run Now` materializes a run immediately from the job's agent+prompt.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform.api.auth import require_admin
from agentplatform.db import ScheduledJob
from agentplatform.materialize import materialize_run
from agentplatform.scheduler import is_valid_cron

router = APIRouter(dependencies=[Depends(require_admin)])


def _view(j: ScheduledJob) -> dict:
    return {"id": j.id, "name": j.name, "agent": j.agent, "cron": j.cron,
            "prompt": j.prompt, "enabled": j.enabled,
            "last_fire": j.last_fire.isoformat() if j.last_fire else None,
            "next_fire": j.next_fire.isoformat() if j.next_fire else None}


class JobIn(BaseModel):
    name: str
    agent: str
    cron: str
    prompt: str


class JobPatch(BaseModel):
    name: str | None = None
    agent: str | None = None
    cron: str | None = None
    prompt: str | None = None
    enabled: bool | None = None


def _check(request: Request, *, cron: str | None, agent: str | None) -> None:
    if cron is not None and not is_valid_cron(cron):
        raise HTTPException(422, "invalid cron expression (5 fields)")
    if agent is not None:
        request.app.state.agent_store.reload()
        info = request.app.state.agent_store.get(agent)
        if info is None:
            raise HTTPException(422, f"unknown agent: {agent}")


@router.get("/api/jobs")
async def list_jobs(request: Request):
    async with request.app.state.session_factory() as s:
        jobs = (await s.execute(select(ScheduledJob).order_by(ScheduledJob.name))).scalars().all()
        return [_view(j) for j in jobs]


@router.post("/api/jobs", status_code=201)
async def create_job(request: Request, body: JobIn):
    _check(request, cron=body.cron, agent=body.agent)
    async with request.app.state.session_factory() as s:
        job = ScheduledJob(name=body.name, agent=body.agent, cron=body.cron, prompt=body.prompt)
        s.add(job)
        await s.commit()
        return _view(job)


@router.patch("/api/jobs/{job_id}")
async def edit_job(request: Request, job_id: str, body: JobPatch):
    _check(request, cron=body.cron, agent=body.agent)
    async with request.app.state.session_factory() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        for field in ("name", "agent", "cron", "prompt", "enabled"):
            val = getattr(body, field)
            if val is not None:
                setattr(job, field, val)
        # A changed cron re-arms next_fire from the scheduler's next tick.
        if body.cron is not None:
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


@router.post("/api/jobs/{job_id}/run")
async def run_job_now(request: Request, job_id: str, principal: str = Depends(require_admin)):
    """Run Now: materialize a run immediately from the job's agent + prompt."""
    async with request.app.state.session_factory() as s:
        job = await s.get(ScheduledJob, job_id)
        if job is None:
            raise HTTPException(404, "unknown job")
        agent, prompt = job.agent, job.prompt
    run_id = uuid.uuid4().hex
    await materialize_run(request.app.state.session_factory, request.app.state.producer, {
        "run_id": run_id, "agent": agent, "prompt": prompt,
        "trigger": "manual", "requested_by": f"{principal} (job:{job_id})",
    })
    return {"id": run_id, "agent": agent}
