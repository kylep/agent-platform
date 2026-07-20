import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.auth import require_role
from agentplatform.db import Run
from agentplatform.events import TOPIC_RUN_REQUESTS

log = logging.getLogger("webhooks")

router = APIRouter()


@router.post("/api/webhooks/{agent}", status_code=202)
async def webhook(request: Request, agent: str, principal: str = Depends(require_role("operator"))):
    """External trigger: an operator+ caller (typically an `ap_` API key) fires
    `{agent}` with the request body as prompt context. Queuing/backpressure
    come from the normal run.requests → dispatcher path."""
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
    run = Run(agent=agent, trigger="webhook", requested_by=principal, prompt=prompt)
    async with st.session_factory() as s:
        s.add(run); await s.commit()
    try:
        await st.producer.publish(TOPIC_RUN_REQUESTS, run.id, {"type": "run", "run_id": run.id})
    except Exception:
        log.warning("publish failed for webhook run %s; sweep will drain it", run.id)
    return {"id": run.id, "state": run.state}
