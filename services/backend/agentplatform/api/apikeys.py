from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform.api.auth import API_KEY_ROLES, require_admin
from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, utcnow

from agentplatform.api import schemas as S
router = APIRouter(dependencies=[Depends(require_admin)])


class ApiKeyIn(BaseModel):
    name: str
    role: str
    # No `agent` scope: the role is the only authorization boundary. The
    # ApiKey.agent column exists but is an internal owner label (system keys),
    # not an enforced scope — exposing it here implied per-agent scoping that
    # doesn't exist.


class ApiKeyRoleIn(BaseModel):
    role: str


def _view(k: ApiKey) -> dict:
    return {"id": k.id, "name": k.name, "role": k.role, "agent": k.agent,
            "managed": _managed(k),
            "prefix": k.prefix, "created_at": k.created_at,
            "revoked_at": k.revoked_at}


def _managed(k: ApiKey) -> bool:
    return k.run_id is not None or k.name.startswith("app:")


@router.get("/api/api-keys", response_model=list[S.ApiKeyView])
async def list_api_keys(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(ApiKey).order_by(ApiKey.created_at))).scalars().all()
    return [_view(k) for k in rows]


@router.post("/api/api-keys", status_code=201, response_model=S.ApiKeyCreated)
async def mint_api_key(request: Request, body: ApiKeyIn):
    if body.role not in API_KEY_ROLES:
        raise HTTPException(422, f"role must be one of {API_KEY_ROLES}")
    if body.name.startswith("app:"):
        raise HTTPException(422, "app: names are reserved for platform-managed keys")
    token = generate_token()
    key = ApiKey(name=body.name, role=body.role,
                 key_hash=hash_token(token), prefix=token_prefix(token))
    async with request.app.state.session_factory() as s:
        s.add(key); await s.commit()
        # `token` is returned exactly once here and never persisted in clear.
        return {**_view(key), "token": token}


@router.delete("/api/api-keys/{key_id}", response_model=S.Ok)
async def revoke_api_key(request: Request, key_id: str):
    async with request.app.state.session_factory() as s:
        key = await s.get(ApiKey, key_id)
        if key is None:
            raise HTTPException(404, "unknown key")
        if key.revoked_at is None:
            key.revoked_at = utcnow()
            await s.commit()
    return {"ok": True}


@router.patch("/api/api-keys/{key_id}", response_model=S.ApiKeyView)
async def change_api_key_role(request: Request, key_id: str, body: ApiKeyRoleIn):
    if body.role not in API_KEY_ROLES:
        raise HTTPException(422, f"role must be one of {API_KEY_ROLES}")
    async with request.app.state.session_factory() as s:
        key = await s.get(ApiKey, key_id)
        if key is None:
            raise HTTPException(404, "unknown key")
        if key.revoked_at is not None:
            raise HTTPException(409, "revoked key cannot be edited")
        if _managed(key):
            raise HTTPException(409, "platform-managed key role comes from its declaration")
        key.role = body.role
        await s.commit()
        return _view(key)
