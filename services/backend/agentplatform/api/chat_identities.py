"""Chat account metadata. Credentials remain in the existing secret store."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentplatform.api import schemas as S
from agentplatform.api.auth import authenticate, connector_identity, require_admin, require_role
from agentplatform.db import ChatIdentity, RelayBinding

router = APIRouter()


class IdentityStatusIn(BaseModel):
    status: Literal["active", "disabled"]


class CreateIdentityIn(BaseModel):
    id: str = Field(pattern=r"^discord-[a-z][a-z0-9-]{0,31}$")
    display_name: str = Field(min_length=1, max_length=128)
    secret_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")


async def _configured(request: Request, row: ChatIdentity) -> bool:
    ref = row.secret_refs.get("bot_token")
    if not isinstance(ref, dict) or not isinstance(ref.get("secret"), str):
        return False
    secret = await request.app.state.secret_store.get(ref["secret"])
    return bool(secret and secret.get(ref.get("key", "")))


async def _agent_can_send(request: Request, identity_id: str) -> bool:
    """The Tool grant and chosen account are both required for agent sends."""
    agent = getattr(request.state, "api_key_agent", None)
    if not agent:
        return False
    await request.app.state.agent_store.reload()
    info = request.app.state.agent_store.get(agent)
    frozen = getattr(request.state, "frozen_tools", None)
    grants = frozen if frozen is not None else (info.platform_tools if info else [])
    return bool(info and info.enabled and info.discord_identity_id == identity_id
                and "mcp__platform__discord_chat" in grants)


@router.get("/api/chat-identities", response_model=list[S.ChatIdentityView])
async def list_chat_identities(request: Request,
                               actor: str = Depends(require_admin)):
    """Admin inventory of identities and exact routes; never secret values."""
    async with request.app.state.session_factory() as session:
        rows = (await session.execute(select(ChatIdentity).order_by(
            ChatIdentity.connector, ChatIdentity.id))).scalars().all()
        counts = dict((await session.execute(select(
            RelayBinding.identity_id, func.count(RelayBinding.id)
        ).where(RelayBinding.identity_id.is_not(None)).group_by(
            RelayBinding.identity_id))).all())
    result = []
    for row in rows:
        result.append({"id": row.id, "connector": row.connector,
                       "display_name": row.display_name, "status": row.status,
                       "secret_refs": row.secret_refs,
                       "configured": await _configured(request, row),
                       "bound_routes": counts.get(row.id, 0)})
    return result


@router.post("/api/chat-identities", status_code=201,
             response_model=S.ChatIdentityView)
async def create_chat_identity(request: Request, body: CreateIdentityIn,
                               actor: str = Depends(require_admin)):
    """Register another Discord account, disabled until its credential exists."""
    if body.id == "discord-default":
        raise HTTPException(409, "default identity already exists")
    async with request.app.state.session_factory() as session:
        row = ChatIdentity(id=body.id, connector="discord",
                           display_name=body.display_name.strip(),
                           secret_refs={"bot_token": {"secret": body.secret_name,
                                                      "key": "token"}},
                           status="disabled")
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "chat identity already exists")
    return {"id": row.id, "connector": row.connector,
            "display_name": row.display_name, "status": row.status,
            "secret_refs": row.secret_refs,
            "configured": await _configured(request, row), "bound_routes": 0}


@router.patch("/api/chat-identities/{identity_id}/status")
async def set_chat_identity_status(request: Request, identity_id: str,
                                   body: IdentityStatusIn,
                                   actor: str = Depends(require_admin)):
    """Disable an external account without deleting its routes or credential."""
    async with request.app.state.session_factory() as session:
        row = await session.get(ChatIdentity, identity_id)
        if row is None:
            raise HTTPException(404, "unknown chat identity")
        if body.status == "active" and not await _configured(request, row):
            raise HTTPException(409, "chat identity token is not configured")
        row.status = body.status
        await session.commit()
    return {"id": identity_id, "status": body.status}


@router.get("/api/chat-identities/{identity_id}/transport")
async def chat_identity_transport(request: Request, identity_id: str,
                                  caller: str = Depends(require_role(
                                      "connector", "tools", "relay", "admin"))):
    """Credential-free activation check for the connector and Tool broker."""
    ident = await authenticate(request)
    if getattr(request.state, "api_key_agent", None) and not await _agent_can_send(request, identity_id):
        raise HTTPException(403, "agent has not been granted this Discord identity")
    if ident and ident[1] == "connector" and connector_identity(ident[0]) != identity_id:
        raise HTTPException(403, "connector identity cannot inspect another account")
    async with request.app.state.session_factory() as session:
        row = await session.get(ChatIdentity, identity_id)
        if row is None:
            raise HTTPException(404, "unknown chat identity")
        return {"id": row.id,
                "active": row.status == "active" and await _configured(request, row)}
