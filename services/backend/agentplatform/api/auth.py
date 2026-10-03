import secrets

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel, Field
from sqlalchemy import select
from agentplatform.agentspec import platform_token_role
from agentplatform.apikeys import hash_token
from agentplatform.appdata import credentials as _tc
from agentplatform.db import ApiKey, ChatIdentity, Principal

ph = PasswordHasher()
# A hash no password matches, verified on the login path's miss branch so an
# unknown principal name costs the same as a wrong password.
_NO_SUCH_PRINCIPAL_HASH = ph.hash(secrets.token_urlsafe(32))
from agentplatform.api import schemas as S
router = APIRouter()

class Creds(BaseModel):
    password: str
    # Which Principal row the password opens (docs/design/25). The console's
    # login form leaves it at `admin`; the QA's `bin/ap-web-login` names `qa`.
    # Any row with a password_hash can be named — role comes from the row, so
    # naming `qa` buys a reader cookie and nothing more. Bounded to a short
    # lowercase slug so the lookup never sees an arbitrary string.
    principal: str = Field(default="admin", min_length=1, max_length=64,
                           pattern=r"^[a-z][a-z0-9_-]*$")

def _signer(request: Request) -> URLSafeSerializer:
    return URLSafeSerializer(request.app.state.settings.session_secret, salt="ap-session")

async def _principal(request: Request, name: str) -> Principal | None:
    async with request.app.state.session_factory() as s:
        return (await s.execute(select(Principal).where(Principal.name == name))).scalar_one_or_none()


async def _admin(request: Request) -> Principal | None:
    return await _principal(request, "admin")

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

