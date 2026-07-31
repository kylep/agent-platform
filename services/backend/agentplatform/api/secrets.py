from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform import secretverify
from agentplatform.api.auth import require_admin
from agentplatform.db import SecretMeta
from agentplatform.secretregistry import SecretInfo

from agentplatform.api import schemas as S
router = APIRouter()


class SecretIn(BaseModel):
    data: dict[str, str]


def _declared_secrets(request: Request) -> set[str]:
    """Secrets the platform's components declare they need: skill `secrets:` and
    connector secrets. Surfaced as (optional) rows so they're settable in the UI."""
    from agentplatform.connectors import CONNECTOR_SECRETS
    declared = set(CONNECTOR_SECRETS)
    store = getattr(request.app.state, "skill_store", None)
    if store is not None:
        store.reload()
        for info in store.list():
            if info.skill:
                declared |= set(info.skill.secrets)
    return declared


def _registry(request: Request):
    reg = request.app.state.secret_registry
    # The synced checkout changes underneath us (agents-sync pulls git).
    reg.reload()
    return reg


def _hints(info: SecretInfo | None) -> dict:
    """Flatten a registry spec into the UI's hint fields. Single-key secrets
    suggest their key (it becomes the env var a skill reads); multi-key or
    flexible-key secrets leave `key` blank for the editor heuristic."""
    spec = info.spec if info else None
    if spec is None:
        return {"required": False, "hint": "", "key": "", "probeable": False}
    single = spec.keys[0] if len(spec.keys) == 1 else None
    return {"required": spec.required,
            "hint": spec.hint or (single.hint if single else spec.description),
            "key": single.name if single else "",
            "probeable": spec.verifiable}


async def secret_listing(request: Request) -> list[dict]:
    reg = _registry(request)
    async with request.app.state.session_factory() as s:
        rows = {m.name: m.status for m in (await s.execute(select(SecretMeta))).scalars()}
    names = sorted({i.name for i in reg.list()} | set(rows) | _declared_secrets(request))
    out = []
    for n in names:
        status = rows.get(n, "missing")
        if status == "missing" and await request.app.state.secret_store.exists(n):
            # Secret was created out-of-band (e.g. set-claude-token.sh kubectl
            # mode writes the k8s Secret directly, bypassing the API): the
            # store is the truth for existence, meta only tracks probe status.
            status = "unprobed"
        out.append({"name": n, "status": status, **_hints(reg.get(n))})
    return out


@router.get("/api/secrets", response_model=list[S.SecretStatus], dependencies=[Depends(require_admin)])
async def list_secrets(request: Request):
    return await secret_listing(request)


@router.post("/api/secrets/{name}/verify", response_model=S.SecretVerify, dependencies=[Depends(require_admin)])
async def verify_secret(request: Request, name: str):
    """Run the secret's declared verification (probe or sandboxed script) and
    record the result."""
    data = await request.app.state.secret_store.get(name)
    if data is None:
        raise HTTPException(404, "secret is not set")
    info = _registry(request).get(name)
    result = await secretverify.verify_secret(info, data) if info else None
    if result is None:
        raise HTTPException(422, "this secret has no verify")
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = result.status
        s.add(meta); await s.commit()
    return {"name": name, "status": result.status, "code": result.code,
            "detail": result.detail}


@router.put("/api/secrets/{name}", response_model=S.Ok, dependencies=[Depends(require_admin)])
async def put_secret(request: Request, name: str, body: SecretIn):
    await request.app.state.secret_store.set(name, body.data)
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = "unprobed"
        s.add(meta); await s.commit()
    return {"ok": True}
