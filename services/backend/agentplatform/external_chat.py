"""Account-owned external chat. Durable state lives in the existing database.

Every caller supplies authenticated identity, never authority from a bus payload.
The connector polls the durable queue; Kafka remains the Relay routing/feed bus.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import uuid

from sqlalchemy import DateTime, Integer, JSON, String, Text, UniqueConstraint, and_, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from agentplatform.db import Base, AgentDef, ChatIdentity, Conversation, RelayMessage, Run, utcnow

LEASE_SECONDS = 180
CLAIM_SECONDS = 120


def ident():
    return uuid.uuid4().hex


def fresh(value):
    return value is not None and value.replace(tzinfo=timezone.utc) > utcnow()


class ExternalEndpoint(Base):
    __tablename__ = "external_endpoints"
    __table_args__ = (UniqueConstraint("provider", "external_ref"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    provider: Mapped[str] = mapped_column(String(32))
    external_ref: Mapped[str] = mapped_column(String(256))
    channel_id: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(24), default="channel")
    display_name: Mapped[str] = mapped_column(String(256), default="")


class ExternalAccess(Base):
    __tablename__ = "external_access"
    identity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    can_read: Mapped[bool] = mapped_column(default=False)
    can_history: Mapped[bool] = mapped_column(default=False)
    can_send: Mapped[bool] = mapped_column(default=False)
    ownership_generation: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExternalMessage(Base):
    __tablename__ = "external_messages"
    endpoint_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider_message_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    message_id: Mapped[str] = mapped_column(String(32), unique=True)


class ExternalObservation(Base):
    __tablename__ = "external_observations"
    __table_args__ = (UniqueConstraint("endpoint_id", "provider_message_id", "identity_id", "ownership_generation"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    endpoint_id: Mapped[str] = mapped_column(String(32))
    provider_message_id: Mapped[str] = mapped_column(String(128))
    identity_id: Mapped[str] = mapped_column(String(64))
    ownership_generation: Mapped[int] = mapped_column(Integer)
    message_id: Mapped[str] = mapped_column(String(32))
    addressed: Mapped[bool] = mapped_column(default=False)
    author_bot: Mapped[bool] = mapped_column(default=False)
    co_mentioned: Mapped[list | None] = mapped_column(JSON, nullable=True)
    mentioned_bot_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)


class ExternalScanCursor(Base):
    """Acknowledged ambient human activity for one owned channel generation."""
    __tablename__ = "external_scan_cursors"
    identity_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    endpoint_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    ownership_generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_id: Mapped[str] = mapped_column(String(32))


class ExternalDelivery(Base):
    __tablename__ = "external_deliveries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=ident)
    identity_id: Mapped[str] = mapped_column(String(64), index=True)
    endpoint_id: Mapped[str] = mapped_column(String(32))
    agent: Mapped[str] = mapped_column(String(128))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    authorization_generation: Mapped[int] = mapped_column(Integer)
    ownership_generation: Mapped[int] = mapped_column(Integer)
    answer_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    answer_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    chunks: Mapped[list] = mapped_column(JSON, default=list)
    receipts: Mapped[dict] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    claim_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    error: Mapped[str] = mapped_column(Text, default="")


class ExternalChatError(ValueError):
    pass


async def owned_identity(session, identity_id, agent=None, *, operational=True):
    row = await session.get(ChatIdentity, identity_id)
    owner = await session.get(AgentDef, row.owner_agent) if row and row.owner_agent else None
    if not row or row.status != "active" or not owner or not owner.enabled or owner.agent_type != "persona":
        raise ExternalChatError("account has no enabled persona owner")
    if agent is not None and row.owner_agent != agent:
        raise ExternalChatError("account is not owned by this persona")
    if operational and (row.lease_invalidated or not fresh(row.access_expires_at)):
        raise ExternalChatError("account permission inventory is unavailable or stale")
    return row


async def endpoint_access(session, identity, external_ref, *, send=False):
    ep = (await session.execute(select(ExternalEndpoint).where(
        ExternalEndpoint.provider == identity.connector,
        ExternalEndpoint.external_ref == external_ref))).scalar_one_or_none()
    access = await session.get(ExternalAccess, (identity.id, ep.id)) if ep else None
    if not access or not access.can_read or not access.can_history or not fresh(access.expires_at) \
            or access.ownership_generation != identity.ownership_generation or (send and not access.can_send):
        raise ExternalChatError("endpoint permission denied")
    return ep, access


async def can_read_channel(session, agent, channel_id):
    row = await session.get(AgentDef, agent)
    if not row or not row.enabled:
        return False
    if row.agent_type == "worker" and row.external_observer:
        return True
    if row.agent_type != "persona":
        return False
    ep = (await session.execute(select(ExternalEndpoint).where(ExternalEndpoint.channel_id == channel_id))).scalar_one_or_none()
    if not ep:
        return False
    identities = (await session.execute(select(ChatIdentity).where(ChatIdentity.owner_agent == agent))).scalars()
    for account in identities:
        try:
            await owned_identity(session, account.id, agent)
            await endpoint_access(session, account, ep.external_ref)
            return True
        except ExternalChatError:
            continue
    return False


async def invalidate(session, account):
    from agentplatform.authority import authority_lock
    await authority_lock(session)
    if not account.lease_invalidated:
        owner = await session.get(AgentDef, account.owner_agent) if account.owner_agent else None
        if owner:
            owner.authorization_generation += 1
    account.lease_invalidated = True
    account.access_expires_at = utcnow()


async def snapshot(session, identity_id, generation, sequence, endpoints, provider_user_id=None):
    from agentplatform.authority import authority_lock
    await authority_lock(session)
    # Use the shared agent -> accounts lock order before the account lock.
    account = await session.get(ChatIdentity, identity_id)
    from agentplatform.authority import current_generation
    if account and account.owner_agent:
        await current_generation(session, account.owner_agent)
    account = (await session.execute(select(ChatIdentity).where(ChatIdentity.id == identity_id).with_for_update())).scalar_one_or_none()
    if not account or generation != account.ownership_generation or sequence <= account.permission_sequence:
        raise ExternalChatError("stale permission snapshot")
    if provider_user_id:
        account.provider_user_id = provider_user_id
    await owned_identity(session, identity_id, operational=False)
    # Process silent lease loss before a refresh could make it disappear.
    from agentplatform.authority import current_generation
    await current_generation(session, account.owner_agent)
    previous = list((await session.execute(select(ExternalAccess).where(ExternalAccess.identity_id == identity_id))).scalars())
    previous_map = {a.endpoint_id: a for a in previous}
    seen = set()
    expiry = utcnow() + timedelta(seconds=LEASE_SECONDS)
    decreased = False
    for item in endpoints:
        ep = (await session.execute(select(ExternalEndpoint).where(
            ExternalEndpoint.provider == account.connector,
            ExternalEndpoint.external_ref == item["external_ref"]).with_for_update())).scalar_one_or_none()
        if ep is None:
            # Unique constraints plus a savepoint handle two accounts discovering
            # the same endpoint concurrently without duplicating the mirror.
            from sqlalchemy.exc import IntegrityError
            try:
                async with session.begin_nested():
                    conv = Conversation(connector=account.connector, home="external", kind="channel",
                        open=False, title=item.get("display_name", ""), reply_mode="linear", dispatch_mode="mentions")
                    session.add(conv)
                    await session.flush()
                    ep = ExternalEndpoint(provider=account.connector, external_ref=item["external_ref"],
                        channel_id=conv.id, kind=item.get("kind", "channel"), display_name=item.get("display_name", ""))
                    session.add(ep)
                    await session.flush()
            except IntegrityError:
                ep = (await session.execute(select(ExternalEndpoint).where(
                    ExternalEndpoint.provider == account.connector,
                    ExternalEndpoint.external_ref == item["external_ref"]))).scalar_one()
        seen.add(ep.id)
        access = previous_map.get(ep.id)
        permissions = {k: bool(item.get(k, False)) for k in ("can_read", "can_history", "can_send")}
        if access is None:
            access = ExternalAccess(identity_id=identity_id, endpoint_id=ep.id, expires_at=expiry)
            session.add(access)
        else:
            decreased |= any(getattr(access, k) and not value for k, value in permissions.items())
        for key, value in permissions.items():
            setattr(access, key, value)
        access.expires_at = expiry
        access.ownership_generation = generation
    for access in previous:
        if access.endpoint_id not in seen:
            decreased |= access.can_read or access.can_history or access.can_send
            access.can_read = access.can_history = access.can_send = False
            access.expires_at = utcnow()
    if decreased:
        await invalidate(session, account)
    account.permission_sequence = sequence
    account.access_expires_at = expiry
    account.lease_invalidated = False
    await session.flush()
    return account


async def mirror(session, ep, provider_message_id, author, text, *, run_id=None):
    canonical = await session.get(ExternalMessage, (ep.id, provider_message_id))
    if canonical:
        return await session.get(RelayMessage, canonical.message_id)
    from agentplatform.relay_store import post_relay_message
    from sqlalchemy.exc import IntegrityError
    try:
        async with session.begin_nested():
            conv = await session.get(Conversation, ep.channel_id)
            msg = await post_relay_message(session, conv, author=author, body=text,
                mentions=[], external_message_id=provider_message_id, run_id=run_id)
            session.add(ExternalMessage(endpoint_id=ep.id, provider_message_id=provider_message_id, message_id=msg.id))
            await session.flush()
        return msg
    except IntegrityError:
        canonical = await session.get(ExternalMessage, (ep.id, provider_message_id))
        return await session.get(RelayMessage, canonical.message_id)


async def observe(session, identity_id, generation, data):
    account = await owned_identity(session, identity_id)
    if generation != account.ownership_generation:
        raise ExternalChatError("stale owner generation")
    ep, _ = await endpoint_access(session, account, data["external_ref"])
    msg = await mirror(session, ep, data["provider_message_id"],
        f"{account.connector}:{data['author_id']}", data["text"])
    observation = (await session.execute(select(ExternalObservation).where(
        ExternalObservation.endpoint_id == ep.id,
        ExternalObservation.provider_message_id == data["provider_message_id"],
        ExternalObservation.identity_id == identity_id,
        ExternalObservation.ownership_generation == generation))).scalar_one_or_none()
    if observation is None:
        from sqlalchemy.exc import IntegrityError
        try:
            async with session.begin_nested():
                observation = ExternalObservation(endpoint_id=ep.id,
                    provider_message_id=data["provider_message_id"], identity_id=identity_id,
                    ownership_generation=generation, message_id=msg.id,
                    addressed=data.get("addressed", False),
                    author_bot=data.get("author_bot", False),
                    co_mentioned=data.get("co_mentioned", []),
                    mentioned_bot_ids=data.get("mentioned_bot_ids", []))
                session.add(observation)
                await session.flush()
        except IntegrityError:
            observation = (await session.execute(select(ExternalObservation).where(
                ExternalObservation.endpoint_id == ep.id,
                ExternalObservation.provider_message_id == data["provider_message_id"],
                ExternalObservation.identity_id == identity_id,
                ExternalObservation.ownership_generation == generation))).scalar_one()
    return ep, msg, observation


SCAN_LIMIT = 20
SCAN_FIRST_LOOKBACK = timedelta(hours=24)


async def scan_batch(session, agent: str, identity_id: str, limit: int = SCAN_LIMIT) -> list[dict]:
    """Oldest unacknowledged ambient human posts in currently owned guild rooms.

    Reads the mirror, not Discord again. Every endpoint is checked against a
    fresh permission lease and ownership generation on each call. DMs and
    threads are deliberately out of scope; addressed messages already have
    their own reply path.
    """
    account = await owned_identity(session, identity_id, agent)
    if account.connector != "discord":
        raise ExternalChatError("ambient scan currently supports Discord")
    accesses = (await session.execute(select(ExternalEndpoint, ExternalAccess).join(
        ExternalAccess, ExternalAccess.endpoint_id == ExternalEndpoint.id).where(
        ExternalAccess.identity_id == identity_id, ExternalEndpoint.kind == "channel",
        ExternalAccess.ownership_generation == account.ownership_generation,
        ExternalAccess.can_read.is_(True), ExternalAccess.can_history.is_(True),
        ExternalAccess.expires_at > utcnow()))).all()
    first_at = utcnow() - SCAN_FIRST_LOOKBACK
    batch = []
    for ep, _ in accesses:
        cursor = await session.get(ExternalScanCursor,
            (identity_id, ep.id, account.ownership_generation))
        boundary = cursor.last_at if cursor else first_at
        after_cursor = (or_(RelayMessage.created_at > boundary,
                            and_(RelayMessage.created_at == boundary,
                                 RelayMessage.id > cursor.last_id)) if cursor
                        else RelayMessage.created_at >= boundary)
        stmt = (select(RelayMessage).join(ExternalObservation,
                ExternalObservation.message_id == RelayMessage.id).where(
            ExternalObservation.identity_id == identity_id,
            ExternalObservation.endpoint_id == ep.id,
            ExternalObservation.ownership_generation == account.ownership_generation,
            ExternalObservation.addressed.is_(False),
            ExternalObservation.author_bot.is_(False),
            RelayMessage.deleted_at.is_(None), after_cursor)
            .order_by(RelayMessage.created_at, RelayMessage.id).limit(limit))
        for msg in (await session.execute(stmt)).scalars():
            batch.append({"endpoint_id": ep.id, "external_ref": ep.external_ref,
                          "room": ep.display_name, "message_id": msg.id,
                          "author": msg.author, "text": (msg.body or "")[:1200],
                          "created_at": msg.created_at.isoformat()})
    batch.sort(key=lambda m: (m["created_at"], m["message_id"]))
    return batch[:limit]


def scan_batch_id(batch: list[dict]) -> str:
    return hashlib.sha256("|".join(m["message_id"] for m in batch).encode()).hexdigest()


async def acknowledge_scan(session, agent: str, identity_id: str, batch_id: str) -> int:
    """Advance only over the current, complete page; retries before ack replay."""
    from agentplatform.authority import authority_lock
    await authority_lock(session)
    account = await owned_identity(session, identity_id, agent)
    batch = await scan_batch(session, agent, identity_id)
    if not batch or scan_batch_id(batch) != batch_id:
        raise ExternalChatError("scan batch changed; read it again before acknowledging")
    last_by_endpoint = {item["endpoint_id"]: item for item in batch}
    for endpoint_id, item in last_by_endpoint.items():
        cursor = await session.get(ExternalScanCursor,
            (identity_id, endpoint_id, account.ownership_generation))
        if cursor is None:
            cursor = ExternalScanCursor(identity_id=identity_id, endpoint_id=endpoint_id,
                ownership_generation=account.ownership_generation,
                last_at=datetime.fromisoformat(item["created_at"]), last_id=item["message_id"])
            session.add(cursor)
        else:
            cursor.last_at = datetime.fromisoformat(item["created_at"])
            cursor.last_id = item["message_id"]
    await session.flush()
    return len(batch)


async def has_scan_activity(session, agent: str) -> bool:
    """Cheap schedule gate: no model run when all owned rooms are quiet."""
    identities = (await session.execute(select(ChatIdentity.id).where(
        ChatIdentity.owner_agent == agent, ChatIdentity.status == "active",
        ChatIdentity.connector == "discord"))).scalars()
    for identity_id in identities:
        try:
            if await scan_batch(session, agent, identity_id, limit=1):
                return True
        except ExternalChatError:
            continue
    return False


async def queue_send(session, *, agent, run_id, identity_id, external_ref, text, answer_to=None, automatic=False):
    from agentplatform.authority import current_generation, ensure_run_authority
    generation = await current_generation(session, agent)
    if generation is None:
        raise ExternalChatError("persona is unavailable")
    run = await session.get(Run, run_id) if run_id else None
    if not run or run.agent != agent or not await ensure_run_authority(session, run):
        raise ExternalChatError("current persona run required")
    account = await owned_identity(session, identity_id, agent)
    ep, _ = await endpoint_access(session, account, external_ref, send=True)
    # Lock the endpoint while checking the addressed-turn fence.
    await session.execute(select(ExternalEndpoint).where(ExternalEndpoint.id == ep.id).with_for_update())
    if answer_to:
        obs = (await session.execute(select(ExternalObservation).where(
            ExternalObservation.identity_id == identity_id, ExternalObservation.endpoint_id == ep.id,
            ExternalObservation.message_id == answer_to, ExternalObservation.addressed.is_(True),
            ExternalObservation.ownership_generation == account.ownership_generation))).scalars().first()
        if not obs or run.trigger_message_id != answer_to:
            raise ExternalChatError("answer must reference this run's addressed turn")
        existing = (await session.execute(select(ExternalDelivery).where(
            ExternalDelivery.identity_id == identity_id, ExternalDelivery.endpoint_id == ep.id,
            ExternalDelivery.answer_to == answer_to,
            ExternalDelivery.state.in_(("pending", "claimed", "accepted", "unknown"))))).scalars().first()
        if existing:
            return existing
    if not text.strip():
        raise ExternalChatError("message is empty")
    key = hashlib.sha256(f"{identity_id}:{ep.id}:{answer_to}:{run.id}".encode()).hexdigest() if automatic else None
    existing = (await session.execute(select(ExternalDelivery).where(ExternalDelivery.answer_key == key))).scalar_one_or_none() if key else None
    if existing:
        return existing
    row = ExternalDelivery(identity_id=identity_id, endpoint_id=ep.id, agent=agent, run_id=run_id,
        authorization_generation=generation, ownership_generation=account.ownership_generation,
        answer_to=answer_to, answer_key=key, chunks=[text[i:i + 1900] for i in range(0, len(text), 1900)])
    session.add(row)
    await session.flush()
    return row


async def queue_final(session, run, text):
    observation_id = getattr(run, "external_observation_id", None)
    if not observation_id or not run.trigger_message_id:
        return None
    obs = await session.get(ExternalObservation, observation_id)
    if not obs or not obs.addressed or obs.message_id != run.trigger_message_id:
        return None
    account = await session.get(ChatIdentity, obs.identity_id)
    if account and account.owner_agent == run.agent and account.ownership_generation == obs.ownership_generation:
        ep = await session.get(ExternalEndpoint, obs.endpoint_id)
        return await queue_send(session, agent=run.agent, run_id=run.id, identity_id=account.id,
            external_ref=ep.external_ref, text=text, answer_to=run.trigger_message_id, automatic=True)
    return None


async def delivery_authorized(session, row):
    from agentplatform.authority import current_generation, ensure_run_authority
    generation = await current_generation(session, row.agent)
    run = await session.get(Run, row.run_id) if row.run_id else None
    if generation != row.authorization_generation or not run or not await ensure_run_authority(session, run):
        raise ExternalChatError("delivery authority revoked")
    account = await owned_identity(session, row.identity_id, row.agent)
    if account.ownership_generation != row.ownership_generation:
        raise ExternalChatError("delivery owner changed")
    ep = await session.get(ExternalEndpoint, row.endpoint_id)
    await endpoint_access(session, account, ep.external_ref, send=True)
    return ep


async def claim_delivery(session, identity_id, request_id):
    from agentplatform.authority import authority_lock
    await authority_lock(session)
    row = (await session.execute(select(ExternalDelivery).where(
        ExternalDelivery.id == request_id, ExternalDelivery.identity_id == identity_id).with_for_update())).scalar_one_or_none()
    if not row:
        raise ExternalChatError("delivery not found")
    if row.state != "pending":
        raise ExternalChatError("delivery already claimed or terminal")
    try:
        ep = await delivery_authorized(session, row)
    except ExternalChatError as exc:
        row.state, row.error = "denied", str(exc)
        return row, None
    row.state, row.claim_token, row.claimed_at = "claimed", ident(), utcnow()
    await session.flush()
    return row, ep


async def receipt(session, identity_id, request_id, claim_token, index, provider_message_id=None, outcome=None):
    row = (await session.execute(select(ExternalDelivery).where(
        ExternalDelivery.id == request_id, ExternalDelivery.identity_id == identity_id).with_for_update())).scalar_one_or_none()
    if not row or not claim_token or row.claim_token != claim_token:
        raise ExternalChatError("invalid delivery claim")
    # An accepted effect is always accounted for, even if ownership changed
    # during the network request; accepting evidence grants no further send.
    if provider_message_id:
        if index is None or not 0 <= index < len(row.chunks):
            raise ExternalChatError("invalid chunk index")
        receipts = dict(row.receipts or {})
        if str(index) in receipts and receipts[str(index)] != provider_message_id:
            raise ExternalChatError("conflicting delivery receipt")
        receipts[str(index)] = provider_message_id
        row.receipts = receipts
        ep = await session.get(ExternalEndpoint, row.endpoint_id)
        msg = await mirror(session, ep, provider_message_id, f"agent:{row.agent}", row.chunks[index], run_id=row.run_id)
        if len(receipts) == len(row.chunks):
            row.state = "accepted"
        return row, msg
    if row.state != "accepted":
        row.state = "failed" if outcome == "failed" and not row.receipts else "unknown"
        row.error = "provider effect not confirmed" if row.state == "unknown" else "provider refused before effect"
    return row, None
