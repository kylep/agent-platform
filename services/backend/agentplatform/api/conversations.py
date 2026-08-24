import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from agentplatform.api.auth import INVOKE_ROLES, READ_ROLES, require_role
from agentplatform.connectors import CONNECTORS, IMPLEMENTED
from agentplatform.conversation import continue_conversation
from agentplatform.db import Conversation, Run

log = logging.getLogger("conversations")

from agentplatform.api import schemas as S
router = APIRouter()


class ConversationIn(BaseModel):
    connector: str = "web"
    agent: str
    title: str | None = None


class MessageIn(BaseModel):
    text: str


def _view(c: Conversation) -> dict:
    return {"id": c.id, "connector": c.connector, "external_ref": c.external_ref,
            "agent": c.agent, "title": c.title, "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None,
            "updated_at": c.updated_at.isoformat() if c.updated_at else None}


def _sender(requested_by: str | None) -> str:
    """Human-facing sender of a turn, from Run.requested_by. Connector turns are
    stored as `connector:<name>:<user>`; show just the external user. Web/API
    turns carry the principal name (an admin/operator) as-is."""
    if requested_by and requested_by.startswith("connector:"):
        parts = requested_by.split(":", 2)
        return parts[2] if len(parts) == 3 and parts[2] else parts[1]
    return requested_by or "unknown"


@router.get("/api/connectors", response_model=list[S.Connector], dependencies=[Depends(require_role(*READ_ROLES))])
async def list_connectors():
    return CONNECTORS


@router.post("/api/conversations", status_code=201, response_model=S.ConversationView,
             dependencies=[Depends(require_role(*INVOKE_ROLES))])
async def create_conversation(request: Request, body: ConversationIn):
    if body.connector not in IMPLEMENTED:
        raise HTTPException(422, f"connector '{body.connector}' is not implemented")
    # Reload first: an agent created moments ago in the UI must be startable.
    await request.app.state.agent_store.reload()
    info = request.app.state.agent_store.get(body.agent)
    if info is None:
        raise HTTPException(404, "unknown agent")
    if info.error is not None:
        raise HTTPException(409, "agent quarantined")
    conv = Conversation(connector=body.connector, agent=body.agent,
                        title=body.title or f"Conversation with {body.agent}")
    async with request.app.state.session_factory() as s:
        s.add(conv); await s.commit()
        return _view(conv)


@router.get("/api/conversations", response_model=list[S.ConversationView], dependencies=[Depends(require_role(*READ_ROLES))])
async def list_conversations(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Conversation)
                .order_by(Conversation.updated_at.desc()))).scalars().all()
    return [_view(c) for c in rows]


@router.get("/api/conversations/{conversation_id}", response_model=S.ConversationDetail,
            dependencies=[Depends(require_role(*READ_ROLES))])
async def get_conversation(request: Request, conversation_id: str):
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(404, "unknown conversation")
        turns = (await s.execute(select(Run).where(Run.conversation_id == conversation_id)
                 .order_by(Run.created_at))).scalars().all()
    d = _view(conv)
    d["turns"] = [{"run_id": t.id, "user_message": t.user_message, "result": t.result,
                   "state": t.state, "sender": _sender(t.requested_by),
                   "created_at": t.created_at.isoformat() if t.created_at else None}
                  for t in turns]
    return d


class ConversationPatch(BaseModel):
    title: str = Field(min_length=1, max_length=256)


@router.patch("/api/conversations/{conversation_id}", response_model=S.ConversationView,
              dependencies=[Depends(require_role(*INVOKE_ROLES))])
async def rename_conversation(request: Request, conversation_id: str, body: ConversationPatch):
    """Rename a conversation. The title is a local display label (it does not
    touch the external channel), so any type — including Discord — is renamable."""
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(404, "unknown conversation")
        conv.title = body.title.strip()
        await s.commit()
        return _view(conv)


@router.delete("/api/conversations/{conversation_id}", response_model=S.OkId,
               dependencies=[Depends(require_role(*INVOKE_ROLES))])
async def delete_conversation(request: Request, conversation_id: str):
    """Permanently delete a web conversation and its turns. Connector-owned
    conversations (Discord etc.) are not deletable here — their lifecycle
    belongs to the external channel, and a delete would just be recreated on
    the next inbound message."""
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(404, "unknown conversation")
        if conv.connector != "web":
            raise HTTPException(409, f"{conv.connector} conversations are managed by "
                                     "their channel and can't be deleted here")
        # Turns are runs; detach them (keep the run history) then drop the thread.
        for t in (await s.execute(select(Run).where(
                Run.conversation_id == conversation_id))).scalars().all():
            t.conversation_id = None
        await s.delete(conv)
        await s.commit()
    return {"ok": True, "id": conversation_id}


@router.post("/api/conversations/{conversation_id}/messages", response_model=S.MessageAccepted,
             dependencies=[Depends(require_role(*INVOKE_ROLES))])
async def post_message(request: Request, conversation_id: str, body: MessageIn,
                       principal: str = Depends(require_role(*INVOKE_ROLES))):
    """Continue the conversation: create the next turn (a run). Returns the run
    id; stream it via /api/runs/{id}/tail or poll the conversation."""
    run_id = await continue_conversation(
        request.app.state.session_factory, request.app.state.producer,
        conversation_id, body.text, principal)
    if run_id is None:
        raise HTTPException(409, "conversation is closed, missing, or has a turn in progress")
    return {"run_id": run_id}
