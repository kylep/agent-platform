"""Chat account metadata. Credentials remain in the existing secret store."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from agentplatform.api import schemas as S
from agentplatform.api.auth import require_admin, require_role
from agentplatform.db import ChatIdentity, RelayBinding

router = APIRouter()


class IdentityStatusIn(BaseModel):
    status: Literal["active", "disabled"]


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
        names = sorted({ref["secret"] for ref in row.secret_refs.values()
                        if isinstance(ref, dict) and isinstance(ref.get("secret"), str)})
        configured = all([await request.app.state.secret_store.exists(name)
                          for name in names]) if names else False
        result.append({"id": row.id, "connector": row.connector,
                       "display_name": row.display_name, "status": row.status,
                       "secret_refs": row.secret_refs, "configured": configured,
                       "bound_routes": counts.get(row.id, 0)})
    return result


@router.patch("/api/chat-identities/{identity_id}/status")
async def set_chat_identity_status(request: Request, identity_id: str,
                                   body: IdentityStatusIn,
                                   actor: str = Depends(require_admin)):
    """Disable an external account without deleting its routes or credential."""
    async with request.app.state.session_factory() as session:
        row = await session.get(ChatIdentity, identity_id)
        if row is None:
            raise HTTPException(404, "unknown chat identity")
        row.status = body.status
        await session.commit()
    return {"id": identity_id, "status": body.status}


@router.get("/api/chat-identities/{identity_id}/transport")
async def chat_identity_transport(request: Request, identity_id: str,
                                  caller: str = Depends(require_role(
                                      "connector", "tools", "relay", "admin"))):
    """Credential-free activation check for the connector and Tool broker."""
    async with request.app.state.session_factory() as session:
        row = await session.get(ChatIdentity, identity_id)
        if row is None:
            raise HTTPException(404, "unknown chat identity")
        return {"id": row.id, "active": row.status == "active"}
