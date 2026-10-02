"""Tool-call credentials (docs/design/39, "Tool-call credentials").

A tool that declares `app_access` reaches App data through a credential minted
for ONE call: the broker exchanges the caller's bearer and run JWT for it, the
executor holds it, and the tool process never does. The claims are a ceiling,
not a grant: `app_scope` bounds what the call may attempt, and every record
operation is still checked against the App's current definitions.

What makes a copied credential useless:
- it is signed with its own key (`tool-call-jwt-key`), audience and `kind`, so
  neither a run JWT nor anything else the platform signs passes this verifier;
- `cnf` names the executor's ServiceAccount, and the API requires that
  workload's own token alongside it, the same sender constraint run JWTs carry;
- `call_id` is live only between mint and return: its row here is revoked when
  the broker gets the result, and `exp` is the tool's timeout.

Kyle's page actions (Release 2) get a sibling claim set, `page_intent`: the
principal is `kyle`, it names one intent, action, targets and budget, and it
has no run. Its verifier branch is here so the shape is fixed; minting it from
a confirmed intent is R2's.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import jwt
from sqlalchemy import delete, select

from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import AppDataApp, AppDataToolCall
from agentplatform.toolregistry import APP_VERBS, AppAccess

log = logging.getLogger("appdata.credentials")

SECRET_NAME = "tool-call-jwt-key"
ISSUER = "ap-api"
# Not run JWTs' `agent-platform`: a token minted for one purpose never
# verifies as the other, whatever key signed it.
AUDIENCE = "agent-platform/tool-call"
ALGORITHM = "ES256"
KIND_TOOL_CALL = "tool_call"
KIND_PAGE_INTENT = "page_intent"
HEADER = "x-ap-tool-call"
CALL_ID_HEADER = "x-ap-tool-call-id"
# Beyond the tool's own timeout: the executor stages files and starts the
# process after the mint, and the broker revokes at return either way.
EXP_SLACK_SECONDS = 30
# Rows stay this long past expiry, for the audit trail, then go.
_KEEP_EXPIRED = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- signing ---------------------------------------------------------------------

def mint_tool_call(private_key_pem: str, *, call_id: str, run_id: str, agent: str,
                   tool: str, action: str, app_scope: list[dict], cnf_sa: str,
                   ttl_seconds: int, jti: str | None = None) -> tuple[str, dict]:
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + ttl_seconds,
        "kind": KIND_TOOL_CALL, "jti": jti or uuid.uuid4().hex,
        "call_id": call_id, "run_id": run_id, "agent": agent, "tool": tool,
        "action": action, "app_scope": app_scope, "cnf": {"sa": cnf_sa},
    }
    return jwt.encode(claims, private_key_pem, algorithm=ALGORITHM), claims


def mint_page_intent(private_key_pem: str, *, intent_id: str, action: str,
                     targets: list, budget: dict, cnf_sa: str, ttl_seconds: int,
                     jti: str | None = None) -> tuple[str, dict]:
    """The claim shape only. TODO(R2): mint from a confirmed page intent
    (design 39, "Tool actions") and record its jti like a tool call's; until
    then nothing calls this outside the verifier's tests."""
    now = int(time.time())
    claims = {
        "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + ttl_seconds,
        "kind": KIND_PAGE_INTENT, "jti": jti or uuid.uuid4().hex,
        "principal": "kyle", "intent_id": intent_id, "action": action,
        "targets": targets, "budget": budget, "cnf": {"sa": cnf_sa},
    }
    return jwt.encode(claims, private_key_pem, algorithm=ALGORITHM), claims


def _str(claims: dict, key: str) -> bool:
    return isinstance(claims.get(key), str) and bool(claims[key])


def _valid_scope(scope) -> bool:
    if not isinstance(scope, list):
        return False
    for entry in scope:
        if not (isinstance(entry, dict) and isinstance(entry.get("app_id"), str)
                and isinstance(entry.get("collections"), list)
                and all(isinstance(c, str) for c in entry["collections"])
                and isinstance(entry.get("verbs"), list)
                and set(entry["verbs"]) <= set(APP_VERBS)):
            return False
    return True


