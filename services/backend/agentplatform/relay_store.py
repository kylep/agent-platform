"""Writing a Relay message: the row, its view, and the `relay.messages` event.

Three callers post into channels — the REST API, the recorder (an agent's
reply) and the conversation facade (a human's turn) — and every one of them
must produce the same row and the same event, because the router, the SSE
fan-out and the bridges downstream read only what was written here. A second
implementation would be a second definition of what a message is."""
import logging

from sqlalchemy import select

from agentplatform.db import (AgentDef, RelayBinding, RelayMessage,
                              RelayParticipant, utcnow)
from agentplatform.events import TOPIC_RELAY_MESSAGES

log = logging.getLogger("relay_store")


def message_view(row, *, face=None, reactions=None) -> dict:
    return {"id": row.id, "channel_id": row.channel_id, "author": row.author,
            "kind": row.kind, "body": row.body, "card": row.card,
            "reply_to": row.reply_to, "thread_root": row.thread_root,
            "run_id": row.run_id, "hop": row.hop, "mentions": row.mentions or [],
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "edited_at": row.edited_at.isoformat() if row.edited_at else None,
            "face": face, "reactions": reactions or []}


def relay_message_payload(msg, conv, *, face=None) -> dict:
    """The `relay.messages` body. `channel_kind` rides along so a consumer can
    apply the room's rules (a dm is not a channel) without a lookup."""
    return {**message_view(msg, face=face), "channel_kind": conv.kind}


async def post_relay_message(session, conv, *, author: str, body: str,
                             kind: str = "text", run_id: str | None = None,
                             hop: int = 0, trigger_message_id: str | None = None,
                             reply_to: str | None = None,
                             mentions=None) -> RelayMessage:
    """Insert a message into `conv`. Flushed but NOT committed: the caller owns
    the transaction, so a turn's message and the run that answers it land
    together or not at all."""
    thread_root = None
    if reply_to:
        parent = await session.get(RelayMessage, reply_to)
        # A thread is flat: a reply to a reply joins the same root, so the pane
        # never has to render a tree.
        thread_root = (parent.thread_root or parent.id) if parent is not None else None
    row = RelayMessage(channel_id=conv.id, author=author, kind=kind, body=body,
                       reply_to=reply_to, thread_root=thread_root, run_id=run_id,
                       trigger_message_id=trigger_message_id, hop=hop,
                       mentions=list(mentions or []))
    session.add(row)
    # Posting is the activity the rail sorts private rooms by, and a room whose
    # only message was just written has nothing else to sort on.
    conv.updated_at = utcnow()
    await session.flush()
    return row


async def publish_relay_message(producer, conv, msg, *, face=None) -> None:
    """`relay.messages` is what the router, the SSE fan-out and the bridges all
    read. The row is committed and is the source of truth, so a broker blip must
    not fail a post that demonstrably landed — it costs the message its routing,
    which the invocation log shows as the mention that did nothing."""
    if producer is None:
        return
    try:
        await producer.publish(TOPIC_RELAY_MESSAGES, conv.id,
                               relay_message_payload(msg, conv, face=face),
                               type="relay.message")
    except Exception:
        log.warning("relay.messages publish failed for message %s", msg.id, exc_info=True)


async def enabled_agents(session) -> set[str]:
    """The agents a mention may resolve to, read off the rows rather than the
    AgentStore's cache: the recorder and the conversation facade hold a session,
    not a store. A quarantined definition still answers to its name here, which
    the router re-checks before it invokes anything."""
    return set((await session.execute(
        select(AgentDef.name).where(AgentDef.enabled))).scalars())


async def explicit_members(session, channel_id: str) -> set[str]:
    return set((await session.execute(select(RelayParticipant.participant).where(
        RelayParticipant.channel_id == channel_id))).scalars())


async def binding_of(session, channel_id: str) -> RelayBinding | None:
    return (await session.execute(select(RelayBinding).where(
        RelayBinding.channel_id == channel_id))).scalars().first()
