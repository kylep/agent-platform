"""Chat account metadata. Credentials remain in the existing secret store."""
import asyncio
import uuid

import httpx
from kubernetes import client as k8s, config as k8s_config

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentplatform.api import schemas as S
from agentplatform.api.auth import authenticate, connector_identity, require_admin, require_role
from agentplatform.db import AgentDef, AgentVersion, ChatIdentity, RelayBinding, SecretMeta
from agentplatform.agentdefs import next_version, snapshot_of
from agentplatform.authority import expired, authority_lock

class CredentialSafeRoute(APIRoute):
    """FastAPI normally includes rejected input in 422 responses; tokens must not echo."""
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                raise HTTPException(422, "Invalid account details. Check the display name, internal ID, and token.") from None

        return safe_handler


router = APIRouter(route_class=CredentialSafeRoute)


class EditIdentityIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    owner_agent: str | None = None
    token: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)


class CreateIdentityIn(BaseModel):
    id: str = Field(pattern=r"^discord-[a-z][a-z0-9-]{0,31}$")
    display_name: str = Field(min_length=1, max_length=128)
    secret_name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")


async def _configured(request: Request, row: ChatIdentity) -> bool:
    ref = row.secret_refs.get("bot_token")
    if not isinstance(ref, dict) or not isinstance(ref.get("secret"), str):
        return False
    secret = await request.app.state.secret_store.get(ref["secret"])
    return bool(secret and secret.get(ref.get("key", "")))


async def _agent_can_send(request: Request, identity_id: str) -> bool:
    agent = getattr(request.state, "api_key_agent", None)
    if not agent:
        return False
    async with request.app.state.session_factory() as session:
        identity = await session.get(ChatIdentity, identity_id)
        owner = await session.get(AgentDef, agent)
        return bool(identity and owner and owner.enabled and owner.agent_type == "persona"
                    and not owner.system_source and identity.owner_agent == agent)


@router.get("/api/chat-identities", response_model=list[S.ChatIdentityView])
async def list_chat_identities(request: Request, include_deleted: bool = False,
                               actor: str = Depends(require_admin)):
    """Admin inventory of identities and exact routes; never secret values."""
    async with request.app.state.session_factory() as session:
        query = select(ChatIdentity).order_by(ChatIdentity.connector, ChatIdentity.id)
        if not include_deleted:
            query = query.where(ChatIdentity.status != "deleted")
        rows = (await session.execute(query)).scalars().all()
        counts = dict((await session.execute(select(
            RelayBinding.identity_id, func.count(RelayBinding.id)
        ).where(RelayBinding.identity_id.is_not(None)).group_by(
            RelayBinding.identity_id))).all())
    result = []
    for row in rows:
        result.append({"id": row.id, "connector": row.connector,
                       "display_name": row.display_name, "status": row.status,
                       "secret_refs": row.secret_refs,
                       "configured": await _configured(request, row),
                       "bound_routes": counts.get(row.id, 0),
                       "owner_agent": row.owner_agent,
                       "ownership_generation": row.ownership_generation or 0,
                       "access_expires_at": row.access_expires_at,
                       "connected": row.status == "active" and not expired(row.access_expires_at)})
    return result


@router.post("/api/chat-identities", status_code=201,
             response_model=S.ChatIdentityView)
