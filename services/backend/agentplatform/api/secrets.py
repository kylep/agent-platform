import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from agentplatform import secretverify
from agentplatform.agentspec import validate_agent_name
from agentplatform.api.auth import require_admin
from agentplatform.db import SecretMeta
from agentplatform.secretregistry import SecretInfo, SecretSpec

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
                declared |= set(info.skill.secret_names)
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
            "probeable": spec.verifiable,
            "keys": [{"name": k.name, "hint": k.hint} for k in spec.keys]}


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
        info = reg.get(n)
        out.append({"name": n, "status": status,
                    "declared": bool(info and info.spec), **_hints(info)})
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


# --- declarations (secrets as code — the git side) ---------------------------

class SecretKeyIn(BaseModel):
    name: str
    hint: str = ""


class ProbeIn(BaseModel):
    url: str
    headers: dict[str, str] = {}


class SecretDeclareIn(BaseModel):
    name: str
    description: str = ""
    hint: str = ""
    required: bool = False
    keys: list[SecretKeyIn] = []
    probe: ProbeIn | None = None


def _render_secret_yaml(body: SecretDeclareIn) -> str:
    """Deterministic secret.yaml scaffold from the declare form. Data-only —
    no coding agent involved; verify scripts stay a hand-written escape hatch."""
    doc: dict = {"name": body.name}
    if body.description:
        doc["description"] = body.description
    if body.required:
        doc["required"] = True
    if body.hint:
        doc["hint"] = body.hint
    if body.keys:
        doc["keys"] = [{"name": k.name, **({"hint": k.hint} if k.hint else {})}
                       for k in body.keys]
    if body.probe is not None:
        doc["verify"] = {"probe": {"url": body.probe.url,
                                   **({"headers": body.probe.headers} if body.probe.headers else {})}}
    return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=88)


@router.post("/api/secrets/declare", response_model=S.EditResult, dependencies=[Depends(require_admin)])
async def declare_secret(request: Request, body: SecretDeclareIn,
                         principal: str = Depends(require_admin)):
    """Declare a new secret: scaffold `secrets/<name>/secret.yaml` from the
    form and open a pull request on `coder/secret-<name>` — the standard
    change loop. The value is set separately (Secrets page) once the
    declaration is live."""
    try:
        validate_agent_name(body.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    if _registry(request).get(body.name) is not None:
        raise HTTPException(409, "a secret with this name is already declared")
    text = _render_secret_yaml(body)
    # Round-trip guard: what we scaffold must load as a valid SecretSpec.
    SecretSpec(**(yaml.safe_load(text) or {}))
    from agentplatform.api.gitedit import _apply_files
    return await _apply_files(
        request, {f"secrets/{body.name}/secret.yaml": text},
        message=f"{principal}: declare secret {body.name}",
        branch=f"coder/secret-{body.name}",
        pr_title=f"Declare secret: {body.name}",
        pr_body="Secret declaration scaffolded from the Secrets page.")


@router.get("/api/secrets/{name}/declaration", response_model=S.SecretDeclaration, dependencies=[Depends(require_admin)])
async def secret_declaration(request: Request, name: str):
    """The secret's declaration file as written (comments preserved) — what
    the in-place editor round-trips."""
    info = _registry(request).get(name)
    if info is None:
        raise HTTPException(404, "unknown secret declaration")
    try:
        raw = (info.dir / "secret.yaml").read_text()
    except OSError:
        raise HTTPException(404, "declaration file unreadable")
    return {"name": name, "raw": raw, "error": info.error}


class SecretQuickEditIn(BaseModel):
    value: str            # the full secret.yaml text


@router.post("/api/secrets/{name}/quick-edit", response_model=S.EditResult, dependencies=[Depends(require_admin)])
async def secret_quick_edit(request: Request, name: str, body: SecretQuickEditIn,
                            principal: str = Depends(require_admin)):
    """Deterministic edit of a secret's declaration (never its value): writes
    the exact secret.yaml supplied and opens a PR on `coder/secret-<name>`,
    with validation up front so a broken declaration can't be proposed."""
    if _registry(request).get(name) is None:
        raise HTTPException(404, "unknown secret declaration")
    try:
        raw = yaml.safe_load(body.value) or {}
        raw.setdefault("name", name)
        SecretSpec(**raw)
    except Exception as e:
        raise HTTPException(422, f"invalid secret.yaml: {e}")
    from agentplatform.api.gitedit import _apply_files
    return await _apply_files(
        request, {f"secrets/{name}/secret.yaml": body.value},
        message=f"{principal}: quick-edit secret {name}",
        branch=f"coder/secret-{name}", pr_title=f"Edit secret declaration: {name}",
        pr_body=f"Direct secret.yaml edit for `{name}` from the Secrets page.")


@router.put("/api/secrets/{name}", response_model=S.Ok, dependencies=[Depends(require_admin)])
async def put_secret(request: Request, name: str, body: SecretIn):
    # Merge into whatever is already stored rather than replacing wholesale, so
    # a multi-key secret can be filled one field (or one save) at a time and
    # rotating a single key never silently drops the others. The k8s store
    # replaces the whole Secret object, so the merge has to happen here.
    store = request.app.state.secret_store
    existing = await store.get(name) or {}
    merged = {**existing, **{k: v for k, v in body.data.items() if v is not None}}
    await store.set(name, merged)
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = "unprobed"
        s.add(meta); await s.commit()
    return {"ok": True}
