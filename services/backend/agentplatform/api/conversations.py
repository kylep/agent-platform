import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform.api.auth import INVOKE_ROLES, READ_ROLES, require_role
from agentplatform.connectors import CONNECTORS, IMPLEMENTED
from agentplatform.conversation import continue_conversation
from agentplatform.db import Conversation, Run, utcnow

log = logging.getLogger("conversations")

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


@router.get("/api/connectors", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_connectors():
    return CONNECTORS


@router.post("/api/conversations", status_code=201,
             dependencies=[Depends(require_role(*INVOKE_ROLES))])
async def create_conversation(request: Request, body: ConversationIn):
    if body.connector not in IMPLEMENTED:
        raise HTTPException(422, f"connector '{body.connector}' is not implemented")
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


@router.get("/api/conversations", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_conversations(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Conversation)
                .order_by(Conversation.updated_at.desc()))).scalars().all()
    return [_view(c) for c in rows]


@router.get("/api/conversations/{conversation_id}",
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
                   "state": t.state,
                   "created_at": t.created_at.isoformat() if t.created_at else None}
                  for t in turns]
    return d


@router.delete("/api/conversations/{conversation_id}",
               dependencies=[Depends(require_role(*INVOKE_ROLES))])
async def close_conversation(request: Request, conversation_id: str):
    async with request.app.state.session_factory() as s:
        conv = await s.get(Conversation, conversation_id)
        if conv is None:
            raise HTTPException(404, "unknown conversation")
        conv.status = "closed"
        conv.updated_at = utcnow()
        await s.commit()
    return {"ok": True, "id": conversation_id, "status": "closed"}


@router.post("/api/conversations/{conversation_id}/messages",
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