def _tool_call_shape(claims: dict) -> bool:
    return (all(_str(claims, k) for k in ("call_id", "run_id", "agent", "tool"))
            and isinstance(claims.get("action"), str)
            and _valid_scope(claims.get("app_scope"))
            # A tool call acts as its agent; it never names a principal of its own.
            and "principal" not in claims)


def _page_intent_shape(claims: dict) -> bool:
    return (claims.get("principal") == "kyle"
            and _str(claims, "intent_id") and _str(claims, "action")
            and isinstance(claims.get("targets"), list)
            and isinstance(claims.get("budget"), dict)
            # Kyle's intent is not a run, and must not pass for a tool call.
            and not any(k in claims for k in ("run_id", "call_id", "agent", "tool")))


_SHAPES = {KIND_TOOL_CALL: _tool_call_shape, KIND_PAGE_INTENT: _page_intent_shape}


def verify(public_key_pem: str, token: str, *, expected_sa: str,
           kinds: tuple[str, ...] = (KIND_TOOL_CALL,)) -> dict | None:
    """Signature, audience, expiry, kind and claim shape, and `cnf` against
    the workload that presented it. Returns the claims, or None. Liveness (not
    revoked) is `is_live`'s, because it needs the database."""
    try:
        claims = jwt.decode(token, public_key_pem, algorithms=[ALGORITHM],
                            audience=AUDIENCE, issuer=ISSUER,
                            options={"require": ["exp", "iat", "jti"]})
    except jwt.InvalidTokenError as e:
        log.debug("tool-call credential rejected: %s", e)
        return None
    kind = claims.get("kind")
    if kind not in kinds or not _SHAPES[kind](claims):
        log.warning("tool-call credential of kind %r refused", kind)
        return None
    if (claims.get("cnf") or {}).get("sa") != expected_sa:
        log.warning("tool-call credential cnf mismatch: %s presented by %s",
                    (claims.get("cnf") or {}).get("sa"), expected_sa)
        return None
    return claims


# --- keys --------------------------------------------------------------------------

async def keypair(app_state) -> dict[str, str]:
    """The API's tool-call signing pair, generated on first use. The API both
    mints and verifies, so unlike the run-JWT pair it holds the private half."""
    keys = getattr(app_state, "_tool_call_keys", None)
    if keys:
        return keys
    # No await between the read and the set, so one lock per app.
    lock = getattr(app_state, "_tool_call_keys_lock", None)
    if lock is None:
        lock = app_state._tool_call_keys_lock = asyncio.Lock()
    async with lock:
        keys = getattr(app_state, "_tool_call_keys", None)
        if keys:
            return keys
        stored = await app_state.secret_store.get(SECRET_NAME)
        if not (stored or {}).get("private_key") or not stored.get("public_key"):
            from agentplatform import runjwt
            stored = runjwt.generate_keypair()
            await app_state.secret_store.set(SECRET_NAME, stored)
        app_state._tool_call_keys = {"private_key": stored["private_key"],
                                     "public_key": stored["public_key"]}
        return app_state._tool_call_keys


# --- revocation ----------------------------------------------------------------

async def prune(session, *, now: datetime | None = None) -> int:
    """Drop credential rows a day past expiry. Doesn't commit; returns how
    many went. A mint does this too, and the dispatcher's hourly sweep keeps
    the table short when nothing is being minted."""
    result = await session.execute(delete(AppDataToolCall).where(
        AppDataToolCall.expires_at < (now or _now()) - _KEEP_EXPIRED))
    return result.rowcount or 0


async def record(session, claims: dict) -> None:
    """Note a freshly minted credential's jti, and drop rows long expired."""
    await prune(session)
    session.add(AppDataToolCall(
        jti=claims["jti"], call_id=claims.get("call_id") or claims.get("intent_id"),
        kind=claims["kind"], run_id=claims.get("run_id"), agent=claims.get("agent"),
        tool=claims.get("tool"), action=claims.get("action") or "",
        expires_at=datetime.fromtimestamp(claims["exp"], timezone.utc)))


async def revoke(session, jti: str) -> bool:
    row = await session.get(AppDataToolCall, jti)
    if row is None:
        return False
    if row.revoked_at is None:
        row.revoked_at = _now()
    return True


