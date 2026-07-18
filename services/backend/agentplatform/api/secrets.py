from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import require_admin
from agentplatform.db import SecretMeta
from agentplatform.secrets import REQUIRED_SECRETS

router = APIRouter()

class SecretIn(BaseModel):
    data: dict[str, str]

async def secret_listing(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        rows = {m.name: m.status for m in (await s.execute(select(SecretMeta))).scalars()}
    names = sorted(set(REQUIRED_SECRETS) | set(rows))
    return [{"name": n, "status": rows.get(n, "missing"), "required": n in REQUIRED_SECRETS}
            for n in names]

@router.get("/api/secrets", dependencies=[Depends(require_admin)])
async def list_secrets(request: Request):
    return await secret_listing(request)

@router.put("/api/secrets/{name}", dependencies=[Depends(require_admin)])
async def put_secret(request: Request, name: str, body: SecretIn):
    await request.app.state.secret_store.set(name, body.data)
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = "unprobed"
        s.add(meta); await s.commit()
    return {"ok": True}
