import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select

from agentplatform.api.auth import READ_ROLES, require_admin, require_role
from agentplatform.db import Run, RunState, utcnow
from agentplatform.events import TOPIC_RUN_REQUESTS

log = logging.getLogger("dlq")

router = APIRouter()


def _view(r: Run) -> dict:
    return {"id": r.id, "agent": r.agent, "trigger": r.trigger, "error": r.error,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "finished_at": r.finished_at.isoformat() if r.finished_at else None}


@router.get("/api/dlq", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_dlq(request: Request):
    """Runs that landed in the dead-letter queue (launch failed after retries)."""
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Run).where(Run.state == RunState.DLQ)
                .order_by(Run.finished_at.desc()))).scalars().all()
    return [_view(r) for r in rows]


@router.post("/api/dlq/{run_id}/retry", dependencies=[Depends(require_admin)])
async def retry_dlq(request: Request, run_id: str):
    """Re-queue a dead-lettered run: reset it to queued, clear the failure, and
    republish its run-request. The dispatcher's handle() is idempotent, and its
    queued-sweep drains it even if Kafka is down."""
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        if run.state != RunState.DLQ:
            raise HTTPException(409, "run is not in the DLQ")
        run.state = RunState.QUEUED
        run.error = None
        run.finished_at = None
        run.started_at = None
        await s.commit()
    try:
        await request.app.state.producer.publish(TOPIC_RUN_REQUESTS, run_id,
                                                 {"type": "run", "run_id": run_id})
    except Exception:
        log.warning("publish failed for retried run %s; sweep will drain it", run_id)
    return {"ok": True, "id": run_id, "state": RunState.QUEUED}


@router.post("/api/dlq/{run_id}/discard", dependencies=[Depends(require_admin)])
async def discard_dlq(request: Request, run_id: str):
    """Acknowledge and drop a dead-lettered run: mark it failed with a note so it
    leaves the DLQ view without being retried."""
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        if run.state != RunState.DLQ:
            raise HTTPException(409, "run is not in the DLQ")
        run.state = RunState.FAILED
        run.error = "discarded from DLQ"
        if run.finished_at is None:
            run.finished_at = utcnow()
        await s.commit()
    return {"ok": True, "id": run_id, "state": RunState.FAILED}
