import asyncio
import urllib.error
import urllib.request

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import require_admin
from agentplatform.db import SecretMeta
from agentplatform.secrets import (PROBEABLE_SECRETS, REQUIRED_SECRETS, SECRET_HINTS,
                                   secret_probe_target)

router = APIRouter()


def _http_ok(url: str, headers: dict) -> bool:
    """GET the url; True on a 2xx (the credential authenticates)."""
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300
    except urllib.error.HTTPError:
        return False
    except Exception:
        return False

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


async def secret_listing(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        rows = {m.name: m.status for m in (await s.execute(select(SecretMeta))).scalars()}
    names = sorted(set(REQUIRED_SECRETS) | set(rows) | _declared_secrets(request))
    out = []
    for n in names:
        status = rows.get(n, "missing")
        if status == "missing" and await request.app.state.secret_store.exists(n):
            # Secret was created out-of-band (e.g. set-claude-token.sh kubectl
            # mode writes the k8s Secret directly, bypassing the API): the
            # store is the truth for existence, meta only tracks probe status.
            status = "unprobed"
        hint = SECRET_HINTS.get(n, {})
        out.append({"name": n, "status": status, "required": n in REQUIRED_SECRETS,
                    "hint": hint.get("hint", ""), "key": hint.get("key", ""),
                    "probeable": n in PROBEABLE_SECRETS})
    return out

@router.get("/api/secrets", dependencies=[Depends(require_admin)])
async def list_secrets(request: Request):
    return await secret_listing(request)

@router.post("/api/secrets/{name}/verify", dependencies=[Depends(require_admin)])
async def verify_secret(request: Request, name: str):
    """Validate a secret with a read-only API call and record the result."""
    data = await request.app.state.secret_store.get(name)
    if data is None:
        raise HTTPException(404, "secret is not set")
    target = secret_probe_target(name, data)
    if target is None:
        raise HTTPException(422, "this secret has no probe")
    ok = await asyncio.to_thread(_http_ok, target[0], target[1])
    status = "valid" if ok else "invalid"
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = status
        s.add(meta); await s.commit()
    return {"name": name, "status": status}


@router.put("/api/secrets/{name}", dependencies=[Depends(require_admin)])
async def put_secret(request: Request, name: str, body: SecretIn):
    await request.app.state.secret_store.set(name, body.data)
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = "unprobed"
        s.add(meta); await s.commit()
    return {"ok": True}