# Roles. reader/operator/admin are the human scopes; `annotator`
# is a narrow machine role (read runs + annotate only) for system agents, so a
# prompt-injected system agent can't mint runs or mutate unrelated state.
# admin is a superset of every scope. NOTE: role checks are an explicit
# allow-list per endpoint (not hierarchical) — list every role that may access.
# `tools` (docs/design/12) is the narrowest machine role: it satisfies NO
# endpoint allow-list except /api/whoami — it exists purely so the MCP broker
# can verify a caller and serve its declared custom tools.
# `relay` (docs/design/19, widened by docs/design/20 and docs/design/21) is the
# second per-run machine role, one notch above `tools` and nowhere near the
# human scopes: it satisfies /api/whoami, the Relay endpoints, the Tickets
# endpoints and the Wiki endpoints, which name it EXPLICITLY in their own
# allow-lists, and nothing else. Being listed is the whole of its authority —
# there is no hierarchy here — and in the first two blocks it is channel
# MEMBERSHIP, not the role, that decides where the agent may actually act. The
# wiki has no such fence because a page is not in a room: it belongs to the
# platform, and every participant reads and writes all of it. The role keeps its
# name and means "participant": it is minted for an agent whose platform grants
# are the relay, tickets and wiki tools, which is most agents once the default
# grants land, so it deliberately buys nothing beyond talking, tracking its own
# work and writing down what it learned.
# `dev` (docs/design/24) is a run-PROFILE rung, not an API scope: it decides
# that a run gets the Workbench (a clone, a branch, the toolchain, publish) and
# nothing about which endpoints answer it — like `tools`, it is in NO allow-list
# below. A dev agent's token is minted by the same ladder as everyone else's.
ROLES = ("reader", "annotator", "operator", "admin", "tools", "relay", "dev")
# Only these roles make sense for long-lived, user-created API keys. The other
# roles above are derived for individual agent runs, or describe a run profile.
API_KEY_ROLES = ("reader", "operator", "admin")
READ_ROLES = ("reader", "annotator", "operator")
ANNOTATE_ROLES = ("annotator", "operator")
# Who may request a run (POST /api/runs) — humans (operator+) and agents whose
# injected token is operator-scoped (agent-invokes-agent). `annotator` (the
# default system-agent role) deliberately can't, so a prompt-injected summarizer
# can't spawn runs.
INVOKE_ROLES = ("operator", "admin")
# Who may use the memory API. Agents (annotator+) manage their own namespace;
# the namespace itself (not the role) is the isolation boundary.
MEMORY_ROLES = ("annotator", "operator", "admin")


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
    can't forge its own parent).

    How the caller proved itself goes on `request.state.auth_kind`: "session"
    (the login cookie), "key" (an `ap_` API key) or "workload" (a
    ServiceAccount JWT) or "tool_call" (a tool-call credential beside the
    executor's ServiceAccount JWT, docs/design/39). A key's principal is its
    name, so a name alone can't tell Kyle's login from a key someone called
    `admin`; the kind can."""
    name = validate_session_cookie(request.app, request.cookies.get("ap_session"))
    if name is not None:
        role = await _lookup_role(request, name)
        if role is not None:
            request.state.auth_kind = "session"
            return (name, role)
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    tool_call = request.headers.get(_tc.HEADER, "")
    if tool_call:
        # A present tool-call credential decides the request on its own: it
        # is never a fallback to whatever else the bearer would have been.
        if not tool_call_route(request.url.path):
            return None
        return await _authenticate_tool_call(request, token, tool_call)
    return await authenticate_bearer(request, token, request.headers.get("x-ap-run-token", ""))


async def authenticate_bearer(request: Request, token: str,
                              run_token: str = "") -> tuple[str, str] | None:
    """The bearer half of `authenticate`, for a token and run JWT given
    explicitly. The tool-call mint resolves the broker's CALLER through here,
    since that request's own bearer is the broker's."""
    if token.startswith("ap_"):
        k = await _lookup_api_key(request, token)
        if k is not None:
            if k.run_id and not await _current_run(request, k.run_id):
                return None
            request.state.api_key_run_id = k.run_id
            request.state.api_key_agent = k.agent
            request.state.auth_kind = "key"
            return (k.name, k.role)
    elif token.count(".") == 2:
        # Workload identity (docs/design/13 A): a kubelet-projected,
        # audience-bound ServiceAccount JWT instead of a minted secret.
        ident = await _validate_sa_token(request, token)
        if ident is not None:
            principal, role, agent = ident
            run_id, frozen = None, None
            if agent is not None and run_token:
                # Sender-constrained run JWT (design/13 C): must match
                # the workload that presented it. A PRESENT-but-invalid
                # token is a red flag, not a fallback — reject outright.
                claims = await _verify_run_token(request, run_token, agent)
                if claims is None:
                    return None
                run_id = claims.get("run_id")
                if not run_id or not await _current_run(request, run_id):
                    return None
                request.state.initiated_by = claims.get("initiated_by")
                frozen = [t for t in (claims.get("tools") or [])
                          if isinstance(t, str)]
                # The same ladder the launcher minted the key on, walked
                # over the FROZEN set: a mid-run grant edit must not move
                # the rung either way. An empty freeze still authenticates
                # — as `tools`, which reaches nothing but whoami.
                role = platform_token_role(frozen) or "tools"
            request.state.api_key_run_id = run_id
            request.state.api_key_agent = agent
            request.state.frozen_tools = frozen
            request.state.auth_kind = "workload"
            return (principal, role)
    return None


# Where a tool-call credential authenticates at all. It stands for an agent
# and its run, so a route that trusts `api_key_agent`/`api_key_run_id` alone
# (persona messaging, chat-identity transport, memory, ...) would take it as
# the run itself. Outside these it authenticates nothing: whoami (identity
# only, no authority) and the app_data routes, whose module lets it through to
# the record routes and refuses it on the builder, Kyle and quota routes.
TOOL_CALL_PATHS = ("/api/whoami", "/api/reports")
TOOL_CALL_PREFIXES = ("/api/app-data/",)


def tool_call_route(path: str) -> bool:
    return path in TOOL_CALL_PATHS or path.startswith(TOOL_CALL_PREFIXES)


async def _authenticate_tool_call(request: Request, token: str,
                                  credential: str) -> tuple[str, str] | None:
    """A tool-call credential (docs/design/39), presented by the executor
    beside its own ServiceAccount token. Valid only from that workload, only
    for the call it names, and only until the broker revokes it at return.

    The principal is the agent acting through the tool; the role is `tools`,
    which no allow-list names, and `authenticate` admits it only on
    `tool_call_route`s, so the only routes that answer it are the `app_data`
    record ones, which keep to the credential's `app_scope`."""
    if token.count(".") != 2:
        return None
    sa_name = await workload_sa(request, token)
    settings = request.app.state.settings
    if sa_name is None or sa_name not in (
            settings.tool_executor_service_account,
            settings.tool_executor_views_service_account):
        return None
    keys = await _tc.keypair(request.app.state)
    # TODO(R2): admit KIND_PAGE_INTENT here once page intents are minted;
    # until then a page-intent credential authenticates nothing.
    claims = _tc.verify(keys["public_key"], credential, expected_sa=sa_name,
                        kinds=(_tc.KIND_TOOL_CALL, _tc.KIND_VIEW_EXEC))
    if claims is None:
        return None
    if claims["kind"] == _tc.KIND_VIEW_EXEC:
        if (sa_name != settings.tool_executor_views_service_account
                or request.url.path != "/api/app-data/scan"):
            return None
    elif sa_name != settings.tool_executor_service_account:
        return None
    if request.headers.get(_tc.CALL_ID_HEADER, "") != claims["call_id"]:
        return None
    async with request.app.state.session_factory() as session:
        if not await _tc.is_live(session, claims):
            return None
    if claims["kind"] == _tc.KIND_VIEW_EXEC:
        request.state.auth_kind = _tc.KIND_VIEW_EXEC
        request.state.view_exec = claims
        request.state.frozen_tools = []
        return (claims["principal"], "tools")
    if not await _current_run(request, claims["run_id"]):
        return None
    request.state.auth_kind = _tc.KIND_TOOL_CALL
    request.state.api_key_agent = claims["agent"]
    request.state.api_key_run_id = claims["run_id"]
    request.state.via_tool = f"tool:{claims['tool']}"
    request.state.tool_call = claims
    # whoami must not read a tool call as holding any platform tool.
    request.state.frozen_tools = []
    return (f"agent:{claims['agent']}", "tools")


async def _current_run(request, run_id):
    from agentplatform.db import Run
    from agentplatform.authority import ensure_run_authority
    async with request.app.state.session_factory() as session:
        run = await session.get(Run, run_id)
        allowed = run is not None and await ensure_run_authority(session, run)
        # Persist silent lease revocations even when rejecting the credential.
        await session.commit()
        return allowed


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


def connector_identity(principal: str) -> str | None:
    """The Discord identity bound to a validated connector SA principal."""
    if principal == "connector-discord":
        return "discord-default"
    if principal.startswith("connector-discord:"):
        return principal.partition(":")[2]
    return None


async def workload_sa(request: Request, token: str) -> str | None:
    """The ServiceAccount name a projected token proves, or None. For the
    platform's own workloads (the broker, the executor), which hold no API
    identity of their own: a route that trusts one checks the name itself."""
    import time
    from agentplatform.apikeys import hash_token
    if not hasattr(request.app.state, "_sa_name_cache"):
        request.app.state._sa_name_cache = {}
    cache: dict = request.app.state._sa_name_cache
    h = hash_token(token)
    hit = cache.get(h)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    validator = getattr(request.app.state, "sa_validator", None)
    if validator is None:
        return None
    username = await validator(token)
    if not username:
        return None
    sa_name = username.rsplit(":", 1)[-1]
    cache[h] = (time.monotonic() + _SA_CACHE_TTL, sa_name)
    return sa_name


async def _validate_sa_token(request: Request, token: str) -> tuple[str, str, str | None] | None:
    """Resolve a projected SA token to (principal, role, agent) via TokenReview.

    Agent authorization is recomputed from its current grants. The connector
    has one fixed, narrow machine role so it can discover endpoint bindings
    without carrying a minted secret.
    """
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
    if sa_name == request.app.state.settings.connector_service_account:
        out = ("connector-discord", "connector", None)
        cache[h] = (time.monotonic() + _SA_CACHE_TTL, out)
        return out
    connector_prefix = request.app.state.settings.connector_service_account + "-"
    if sa_name.startswith(connector_prefix):
        identity_id = sa_name[len(connector_prefix):]
        async with request.app.state.session_factory() as session:
            identity = await session.get(ChatIdentity, identity_id)
        if identity is None or identity.connector != "discord":
            return None
        out = (f"connector-discord:{identity_id}", "connector", None)
        cache[h] = (time.monotonic() + _SA_CACHE_TTL, out)
        return out
    if not sa_name.startswith("agent-"):
        return None
    agent = sa_name[len("agent-"):]
    st = request.app.state
    await st.agent_store.reload()
    info = st.agent_store.get(agent)
    if info is None:
        return None
    role = platform_token_role(info.platform_tools)
    if role is None:
        return None       # no platform grant: nothing for this identity to be
    out = (f"sa:{agent}", role, agent)
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
    p = await _principal(request, creds.principal)
    # One 401 for "no such row", "no password on the row" (an API-key-only
    # principal is not a login) and "wrong password": the response must not
    # tell a guesser which principal names exist — so a miss still pays for
    # an argon2 verify rather than answering in a tenth of the time.
    try:
        ph.verify(p.password_hash if p is not None and p.password_hash
                  else _NO_SUCH_PRINCIPAL_HASH, creds.password)
    except VerifyMismatchError:
        raise HTTPException(401)
    if p is None or not p.password_hash:
        raise HTTPException(401)
    response.set_cookie("ap_session", _signer(request).dumps({"principal": p.name}),
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
