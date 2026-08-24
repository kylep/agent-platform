from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.agentspec import PLATFORM_MCP_TOOLS
from agentplatform.apikeys import hash_token
from agentplatform.db import ApiKey, Principal

ph = PasswordHasher()
from agentplatform.api import schemas as S
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

# Roles. reader/operator/coder/admin are the human/agent scopes; `annotator`
# is a narrow machine role (read runs + annotate only) for system agents, so a
# prompt-injected system agent can't mint runs or mutate unrelated state.
# admin is a superset of every scope. NOTE: role checks are an explicit
# allow-list per endpoint (not hierarchical) — list every role that may access.
# `tools` (docs/design/12) is the narrowest machine role: it satisfies NO
# endpoint allow-list except /api/whoami — it exists purely so the MCP broker
# can verify a caller and serve its declared custom tools.
ROLES = ("reader", "annotator", "operator", "coder", "admin", "tools")
READ_ROLES = ("reader", "annotator", "operator", "coder")
ANNOTATE_ROLES = ("annotator", "operator", "coder")
# Who may request a run (POST /api/runs) — humans (operator+) and agents whose
# injected token is operator-scoped (agent-invokes-agent). `annotator` (the
# default system-agent role) deliberately can't, so a prompt-injected summarizer
# can't spawn runs.
INVOKE_ROLES = ("operator", "coder", "admin")
# Who may use the memory API. Agents (annotator+) manage their own namespace;
# the namespace itself (not the role) is the isolation boundary.
MEMORY_ROLES = ("annotator", "operator", "coder", "admin")


def role_allows(role: str | None, allowed: tuple[str, ...]) -> bool:
    """Authorization decision: an authenticated `role` may access an endpoint
    guarded by `allowed` if it is admin (allowed everywhere) or listed."""
    return role is not None and (role == "admin" or role in allowed)


async def _lookup_role(request: Request, name: str) -> str | None:
    async with request.app.state.session_factory() as s:
        p = (await s.execute(select(Principal).where(Principal.name == name))).scalar_one_or_none()
        return p.role if p else None


async def _lookup_api_key(request: Request, token: str) -> ApiKey | None:
    async with request.app.state.session_factory() as s:
        return (await s.execute(select(ApiKey).where(
            ApiKey.key_hash == hash_token(token),
            ApiKey.revoked_at.is_(None)))).scalar_one_or_none()


async def authenticate(request: Request) -> tuple[str, str] | None:
    """Resolve the caller to (principal_name, role), or None. A session cookie
    (interactive admin) is tried first, then an `Authorization: Bearer ap_...`
    API key (non-interactive / agent-invokes-agent).

    When the caller is a per-run API key, its `run_id` is stashed on
    `request.state.api_key_run_id` so run creation can attribute the new run's
    parent and enforce the chain-depth loop guard authoritatively (the caller
    can't forge its own parent)."""
    name = validate_session_cookie(request.app, request.cookies.get("ap_session"))
    if name is not None:
        role = await _lookup_role(request, name)
        if role is not None:
            return (name, role)
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[len("Bearer "):].strip()
        if token.startswith("ap_"):
            k = await _lookup_api_key(request, token)
            if k is not None:
                request.state.api_key_run_id = k.run_id
                request.state.api_key_agent = k.agent
                return (k.name, k.role)
        elif token.count(".") == 2:
            # Workload identity (docs/design/13 A): a kubelet-projected,
            # audience-bound ServiceAccount JWT instead of a minted secret.
            ident = await _validate_sa_token(request, token)
            if ident is not None:
                agent, role = ident
                run_id, frozen = None, None
                run_token = request.headers.get("x-ap-run-token", "")
                if run_token:
                    # Sender-constrained run JWT (design/13 C): must match
                    # the workload that presented it. A PRESENT-but-invalid
                    # token is a red flag, not a fallback — reject outright.
                    claims = await _verify_run_token(request, run_token, agent)
                    if claims is None:
                        return None
                    run_id = claims.get("run_id")
                    request.state.initiated_by = claims.get("initiated_by")
                    frozen = [t for t in (claims.get("tools") or [])
                              if isinstance(t, str)]
                    role = ("annotator" if any(t in PLATFORM_MCP_TOOLS for t in frozen)
                            else "tools")
                request.state.api_key_run_id = run_id
                request.state.api_key_agent = agent
                request.state.frozen_tools = frozen
                return (f"sa:{agent}", role)
    return None


async def _verify_run_token(request: Request, token: str, agent: str) -> dict | None:
    """Verify a run JWT against the dispatcher's public key, cnf-bound to the
    presenting workload's ServiceAccount."""
    from agentplatform import runjwt
    st = request.app.state
    pub = getattr(st, "_runjwt_pub", None)
    if pub is None:
        creds = await st.secret_store.get(runjwt.SECRET_NAME)
        pub = (creds or {}).get("public_key")
        if not pub:
            return None
        st._runjwt_pub = pub
    return runjwt.verify(pub, token, expected_sa=f"agent-{agent}")


