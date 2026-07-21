import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import (ANNOTATE_ROLES, INVOKE_ROLES, READ_ROLES,
                                     require_admin, require_role)
from agentplatform.db import ACTIVE_STATES, Run, SecretAccess, TranscriptEvent
from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.materialize import materialize_run

log = logging.getLogger("runs")

router = APIRouter()

class RunIn(BaseModel):
    agent: str
    prompt: str

class AnnotateIn(BaseModel):
    summary: str | None = None
    tags: list[str] | None = None

def _summary(r: Run) -> dict:
    return {"id": r.id, "agent": r.agent, "state": r.state, "trigger": r.trigger,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "summary": r.summary, "tags": r.tags or []}

@router.post("/api/runs")
async def create_run(request: Request, body: RunIn,
                     principal: str = Depends(require_role(*INVOKE_ROLES))):
    info = request.app.state.agent_store.get(body.agent)
    if info is None: raise HTTPException(404, "unknown agent")
    if info.error is not None: raise HTTPException(409, "agent quarantined")
    # Agent-invokes-agent: when the caller authenticated with a per-run token,
    # this run is a child in that run's chain. Depth is derived from the parent
    # run (looked up by the token's run_id), not the request body, so an agent
    # can't reset its own depth to dodge the loop guard.
    parent_run_id = getattr(request.state, "api_key_run_id", None)
    trigger, depth = "manual", 0
    if parent_run_id:
        async with request.app.state.session_factory() as s:
            parent = await s.get(Run, parent_run_id)
        if parent is not None:
            trigger, depth = "agent", (parent.depth or 0) + 1
            if depth > request.app.state.settings.max_run_chain_depth:
                raise HTTPException(429, "run-chain depth limit exceeded")
    # Synchronous command: materialize the run now (DB-first) and return its id.
    # (Async triggers — webhooks, schedules, connectors — go through run.inbound.)
    run_id = uuid.uuid4().hex
    await materialize_run(request.app.state.session_factory, request.app.state.producer, {
        "run_id": run_id, "agent": body.agent, "prompt": body.prompt,
        "trigger": trigger, "requested_by": principal,
        "parent_run_id": parent_run_id if trigger == "agent" else None, "depth": depth,
    })
    return {"id": run_id, "state": "queued"}

@router.get("/api/runs", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=500),
                    tag: str | None = None, needs_summary: bool = False):
    async with request.app.state.session_factory() as s:
        # Over-fetch then filter in Python (JSON tag membership isn't portable
        # across sqlite/postgres); fine at this scale.
        rows = list((await s.execute(
            select(Run).order_by(Run.created_at.desc()).limit(500))).scalars())
    if needs_summary:
        rows = [r for r in rows if not r.summary]
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    return [_summary(r) for r in rows[:limit]]

@router.get("/api/tags", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_tags(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Run.tags))).scalars()
    seen: set[str] = set()
    for t in rows:
        seen.update(t or [])
    return sorted(seen)

@router.get("/api/runs/{run_id}", dependencies=[Depends(require_role(*READ_ROLES))])
async def get_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        d = _summary(run)
        granted = (await s.execute(select(SecretAccess.secret)
                   .where(SecretAccess.run_id == run_id))).scalars().all()
        d.update({"prompt": run.prompt, "exit_code": run.exit_code, "error": run.error,
                  "tokens_in": run.tokens_in, "tokens_out": run.tokens_out,
                  "tool_calls": run.tool_calls, "secrets_granted": sorted(set(granted)),
                  "parent_run_id": run.parent_run_id, "depth": run.depth or 0,
                  "requested_by": run.requested_by,
                  "started_at": run.started_at.isoformat() if run.started_at else None,
                  "finished_at": run.finished_at.isoformat() if run.finished_at else None})
        return d

@router.post("/api/runs/{run_id}/annotate", dependencies=[Depends(require_role(*ANNOTATE_ROLES))])
async def annotate_run(request: Request, run_id: str, body: AnnotateIn):
    """Set a run's summary and/or tags. Used by the run-summarizer system
    agent (with its API key) and available to any operator+."""
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        if body.summary is not None:
            run.summary = body.summary
        if body.tags is not None:
            run.tags = body.tags
        await s.commit()
    return {"ok": True, "id": run_id}

@router.get("/api/runs/{run_id}/events", dependencies=[Depends(require_admin)])
async def run_events(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(TranscriptEvent)
                .where(TranscriptEvent.run_id == run_id).order_by(TranscriptEvent.seq))).scalars()
        return [e.payload for e in rows]

@router.post("/api/runs/{run_id}/kill", dependencies=[Depends(require_admin)])
async def kill_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        if run.state not in ACTIVE_STATES: raise HTTPException(409, "run is terminal")
    await request.app.state.producer.publish(TOPIC_RUN_REQUESTS, run_id,
                                             {"type": "cancel", "run_id": run_id},
                                             type="run.request")
    return {"ok": True}