async def create_chat_identity(request: Request, body: CreateIdentityIn,
                               actor: str = Depends(require_admin)):
    """Register another Discord account; configured accounts need no manual activation."""
    if not body.display_name.strip():
        raise HTTPException(422, "display name cannot be blank")
    if body.secret_name != f"{body.id}-bot":
        raise HTTPException(422, "use the account-specific bot secret")
    if body.id == "discord-default":
        raise HTTPException(409, "default identity already exists")
    async with request.app.state.session_factory() as session:
        grants = (await session.execute(select(AgentDef.secrets))).scalars().all()
        if any(body.secret_name in (secrets or []) for secrets in grants):
            raise HTTPException(409, "Remove agent grants before making this secret an account credential")
        row = ChatIdentity(id=body.id, connector="discord",
                           display_name=body.display_name.strip(),
                           secret_refs={"bot_token": {"secret": body.secret_name,
                                                      "key": "token"}},
                           status="disabled")
        row.status = "active" if await _configured(request, row) else "disabled"
        session.add(row)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            raise HTTPException(409, "chat identity already exists")
    return {"id": row.id, "connector": row.connector,
            "display_name": row.display_name, "status": row.status,
            "secret_refs": row.secret_refs,
            "configured": await _configured(request, row), "bound_routes": 0}


def _apps(request: Request):
    api = getattr(request.app.state, "k8s_apps_v1", None)
    if api is None:
        try:
            k8s_config.load_incluster_config()
            api = request.app.state.k8s_apps_v1 = k8s.AppsV1Api()
        except k8s_config.ConfigException:
            return None
    return api


def _deployment(request: Request, identity_id: str) -> str:
    default = request.app.state.settings.connector_service_account
    return default if identity_id == "discord-default" else f"{default.removesuffix('-discord')}-{identity_id}"


async def _restart(request: Request, identity_id: str) -> str:
    api = _apps(request)
    if api is None:
        return "Token saved. Restart this bot's connector after deployment."
    try:
        await asyncio.to_thread(api.read_namespaced_deployment,
            _deployment(request, identity_id), request.app.state.settings.k8s_namespace, _request_timeout=10)
        await asyncio.to_thread(api.patch_namespaced_deployment,
            _deployment(request, identity_id), request.app.state.settings.k8s_namespace,
            {"spec": {"template": {"metadata": {"annotations": {
                "agent-platform/token-revision": uuid.uuid4().hex}}}}}, _request_timeout=10)
        return "Token saved; this bot's connector is restarting."
    except k8s.exceptions.ApiException as exc:
        if exc.status == 404:
            return "Token saved. This bot's connector has not been deployed yet."
        raise HTTPException(503, "Token saved, but connector restart failed. Retry saving to restart it.") from None
    except Exception:
        raise HTTPException(503, "Token saved, but connector restart failed. Retry saving to restart it.") from None


async def _credential_ref(session, row: ChatIdentity) -> tuple[str, str]:
    """Lifecycle writes may touch only this identity's own credential."""
    ref = row.secret_refs.get("bot_token", {})
    expected = "discord-bot" if row.id == "discord-default" else f"{row.id}-bot"
    if ref != {"secret": expected, "key": "token"}:
        raise HTTPException(409, "Account uses a custom secret reference; migrate it before editing or deleting credentials.")
    others = (await session.execute(select(ChatIdentity).where(
        ChatIdentity.id != row.id, ChatIdentity.status != "deleted"))).scalars()
    if any(other.secret_refs.get("bot_token", {}).get("secret") == expected for other in others):
        raise HTTPException(409, "Credential is shared with another account; separate it before changing this account.")
    return expected, "token"


@router.patch("/api/chat-identities/{identity_id}")
async def edit_chat_identity(request: Request, identity_id: str, body: EditIdentityIn,
                             actor: str = Depends(require_admin)):
    name = body.display_name.strip()
    if not name or (body.token is not None and not body.token.strip()):
        raise HTTPException(422, "Name and replacement token cannot be blank")
    async with request.app.state.session_factory() as session:
        await authority_lock(session)
        row = await session.get(ChatIdentity, identity_id, with_for_update=True)
        if row is None or row.status in ("deleted", "deleting"):
            raise HTTPException(404, "unknown chat identity")
        if body.token is not None:
            secret, key = await _credential_ref(session, row)
            data = await request.app.state.secret_store.get(secret) or {}
            await request.app.state.secret_store.set(secret, {**data, key: body.token.strip()})
            meta = await session.get(SecretMeta, secret)
            if meta is None:
                meta = SecretMeta(name=secret)
                session.add(meta)
            meta.status = "unprobed"
        from agentplatform.authority import assign_owner
        if "owner_agent" in body.model_fields_set:
            await assign_owner(session, row, body.owner_agent)
        if body.token is not None:
            prior = row.owner_agent
            if prior:
                owner = await session.get(AgentDef, prior, with_for_update=True)
                owner.authorization_generation = (owner.authorization_generation or 0) + 1
            row.ownership_generation = (row.ownership_generation or 0) + 1
            row.access_expires_at = None
            row.lease_invalidated = False
        row.display_name = name
        row.status = "active" if await _configured(request, row) else "disabled"
        await session.commit()
    await request.app.state.agent_store.reload()
    detail = await _restart(request, identity_id) if body.token is not None else "Account updated."
    return {"id": identity_id, "detail": detail}


