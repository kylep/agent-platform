from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.apikeys import hash_token
from agentplatform.db import ApiKey, Principal

ph = PasswordHasher()
router = APIRouter()

class Creds(BaseModel):
    password: str

def _signer(request: Request) -> URLSafeSerializer:
    return URLSafeSerializer(request.app.state.settings.session_secret, salt="ap-session")

async def _admin(request: Request) -> Principal | None:
    async with request.app.state.session_factory() as s:
        return (await s.execute(select(Principal).where(Principal.name == "admin"))).scalar_one_or_none()

def validate_session_cookie(app, cookie: str | None) -> str | None:
    """Validate an `ap_session` cookie against the app's session secret.

    Returns the principal name on success, or None if the cookie is
    missing or invalid. Shared by REST (require_admin) and websocket
    (tail) auth paths so both use the same signer/salt.
    """
    if not cookie:
        return None
    signer = URLSafeSerializer(app.state.settings.session_secret, salt="ap-session")
    try:
        data = signer.loads(cookie)
    except BadSignature:
        return None
    return data["principal"]

# Role privilege order (ascending). `admin` is a superset of every scope.
ROLES = ("reader", "operator", "coder", "admin")


def role_allows(role: str | None, allowed: tuple[str, ...]) -> bool:
    """Authorization decision: an authenticated `role` may access an endpoint
    guarded by `allowed` if it is admin (allowed everywhere) or listed."""
    return role is not None and (role == "admin" or role in allowed)


async def _lookup_role(request: Request, name: str) -> str | None:
    async with request.app.state.session_factory() as s:
        p = (await s.execute(select(Principal).where(Principal.name == name))).scalar_one_or_none()
        return p.role if p else None


async def _lookup_api_key(request: Request, token: str) -> tuple[str, str] | None:
    async with request.app.state.session_factory() as s:
        k = (await s.execute(select(ApiKey).where(
            ApiKey.key_hash == hash_token(token),
            ApiKey.revoked_at.is_(None)))).scalar_one_or_none()
        return (k.name, k.role) if k else None


async def authenticate(request: Request) -> tuple[str, str] | None:
    """Resolve the caller to (principal_name, role), or None. A session cookie
    (interactive admin) is tried first, then an `Authorization: Bearer ap_...`
    API key (non-interactive / agent-invokes-agent)."""
    name = validate_session_cookie(request.app, request.cookies.get("ap_session"))
    if name is not None:
        role = await _lookup_role(request, name)
        if role is not None:
            return (name, role)
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        if token:
            return await _lookup_api_key(request, token)
    return None


def require_role(*allowed: str):
    """Dependency factory: authenticate (session cookie or API key) and require
    the caller's role to satisfy `allowed` (admin always passes). Returns the
    principal name so handlers can attribute actions."""
    async def dep(request: Request) -> str:
        ident = await authenticate(request)
        if ident is None:
            raise HTTPException(401)
        name, role = ident
        if not role_allows(role, allowed):
            raise HTTPException(403)
        return name
    return dep


require_admin = require_role("admin")

@router.get("/api/setup-state")
async def setup_state(request: Request):
    from agentplatform.api.secrets import secret_listing
    return {
        "needs_admin": await _admin(request) is None,
        "secrets": await secret_listing(request),
    }

@router.post("/api/setup")
async def setup(request: Request, creds: Creds):
    if await _admin(request) is not None:
        raise HTTPException(409, "already set up")
    async with request.app.state.session_factory() as s:
        s.add(Principal(name="admin", role="admin", password_hash=ph.hash(creds.password)))
        await s.commit()
    return {"ok": True}

@router.post("/api/login")
async def login(request: Request, response: Response, creds: Creds):
    admin = await _admin(request)
    if admin is None:
        raise HTTPException(401)
    try:
        ph.verify(admin.password_hash, creds.password)
    except VerifyMismatchError:
        raise HTTPException(401)
    response.set_cookie("ap_session", _signer(request).dumps({"principal": "admin"}),
                        httponly=True, samesite="lax")
    return {"ok": True}

@router.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("ap_session")
    return {"ok": True}


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/change-password", dependencies=[Depends(require_admin)])
async def change_password(request: Request, body: PasswordChange):
    """Rotate the admin password from Settings (re-auth with the current one),
    replacing the postgres-row-delete-and-re-setup workaround."""
    admin = await _admin(request)
    if admin is None:
        raise HTTPException(401)
    try:
        ph.verify(admin.password_hash, body.old_password)
    except VerifyMismatchError:
        raise HTTPException(403, "current password is incorrect")
    if len(body.new_password) < 8:
        raise HTTPException(422, "new password must be at least 8 characters")
    async with request.app.state.session_factory() as s:
        p = await s.get(Principal, admin.id)
        p.password_hash = ph.hash(body.new_password)
        await s.commit()
    return {"ok": True}
