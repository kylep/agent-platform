from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform.api.auth import ROLES, require_admin
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, utcnow

router = APIRouter(dependencies=[Depends(require_admin)])


class ApiKeyIn(BaseModel):
    name: str
    role: str
    agent: str | None = None


def _view(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "role": k.role, "agent": k.agent,
            "prefix": k.prefix, "created_at": k.created_at,
            "revoked_at": k.revoked_at}


@router.get("/api/api-keys")
async def list_api_keys(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
    return [_view(k) for k in rows]


@router.post("/api/api-keys", status_code=201)
async def mint_api_key(request: Request, body: ApiKeyIn):
    if body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    token = generate_token()
    key = ApiKey(name=body.name, role=body.role, agent=body.agent,
                 key_hash=hash_token(token), prefix=token_prefix(token))
    async with request.app.state.session_factory() as s:
        s.add(key); await s.commit()
        # `token` is returned exactly once here and never persisted in clear.
        return {**_view(key), "token": token}


@router.delete("/api/api-keys/{key_id}")
async def revoke_api_key(request: Request, key_id: str):
    async with request.app.state.session_factory() as s:
        key = await s.get(ApiKey, key_id)
        if key is None:
            raise HTTPException(404, "unknown key")
        if key.revoked_at is None:
            key.revoked_at = utcnow()
            await s.commit()
    return {"ok": True}
