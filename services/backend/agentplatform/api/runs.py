from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import require_admin
from agentplatform.db import ACTIVE_STATES, Run, TranscriptEvent
from agentplatform.events import TOPIC_RUN_REQUESTS

router = APIRouter()

class RunIn(BaseModel):
    agent: str
    prompt: str

def _summary(r: Run) -> dict:
    return {"id": r.id, "agent": r.agent, "state": r.state, "trigger": r.trigger,
            "created_at": r.created_at.isoformat() if r.created_at else None}

@router.post("/api/runs")
async def create_run(request: Request, body: RunIn, principal: str = Depends(require_admin)):
    info = request.app.state.agent_store.get(body.agent)
    if info is None: raise HTTPException(404, "unknown agent")
    if info.error is not None: raise HTTPException(409, "agent quarantined")
    run = Run(agent=body.agent, trigger="manual", requested_by=principal, prompt=body.prompt)
    async with request.app.state.session_factory() as s:
        s.add(run); await s.commit()
    await request.app.state.producer.publish(TOPIC_RUN_REQUESTS, run.id,
                                             {"type": "run", "run_id": run.id})
    return {"id": run.id, "state": run.state}

@router.get("/api/runs", dependencies=[Depends(require_admin)])
async def list_runs(request: Request, limit: int = 50):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Run).order_by(Run.created_at.desc()).limit(limit))).scalars()
        return [_summary(r) for r in rows]

@router.get("/api/runs/{run_id}", dependencies=[Depends(require_admin)])
async def get_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        d = _summary(run)
        d.update({"prompt": run.prompt, "exit_code": run.exit_code, "error": run.error,
                  "tokens_in": run.tokens_in, "tokens_out": run.tokens_out,
                  "tool_calls": run.tool_calls,
                  "started_at": run.started_at.isoformat() if run.started_at else None,
                  "finished_at": run.finished_at.isoformat() if run.finished_at else None})
        return d

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
                                             {"type": "cancel", "run_id": run_id})
    return {"ok": True}
