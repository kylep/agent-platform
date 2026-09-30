"""One durable internal DM per pair, shared by Relay and the old facade."""

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import Conversation, RelayParticipant, dm_key_of


async def find_internal_dm(s, pair: list[str]) -> Conversation | None:
    key = dm_key_of(pair)
    conv = (await s.execute(select(Conversation).where(
        Conversation.kind == "dm", Conversation.home == "relay",
        Conversation.connector == "web",
        Conversation.dm_key == key))).scalars().first()
    if conv is not None:
        return conv
    ids = list((await s.execute(select(RelayParticipant.channel_id).where(
        RelayParticipant.participant.in_(pair)).group_by(RelayParticipant.channel_id)
        .having(func.count() == len(pair)))).scalars())
    if not ids:
        return None
    convs = list((await s.execute(select(Conversation).where(
        Conversation.id.in_(ids), Conversation.kind == "dm",
        Conversation.home == "relay", Conversation.connector == "web")
        .order_by(Conversation.created_at, Conversation.id))).scalars())
    sizes = dict((await s.execute(select(RelayParticipant.channel_id, func.count()).where(
        RelayParticipant.channel_id.in_([c.id for c in convs]))
        .group_by(RelayParticipant.channel_id))).all())
    return next((c for c in convs if sizes.get(c.id) == len(pair)), None)


async def open_internal_dm(s, pair: list[str], agent: str | None) -> Conversation:
    """Resolve the same room through both entry points, including old archived DMs.

    A uniqueness race is resolved by reading the winning row after rollback.
    Existing messages and session blobs stay attached to their original ID.
    """
    pair = sorted(pair)
    conv = await find_internal_dm(s, pair)
    if conv is None:
        conv = Conversation(connector="web", kind="dm", agent=agent,
                            home="relay", reply_mode="linear",
                            dispatch_mode="facade", default_agent=agent,
                            topic="", open=False, dm_key=dm_key_of(pair),
                            title="dm:" + ":".join(pair))
        s.add(conv)
        await s.flush()
        for p in pair:
            s.add(RelayParticipant(channel_id=conv.id, participant=p, role="member"))
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            conv = await find_internal_dm(s, pair)
            if conv is None:
                raise
    if conv.status != "active" or conv.archived_at is not None:
        conv.status = "active"
        conv.archived_at = None
        await s.commit()
    return conv