@router.post("/api/chat-identities/{identity_id}/verify")
async def verify_chat_identity(request: Request, identity_id: str,
                               actor: str = Depends(require_admin)):
    """Explicit read-only provider checks. Never send a test message or echo errors/tokens."""
    checks = []
    async with request.app.state.session_factory() as session:
        await authority_lock(session)
        row = await session.get(ChatIdentity, identity_id, with_for_update=True)
        if row is None or row.status in ("deleted", "deleting"):
            raise HTTPException(404, "unknown chat identity")
        ref = row.secret_refs.get("bot_token", {})
        data = await request.app.state.secret_store.get(ref.get("secret", "")) or {}
        token = data.get(ref.get("key", ""))
        valid = False
        intent = False
        rejected = False
        if not token:
            checks.append({"ok": False, "detail": "Bot token is missing. Choose Edit to set it."})
        else:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get("https://discord.com/api/v10/applications/@me",
                                                headers={"Authorization": f"Bot {token}"})
                if response.status_code == 200:
                    app = response.json()
                    valid = True
                    bot = app.get("bot") or {}
                    checks.append({"ok": True, "detail": f"Discord authenticated: {bot.get('username', app.get('name', 'bot'))}."})
                    flags = int(app.get("flags_new") or app.get("flags", 0))
                    intent = bool(flags & ((1 << 18) | (1 << 19)))
                    checks.append({"ok": intent, "detail": "Message Content Intent enabled." if intent else
                        "Enable Message Content Intent under Bot in the Discord Developer Portal."})
                else:
                    rejected = response.status_code in (401, 403)
                    checks.append({"ok": False, "detail": "Discord rejected the bot token." if response.status_code in (401, 403) else
                        f"Discord check unavailable (HTTP {response.status_code}); try again later."})
            except (httpx.HTTPError, ValueError, TypeError):
                checks.append({"ok": False, "detail": "Discord could not be checked; try again later."})
        # Verification is also the migration path for previously paused accounts.
        if valid:
            row.status = "active" if intent else "disabled"
        elif rejected or not token:
            row.status = "disabled"
        await session.commit()
    api = _apps(request)
    if api is None:
        checks.append({"ok": None, "detail": "Bot process status unavailable outside Kubernetes."})
    else:
        try:
            deployment = await asyncio.to_thread(api.read_namespaced_deployment,
                _deployment(request, identity_id), request.app.state.settings.k8s_namespace,
                _request_timeout=10)
            ready = bool(deployment.status.ready_replicas)
            checks.append({"ok": ready, "detail": "Bot process is running. This does not prove its Discord gateway connection." if ready else
                "Bot process is not ready; check its logs."})
        except k8s.exceptions.ApiException as exc:
            checks.append({"ok": False, "detail": "Bot process has not been deployed yet." if exc.status == 404 else
                "Bot process status could not be checked."})
        except Exception:
            checks.append({"ok": None, "detail": "Bot process status could not be checked."})
    return {"id": identity_id, "checks": checks}


