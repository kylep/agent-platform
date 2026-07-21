import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.auth import require_role
from agentplatform.events import TOPIC_RUN_INBOUND

log = logging.getLogger("webhooks")

router = APIRouter()


@router.post("/api/webhooks/{agent}", status_code=202)
async def webhook(request: Request, agent: str, principal: str = Depends(require_role("operator"))):
    """External async trigger: an operator+ caller fires `{agent}` with the
    request body as prompt context. This is **event-sourced** — we validate the
    command, then produce a `run.requested` event to `run.inbound`; the ingest
    consumer materializes the run. The pre-assigned id is returned so the caller
    can follow the run once it lands."""
    st = request.app.state
    info = st.agent_store.get(agent)
    if info is None:
        raise HTTPException(404, "unknown agent")
    if info.error is not None:
        raise HTTPException(409, "agent quarantined")
    try:
        payload = await request.json()
    except Exception:
        payload = None
    body = json.dumps(payload, indent=2) if payload is not None else (await request.body()).decode(errors="replace")
    prompt = f"Triggered by webhook. Payload:\n\n{body}" if body.strip() else "Triggered by webhook (no payload)."
    run_id = uuid.uuid4().hex
    await st.producer.publish(TOPIC_RUN_INBOUND, run_id, {
        "run_id": run_id, "agent": agent, "prompt": prompt,
        "trigger": "webhook", "requested_by": principal,
    }, type="run.requested")
    return {"id": run_id, "state": "accepted"}
