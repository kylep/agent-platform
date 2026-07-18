from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.db import Principal

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

async def require_admin(request: Request) -> str:
    principal = validate_session_cookie(request.app, request.cookies.get("ap_session"))
    if principal is None:
        raise HTTPException(401)
    return principal

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
