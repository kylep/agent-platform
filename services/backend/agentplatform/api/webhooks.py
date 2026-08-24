import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request

from agentplatform import webhooksecrets
from agentplatform.agentdefs import WebhookEntry
from agentplatform.agents import AgentInfo
from agentplatform.api.auth import authenticate, role_allows
from agentplatform.events import TOPIC_RUN_INBOUND

log = logging.getLogger("webhooks")

from agentplatform.api import schemas as S
router = APIRouter()

# Who a platform API key must be to fire any webhook — unchanged from before
# design/16, and still accepted on every path whatever its auth mode says.
KEY_ROLES = ("operator",)


def _declaring(store, path: str) -> tuple[AgentInfo, WebhookEntry] | tuple[None, None]:
    """The agent that DECLARES `path`, and the entry that declares it. An
    undeclared path doesn't exist, so an agent can't be webhook-fired unless
    its definition opted in (docs/design/10)."""
    for info in store.list():
        for entry in info.entrypoints.webhooks:
            if entry.path == path:
                return info, entry
    return None, None


async def _authorize(request: Request, path: str) -> tuple[AgentInfo, str]:
    """Authenticate an inbound webhook and resolve which agent it fires.

    Two doors (docs/design/16). A platform API key with the operator role is
    the original one and opens every declared path. The second exists because
    GitHub, IFTTT and a curl from anywhere cannot hold a platform key: a path
    declaring `auth: "secret"` also accepts the shared secret in the
    `X-AP-Webhook-Secret` header, compared in constant time against a salted
    hash that never lived on the definition.

    Order matters for more than authorization. The mode is a property of the
    PATH, so the path has to be resolved before the secret door can be judged —
    and resolving first would otherwise turn this endpoint into a directory of
    declared webhooks for anonymous callers. So an unauthenticated caller gets
    the same 401 whether or not the path exists, and only a caller who already
    authenticated learns the difference (404).
    """
    st = request.app.state
    await st.agent_store.reload()   # a path declared moments ago must count
    info, entry = _declaring(st.agent_store, path)

    ident = await authenticate(request)
    if ident is not None and role_allows(ident[1], KEY_ROLES):
        if info is None:
            raise HTTPException(404, "no agent declares this webhook path")
        return info, ident[0]

    if info is not None and entry.auth == "secret":
        presented = request.headers.get(webhooksecrets.WEBHOOK_SECRET_HEADER, "")
        async with st.session_factory() as s:
            ok = await webhooksecrets.verify(s, info.name, path, presented)
        if ok is None:
            # Mode says `secret`, no secret is set — a rollback restored the
            # mode, or an edit stopped half-way. Fail CLOSED and name the
            # misconfiguration: silently falling back to key-only auth would
            # leave an operator convinced the path was open when it was not.
            raise HTTPException(503, "this webhook is configured for secret auth "
                                     "but no secret is set for it")
        if ok:
            # Not a platform principal: attribute the run to the path that was
            # authenticated, never to anything the caller claims about itself.
            return info, f"webhook:{path}"

    # Nothing opened either door. An authenticated caller (a reader key, say)
    # gets the honest 403; an anonymous one gets 401 and learns nothing.
    raise HTTPException(403 if ident is not None else 401)


@router.post("/api/webhooks/{path}", status_code=202, response_model=S.RunAccepted)
async def webhook(request: Request, path: str):
    """External async trigger: fires the agent that DECLARES `{path}` in its
    entrypoints' `webhooks:` list (docs/design/10) — an undeclared path doesn't
    exist, so an agent can't be webhook-fired unless its definition opted in.

    Two ways to authenticate (docs/design/16): a platform API key with the
    operator role, which works on every declared path; or, on a path whose
    entry declares `auth: "secret"`, the shared secret in the
    `X-AP-Webhook-Secret` header — the door for callers that cannot hold a
    platform key.

    The request body becomes prompt context. Event-sourced: we validate the
    command, then produce a `run.requested` event to `run.inbound`; the ingest
    consumer materializes the run. The pre-assigned id is returned so the
    caller can follow the run."""
    st = request.app.state
    info, principal = await _authorize(request, path)
    # State checks come AFTER auth: whether an agent is quarantined or switched
    # off is platform state, and an unauthenticated caller has no business
    # probing it.
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