@router.delete("/api/chat-identities/{identity_id}")
async def delete_chat_identity(request: Request, identity_id: str,
                               actor: str = Depends(require_admin)):
    """Keep a tombstone so bootstrapping cannot resurrect the default identity.

    Deactivate first; credential deletion is retryable if the secret store fails.
    Historical bindings retain their identity and cannot fall back to another bot.
    """
    async with request.app.state.session_factory() as session:
        await authority_lock(session)
        row = await session.get(ChatIdentity, identity_id, with_for_update=True)
        if row is None:
            raise HTTPException(404, "unknown chat identity")
        secret, key = await _credential_ref(session, row)
        from agentplatform.authority import assign_owner
        await assign_owner(session, row, None)
        row.status = "deleting"
        bindings = (await session.execute(select(RelayBinding).where(
            RelayBinding.identity_id == identity_id))).scalars()
        for binding in bindings:
            binding.status = "disabled"
        agents = (await session.execute(select(AgentDef).where(
            AgentDef.discord_identity_id == identity_id).with_for_update())).scalars()
        for agent in agents:
            agent.discord_identity_id = None
            await session.flush()
            session.add(AgentVersion(agent=agent.name, version=await next_version(session, agent.name),
                                     snapshot=snapshot_of(agent), changed_by=actor,
                                     changed_via="admin:delete-chat-identity"))
        await session.commit()
    await request.app.state.agent_store.reload()
    data = await request.app.state.secret_store.get(secret) or {}
    remaining = {k: v for k, v in data.items() if k != key}
    try:
        # Remove the credential using the existing update permission. The empty
        # Kubernetes Secret object may remain, but holds no usable token.
        await request.app.state.secret_store.set(secret, remaining)
    except Exception:
        raise HTTPException(503, "Account disconnected, but credential removal failed. Retry Delete to finish cleanup.") from None
    # Stop the process as well as its platform transport. A later Helm upgrade
    # cannot restore credentials or reactivate the tombstoned identity.
    api = _apps(request)
    if api is not None:
        try:
            await asyncio.to_thread(api.read_namespaced_deployment,
                _deployment(request, identity_id), request.app.state.settings.k8s_namespace, _request_timeout=10)
            await asyncio.to_thread(api.patch_namespaced_deployment_scale,
                _deployment(request, identity_id), request.app.state.settings.k8s_namespace,
                {"spec": {"replicas": 0}}, _request_timeout=10)
        except k8s.exceptions.ApiException as exc:
            if exc.status != 404:
                raise HTTPException(503, "Account and credential removed, but bot shutdown failed. Retry Delete to finish cleanup.") from None
        except Exception:
            raise HTTPException(503, "Account and credential removed, but bot shutdown failed. Retry Delete to finish cleanup.") from None
    async with request.app.state.session_factory() as session:
        meta = await session.get(SecretMeta, secret)
        if meta:
            await session.delete(meta)
        row = await session.get(ChatIdentity, identity_id, with_for_update=True)
        row.status = "deleted"
        await session.commit()
    return {"id": identity_id, "detail": "Account deleted; credential removed. Chat history is preserved."}


@router.get("/api/chat-identities/{identity_id}/transport")
async def chat_identity_transport(request: Request, identity_id: str,
                                  caller: str = Depends(require_role(
                                      "connector", "tools", "relay", "admin"))):
    """Credential-free activation check for the connector and Tool broker."""
    ident = await authenticate(request)
    if getattr(request.state, "api_key_agent", None) and not await _agent_can_send(request, identity_id):
        raise HTTPException(403, "agent has not been granted this Discord identity")
    if ident and ident[1] == "connector" and connector_identity(ident[0]) != identity_id:
        raise HTTPException(403, "connector identity cannot inspect another account")
    async with request.app.state.session_factory() as session:
        row = await session.get(ChatIdentity, identity_id)
        if row is None:
            raise HTTPException(404, "unknown chat identity")
        return {"id": row.id,
                "active": row.status == "active" and await _configured(request, row)}