async def is_live(session, claims: dict) -> bool:
    """The credential's call is still running: its row exists, names the
    same call, and was neither revoked at return nor left to expire."""
    row = await session.get(AppDataToolCall, claims.get("jti"))
    return (row is not None and row.revoked_at is None
            and row.call_id == claims.get("call_id")
            and _aware(row.expires_at) > _now())


# --- scope --------------------------------------------------------------------------

def bind_roles(ctx, roles: list[str]) -> list[str]:
    """The App's collections a tool's declared roles stand for.

    TODO(R1b): roles bind through the App's App tool fact (design 39, "Tool
    views", "The authority model"), which Kyle approves per App. Until it
    exists a role binds the collection of the same name, which is never wider:
    an App with no collection of that name contributes nothing."""
    return [r for r in roles if r in ctx.bundle.collections]


def _holds(access, verb: str) -> bool:
    if verb == "read":
        return access.can_see_rows()
    return access.can_verb(verb) and access.tool_allowed(verb)


async def compute_app_scope(session, *, agent: str, tool: str,
                            app_access: AppAccess) -> list[dict]:
    """The caller's own reach, acting through the tool, cut down to what the
    manifest declares: per active App, the bound collections and the verbs
    the agent holds on them. Collections with the same verb set share an
    entry; an App the agent can't touch at all isn't listed."""
    from agentplatform.appdata.records import load_app
    caller = Caller(principal=f"agent:{agent}", via_tool=f"tool:{tool}")
    apps = (await session.execute(select(AppDataApp).where(
        AppDataApp.status == "active").order_by(AppDataApp.name))).scalars().all()
    scope: list[dict] = []
    for app in apps:
        try:
            ctx = await load_app(session, app.id)
        except RecordError:
            # Published state that no longer validates serves nobody.
            continue
        groups: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for name in bind_roles(ctx, app_access.roles):
            access = ctx.access(ctx.bundle.collections[name], caller)
            held = tuple(v for v in APP_VERBS if v in app_access.verbs and _holds(access, v))
            if held:
                groups[held].append(name)
        for verbs, collections in groups.items():
            scope.append({"app_id": app.id, "collections": sorted(collections),
                          "verbs": list(verbs)})
    return scope


def scope_allows(scope: list[dict], app_id: str, collection: str, verb: str) -> bool:
    return any(entry["app_id"] == app_id and collection in entry["collections"]
               and verb in entry["verbs"] for entry in scope)


# --- what app_data routes use ---------------------------------------------------

def tool_call_caller(request) -> Caller | None:
    """The records engine's caller for a request authenticated by a tool-call
    credential, or None for any other caller (A9's routes resolve those). It
    acts as its agent, through its tool, so writes are stamped
    `author = agent:<name>, via = tool:<name>`."""
    state = request.state
    if getattr(state, "auth_kind", None) != KIND_TOOL_CALL:
        return None
    return Caller(principal=f"agent:{state.api_key_agent}", via_tool=state.via_tool)


def require_plan_scope(request, app_id: str, plan) -> None:
    """A delete is no wider than the credential either: the records it would
    unlink in other collections are updates there, so a tool-call caller
    needs `update` on each, or the whole delete is refused before anything
    is written. (R2's cascade will need `delete` on its collections.)"""
    state = request.state
    if getattr(state, "auth_kind", None) != KIND_TOOL_CALL:
        return
    scope = state.tool_call.get("app_scope") or []
    missing = sorted({c for c, _, _ in plan.unlinks
                      if not scope_allows(scope, app_id, c, "update")})
    if missing:
        raise RecordError("AD-OUT-OF-SCOPE",
                          f"this tool call may not update {', '.join(missing)} in App "
                          f"{app_id}, which this delete would unlink", 403,
                          {"unlinks": missing})


def require_scope(request, app_id: str, collection: str, verb: str) -> None:
    """Never wider than the credential: a tool-call caller may touch only
    what its `app_scope` lists. Every other caller is the route's to judge."""
    state = request.state
    if getattr(state, "auth_kind", None) != KIND_TOOL_CALL:
        return
    if not scope_allows(state.tool_call.get("app_scope") or [], app_id, collection, verb):
        raise RecordError("AD-OUT-OF-SCOPE",
                          f"this tool call may not {verb} {collection} in App {app_id}", 403)
