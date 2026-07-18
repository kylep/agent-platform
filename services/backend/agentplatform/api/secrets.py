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
    out = []
    for n in names:
        status = rows.get(n, "missing")
        if status == "missing" and await request.app.state.secret_store.exists(n):
            # Secret was created out-of-band (e.g. set-claude-token.sh kubectl
            # mode writes the k8s Secret directly, bypassing the API): the
            # store is the truth for existence, meta only tracks probe status.
            status = "unprobed"
        out.append({"name": n, "status": status, "required": n in REQUIRED_SECRETS})
    return out

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
