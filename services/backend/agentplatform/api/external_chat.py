"""Authenticated connector observations and persona-owned messaging operations."""
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from agentplatform import external_chat as chat
from agentplatform.api.auth import authenticate, connector_identity
from agentplatform.db import AgentDef, ChatIdentity, Conversation, RelayMessage
from agentplatform.events import TOPIC_RELAY_MESSAGES
from agentplatform.relay_store import relay_message_payload, message_view

router = APIRouter(prefix="/api/external-chat", tags=["external-chat"])


async def connector(request):
    auth = await authenticate(request)
    identity = connector_identity(auth[0]) if auth and auth[1] == "connector" else None
    if not identity:
        raise HTTPException(403, "authenticated connector account required")
    return identity


async def persona(request):
    auth = await authenticate(request)
    agent = getattr(request.state, "api_key_agent", None)
    run_id = getattr(request.state, "api_key_run_id", None)
    if not auth or not agent or not run_id:
        raise HTTPException(403, "current persona run required")
    from agentplatform.authority import ensure_run_authority
    from agentplatform.db import Run
    async with request.app.state.session_factory() as session:
        row = await session.get(AgentDef, agent)
        run = await session.get(Run, run_id)
        allowed = bool(row and row.enabled and row.agent_type == "persona" and run
                       and await ensure_run_authority(session, run))
        await session.commit()
    if not allowed:
        raise HTTPException(403, "persona authority unavailable")
    return agent, run_id


def deny(exc):
    return HTTPException(403, str(exc))


class EndpointIn(BaseModel):
    external_ref: str = Field(min_length=1, max_length=256)
    kind: str = Field(default="channel", pattern="^(channel|thread|dm)$")
    display_name: str = Field(default="", max_length=256)
    can_read: bool = False
    can_history: bool = False
    can_send: bool = False


class SnapshotIn(BaseModel):
    ownership_generation: int
    sequence: int = Field(ge=1)
    provider_user_id: str | None = Field(default=None, max_length=128)
    endpoints: list[EndpointIn] = Field(max_length=20000)