# Short-lived cache of validated SA tokens: TokenReview is an apiserver round
# trip, and a chatty agent run makes many broker calls with the same token.
# Lives on app.state (not module-global) so each app instance is isolated.
_SA_CACHE_TTL = 60.0


async def _validate_sa_token(request: Request, token: str) -> tuple[str, str] | None:
    """Resolve a projected SA token to (agent, ladder_role) via TokenReview.
    The role is recomputed from the agent's CURRENT declared tools — identity
    comes from the cluster, authorization from the definition in git."""
    import time
    from agentplatform.apikeys import hash_token
    if not hasattr(request.app.state, "_sa_cache"):
        request.app.state._sa_cache = {}
    cache: dict = request.app.state._sa_cache
    h = hash_token(token)
    hit = cache.get(h)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    validator = getattr(request.app.state, "sa_validator", None)
    if validator is None:
        return None
    username = await validator(token)   # e.g. system:serviceaccount:ns:agent-pai
    if not username:
        return None
    sa_name = username.rsplit(":", 1)[-1]
    if not sa_name.startswith("agent-"):
        return None
    agent = sa_name[len("agent-"):]
    st = request.app.state
    await st.agent_store.reload()
    info = st.agent_store.get(agent)
    if info is None:
        return None
    platform = [t for t in info.platform_tools if t.startswith("mcp__platform__")]
    if not platform:
        return None
    role = "annotator" if any(t in PLATFORM_MCP_TOOLS for t in platform) else "tools"
    out = (agent, role)
    cache[h] = (time.monotonic() + _SA_CACHE_TTL, out)
    return out


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


@router.get("/api/whoami", response_model=S.WhoAmI)
async def whoami(request: Request):
    """Verified caller identity (docs/design/12): the MCP broker calls this
    with a forwarded bearer to resolve WHO is invoking a custom tool — agent,
    run, and the mcp__platform__* tools that agent's definition declares. The
    broker enforces tool grants from this answer, so identity is derived from
    the token, never from anything the model says. Accepts every authenticated
    role including `tools` (whose keys can reach nothing else)."""
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    name, role = ident
    agent = getattr(request.state, "api_key_agent", None)
    run_id = getattr(request.state, "api_key_run_id", None)
    tools: list[str] | None = None
    frozen = getattr(request.state, "frozen_tools", None)
    if frozen is not None:
        # design/13 C: the grant set was FROZEN into the run JWT at launch —
        # a mid-run grant edit cannot widen (or shrink) a live run.
        return {"principal": name, "role": role, "agent": agent,
                "run_id": run_id, "tools": frozen,
                "initiated_by": getattr(request.state, "initiated_by", None)}
    if agent:
        st = request.app.state
        await st.agent_store.reload()
        info = st.agent_store.get(agent)
        # design/15: the grant set is the row's `platform_tools`. An agent with
        # no platform grant gets [], NOT everything — the file era's "no
        # `tools:` line means unrestricted" default went away with the file,
        # and the broker's grant check stays a plain membership test.
        granted = info.platform_tools if info else []
        tools = [t for t in granted if t.startswith("mcp__platform__")]
    return {"principal": name, "role": role, "agent": agent,
            "run_id": run_id, "tools": tools}

@router.get("/api/setup-state", response_model=S.SetupState)
async def setup_state(request: Request):
    from agentplatform.api.secrets import secret_listing
    needs_admin = await _admin(request) is None
    # Secret names/health are only exposed pre-setup (for the first-launch gate)
    # or to an authenticated caller — not to anonymous callers post-setup.
    authed = await authenticate(request) is not None
    secrets = await secret_listing(request) if (needs_admin or authed) else []
    return {"needs_admin": needs_admin, "secrets": secrets}

@router.post("/api/setup", response_model=S.Ok)
async def setup(request: Request, creds: Creds):
    if await _admin(request) is not None:
        raise HTTPException(409, "already set up")
    async with request.app.state.session_factory() as s:
        s.add(Principal(name="admin", role="admin", password_hash=ph.hash(creds.password)))
        await s.commit()
    return {"ok": True}

@router.post("/api/login", response_model=S.Ok)
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

@router.post("/api/logout", response_model=S.Ok)
async def logout(response: Response):
    response.delete_cookie("ap_session")
    return {"ok": True}


class PasswordChange(BaseModel):
    old_password: str
    new_password: str


@router.post("/api/change-password", response_model=S.Ok, dependencies=[Depends(require_admin)])
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
