import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.auth import require_role
from agentplatform.events import TOPIC_RUN_INBOUND

log = logging.getLogger("webhooks")

from agentplatform.api import schemas as S
router = APIRouter()


@router.post("/api/webhooks/{path}", status_code=202, response_model=S.RunAccepted)
async def webhook(request: Request, path: str, principal: str = Depends(require_role("operator"))):
    """External async trigger: an operator+ caller fires the agent that
    DECLARES `{path}` in its entrypoints' `webhooks:` list (docs/design/10) —
    an undeclared path doesn't exist, so an agent can't be webhook-fired
    unless its definition opted in. The request body becomes prompt context.
    Event-sourced: we validate the command, then produce a `run.requested`
    event to `run.inbound`; the ingest consumer materializes the run. The
    pre-assigned id is returned so the caller can follow the run."""
    st = request.app.state
    await st.agent_store.reload()   # a path declared moments ago must count
    info = next((a for a in st.agent_store.list() if path in a.webhook_paths()), None)
    if info is None:
        raise HTTPException(404, "no agent declares this webhook path")
    if info.error is not None:
        raise HTTPException(409, "agent quarantined")
    if not info.enabled:
        # Declaring the path still means the path exists (404 would be a lie
        # about the definition) — the agent is just switched off.
        raise HTTPException(409, "agent is disabled")
    agent = info.name
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