@router.get("/connector/state")
async def connector_state(request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        account = await session.get(ChatIdentity, identity_id)
        if not account:
            raise HTTPException(404)
        try:
            await chat.owned_identity(session, identity_id, operational=False)
            active = True
        except chat.ExternalChatError:
            active = False
        return {"identity_id": identity_id, "active": active,
                "ownership_generation": account.ownership_generation,
                "permission_sequence": account.permission_sequence,
                "known_dm_refs": list((await session.execute(select(chat.ExternalEndpoint.external_ref).join(
                    chat.ExternalAccess, chat.ExternalAccess.endpoint_id == chat.ExternalEndpoint.id).where(
                    chat.ExternalAccess.identity_id == identity_id,
                    chat.ExternalEndpoint.kind == "dm"))).scalars())}


@router.post("/connector/disconnect")
async def disconnect(request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        from agentplatform.authority import authority_lock
        await authority_lock(session)
        account = (await session.execute(select(ChatIdentity).where(ChatIdentity.id == identity_id).with_for_update())).scalar_one_or_none()
        if account:
            await chat.invalidate(session, account)
            await session.commit()
    return {"ok": True}


@router.post("/connector/snapshot")
async def permission_snapshot(body: SnapshotIn, request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        try:
            account = await chat.snapshot(session, identity_id, body.ownership_generation,
                body.sequence, [ep.model_dump() for ep in body.endpoints], body.provider_user_id)
        except chat.ExternalChatError as exc:
            await session.commit()
            raise deny(exc)
        await session.commit()
        return {"ownership_generation": account.ownership_generation,
                "permission_sequence": account.permission_sequence, "lease_seconds": chat.LEASE_SECONDS}


class ObservationIn(BaseModel):
    ownership_generation: int
    external_ref: str = Field(min_length=1, max_length=256)
    provider_message_id: str = Field(min_length=1, max_length=128)
    author_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=100000)
    addressed: bool = False
    author_bot: bool = False
    co_mentioned: list[str] = Field(default_factory=list, max_length=8)
    mentioned_bot_ids: list[str] = Field(default_factory=list, max_length=16)


@router.post("/connector/observe")
async def observation(body: ObservationIn, request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        try:
            ep, msg, obs = await chat.observe(session, identity_id, body.ownership_generation, body.model_dump())
        except chat.ExternalChatError as exc:
            raise deny(exc)
        conv = await session.get(Conversation, ep.channel_id)
        payload = {**relay_message_payload(msg, conv), "external_identity_id": identity_id,
            "external_owner_generation": obs.ownership_generation, "external_observation_id": obs.id}
        await session.commit()
        # Replay is safe: the router deduplicates the persisted observation.
        # Publish failure yields 5xx so the connector retains/retries observation.
        await request.app.state.producer.publish(TOPIC_RELAY_MESSAGES, conv.id, payload, type="relay.message")
        return {"message_id": msg.id, "observation_id": obs.id}


def delivery_view(row, ep=None, *, include_claim=False):
    view = {"id": row.id, "identity_id": row.identity_id, "state": row.state,
            "receipts": row.receipts, "error": row.error}
    if ep:
        view.update(external_ref=ep.external_ref, chunks=row.chunks)
    if include_claim:
        view["claim_token"] = row.claim_token
    return view


@router.get("/connector/deliveries")
async def pending_deliveries(request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        from agentplatform.authority import authority_lock
        await authority_lock(session)
        rows = list((await session.execute(select(chat.ExternalDelivery).where(
            chat.ExternalDelivery.identity_id == identity_id,
            chat.ExternalDelivery.state.in_(("pending", "claimed"))).order_by(chat.ExternalDelivery.created_at).limit(100).with_for_update())).scalars())
        out = []
        for row in rows:
            if row.state == "claimed":
                if not chat.fresh(row.claimed_at + timedelta(seconds=chat.CLAIM_SECONDS)):
                    row.state, row.error = "unknown", "delivery claim expired; effect may have occurred"
                continue
            try:
                ep = await chat.delivery_authorized(session, row)
            except chat.ExternalChatError as exc:
                # A temporary disconnect preserves pending work. Permanent
                # generation/ownership revocation is terminal.
                account = await session.get(ChatIdentity, identity_id)
                agent = await session.get(AgentDef, row.agent)
                if not account or not agent or account.owner_agent != row.agent or not agent.enabled \
                        or account.ownership_generation != row.ownership_generation \
                        or agent.authorization_generation != row.authorization_generation:
                    row.state, row.error = "denied", str(exc)
                continue
            out.append(delivery_view(row, ep))
        await session.commit()
        return out


@router.post("/connector/deliveries/{request_id}/claim")
async def claim(request_id: str, request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        try:
            row, ep = await chat.claim_delivery(session, identity_id, request_id)
        except chat.ExternalChatError as exc:
            raise HTTPException(409, str(exc))
        await session.commit()
        return delivery_view(row, ep, include_claim=True)


class ClaimIn(BaseModel):
    claim_token: str


@router.post("/connector/deliveries/{request_id}/authorize")
async def authorize_chunk(request_id: str, body: ClaimIn, request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        row = await session.get(chat.ExternalDelivery, request_id)
        if not row or row.identity_id != identity_id or row.claim_token != body.claim_token or row.state != "claimed":
            raise HTTPException(403, "invalid active claim")
        try:
            await chat.delivery_authorized(session, row)
        except chat.ExternalChatError as exc:
            row.state = "unknown" if row.receipts else "denied"
            row.error = str(exc)
            await session.commit()
            raise deny(exc)
        await session.commit()
        return {"authorized": True}


class ReceiptIn(ClaimIn):
    index: int | None = None
    provider_message_id: str | None = Field(default=None, max_length=128)
    outcome: str | None = Field(default=None, pattern="^(failed|unknown)$")


@router.post("/connector/deliveries/{request_id}/receipt")
async def record_receipt(request_id: str, body: ReceiptIn, request: Request):
    identity_id = await connector(request)
    async with request.app.state.session_factory() as session:
        try:
            row, msg = await chat.receipt(session, identity_id, request_id, body.claim_token,
                body.index, body.provider_message_id, body.outcome)
        except chat.ExternalChatError as exc:
            raise deny(exc)
        conv = await session.get(Conversation, msg.channel_id) if msg else None
        await session.commit()
        if msg:
            from agentplatform.relay_store import publish_relay_message
            await publish_relay_message(request.app.state.producer, conv, msg)
        return delivery_view(row)


@router.get("/identities")
async def identities(request: Request):
    agent, _ = await persona(request)
    async with request.app.state.session_factory() as session:
        rows = (await session.execute(select(ChatIdentity).where(ChatIdentity.owner_agent == agent))).scalars()
        return [{"id": r.id, "provider": r.connector, "display_name": r.display_name,
            "ownership_generation": r.ownership_generation,
            "available": r.status == "active" and not r.lease_invalidated and chat.fresh(r.access_expires_at)} for r in rows]


@router.get("/endpoints")
async def endpoints(identity_id: str, request: Request):
    agent, _ = await persona(request)
    async with request.app.state.session_factory() as session:
        try:
            account = await chat.owned_identity(session, identity_id, agent)
        except chat.ExternalChatError as exc:
            raise deny(exc)
        rows = (await session.execute(select(chat.ExternalEndpoint, chat.ExternalAccess).join(
            chat.ExternalAccess, chat.ExternalAccess.endpoint_id == chat.ExternalEndpoint.id).where(
            chat.ExternalAccess.identity_id == identity_id))).all()
        return [{"id": ep.id, "provider": ep.provider, "external_ref": ep.external_ref,
            "channel_id": ep.channel_id, "kind": ep.kind, "display_name": ep.display_name,
            "can_read": a.can_read, "can_history": a.can_history, "can_send": a.can_send,
            "expires_at": a.expires_at.isoformat()} for ep, a in rows
            if a.ownership_generation == account.ownership_generation and a.can_read and a.can_history and chat.fresh(a.expires_at)]


@router.get("/messages")
async def messages(identity_id: str, external_ref: str, request: Request, limit: int = 30):
    agent, _ = await persona(request)
    async with request.app.state.session_factory() as session:
        try:
            account = await chat.owned_identity(session, identity_id, agent)
            ep, _ = await chat.endpoint_access(session, account, external_ref)
        except chat.ExternalChatError as exc:
            raise deny(exc)
        rows = (await session.execute(select(RelayMessage).where(RelayMessage.channel_id == ep.channel_id,
            RelayMessage.deleted_at.is_(None)).order_by(RelayMessage.created_at.desc()).limit(max(1, min(limit, 100))))).scalars()
        return [message_view(row) for row in rows]


@router.get("/scan")
async def scan(identity_id: str, request: Request):
    agent, _ = await persona(request)
    async with request.app.state.session_factory() as session:
        try:
            batch = await chat.scan_batch(session, agent, identity_id)
        except chat.ExternalChatError as exc:
            raise deny(exc)
        return {"batch_id": chat.scan_batch_id(batch) if batch else None,
                "messages": batch, "more_possible": len(batch) == chat.SCAN_LIMIT}


class ScanAckIn(BaseModel):
    identity_id: str
    batch_id: str = Field(min_length=64, max_length=64)


@router.post("/scan/ack")
async def scan_ack(body: ScanAckIn, request: Request):
    agent, _ = await persona(request)
    async with request.app.state.session_factory() as session:
        try:
            count = await chat.acknowledge_scan(session, agent, body.identity_id, body.batch_id)
        except chat.ExternalChatError as exc:
            raise HTTPException(409, str(exc))
        await session.commit()
        return {"acknowledged": count}


class SendIn(BaseModel):
    identity_id: str
    external_ref: str = Field(min_length=1, max_length=256)
    text: str = Field(min_length=1, max_length=100000)
    answer_to: str | None = Field(default=None, max_length=32)


@router.post("/send", status_code=202)
async def send(body: SendIn, request: Request):
    agent, run_id = await persona(request)
    async with request.app.state.session_factory() as session:
        try:
            row = await chat.queue_send(session, agent=agent, run_id=run_id, **body.model_dump())
        except chat.ExternalChatError as exc:
            await session.commit()
            raise deny(exc)
        await session.commit()
        return delivery_view(row)


@router.get("/deliveries/{request_id}")
async def delivery(request_id: str, request: Request):
    agent, _ = await persona(request)
    async with request.app.state.session_factory() as session:
        row = await session.get(chat.ExternalDelivery, request_id)
        if not row or row.agent != agent:
            raise HTTPException(404)
        try:
            account = await chat.owned_identity(session, row.identity_id, agent)
            ep = await session.get(chat.ExternalEndpoint, row.endpoint_id)
            await chat.endpoint_access(session, account, ep.external_ref)
        except chat.ExternalChatError as exc:
            raise deny(exc)
        return delivery_view(row)
