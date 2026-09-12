"""Connector ingest consumer: the platform side of a connector's inbound path.
Reads `conversation.inbound` (produced by the Discord connector, etc.) and maps
the external room's ref to one of ours, creating it on first contact.

What happens next depends on the room. A DM — the mention-the-bot thread flow —
becomes a TURN through the shared `continue_conversation` logic: one human, one
agent, one run per message. A bound CHANNEL or group becomes a MESSAGE: several
agents live there, so the router decides from the mentions who (if anyone)
answers, exactly as it does for a message typed in the web pane.

The map from a bridge's room to ours is `relay_bindings` (docs/design/19); the
connector columns on the conversation are the same fact as it was recorded
before, and still answer for a room the backfill has not reached."""
import logging

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.conversation import author_of, continue_conversation
from agentplatform.db import (Conversation, RelayBinding, RelayParticipant,
                              utcnow)
from agentplatform.events import TOPIC_CONVERSATION_INBOUND, consume_forever
from agentplatform.relay import mentionable_in, parse_mentions
from agentplatform.relay_store import (enabled_agents, explicit_members,
                                       outbound_for_message, post_relay_message,
                                       publish_relay_message)

log = logging.getLogger("conversation_ingest")


class ConversationIngestor:
    def __init__(self, settings, session_factory, producer):
        self.settings = settings
        self.sf = session_factory
        self.producer = producer

    async def handle(self, data: dict) -> None:
        connector = data["connector"]
        external_ref = data.get("external_ref")
        text = data.get("text", "")
        agent = data.get("agent", "echo")   # the connector's default agent
        external_user = data.get("external_user", "unknown")
        if not text.strip():
            return
        requested_by = f"connector:{connector}:{external_user}"
        author = author_of(requested_by)
        posted = None
        async with self.sf() as s:
            conv, binding = await self._resolve(s, connector, external_ref)
            if conv is None:
                conv = Conversation(connector=connector, external_ref=external_ref,
                                    agent=agent, title=f"{connector}:{external_ref}")
                s.add(conv)
                await s.flush()
                self._bind(s, conv, connector, external_ref, author)
                try:
                    await s.commit()
                except IntegrityError:
                    # The binding's unique (connector, external_ref) caught two
                    # inbound messages racing for a room nobody had seen yet.
                    # The winner's channel IS the room, so use it rather than
                    # forking the thread's history in two.
                    await s.rollback()
                    conv, binding = await self._resolve(s, connector, external_ref)
                    if conv is None:
                        raise
            conv_id = conv.id
            # A bound CHANNEL is a room, not a DM (docs/design/19 T10): several
            # agents and several humans are in it, and `@news` in Discord has to
            # mean what `@news` means in the web pane. So the message is posted
            # as a message and the router decides who — if anyone — it summons.
            # Handing it to `continue_conversation` instead would give every
            # line in the channel to the connector's default agent, one run per
            # line. A dm (the mention-the-bot thread flow) keeps its turns.
            if binding is not None and conv.kind != "dm":
                msg = await self._post(s, conv, text, author,
                                       data.get("display_name"))
                if msg is None:
                    return
                await s.commit()
                posted = (msg, await outbound_for_message(s, conv, msg))
        if posted is not None:
            msg, outbound = posted
            await publish_relay_message(self.producer, conv, msg, outbound=outbound)
            return
        await continue_conversation(self.sf, self.producer, conv_id, text, requested_by)

    async def _post(self, s, conv: Conversation, text: str, author: str,
                    display_name: str | None):
        """The bridged message as a plain Relay message, at hop 0 — it came
        from a person, so it starts a fresh chain. Mentions are resolved
        against the room's own mentionable set, exactly as the API resolves a
        human's: a bridge must not be a way to summon an agent that is not in
        the room.

        An archived room takes no messages, from any door. The API refuses a
        post into one and the bridge must not be the way around that: an
        archived channel is still bound (archiving is not unbinding), so
        without this every message in the Discord channel would keep landing in
        a room nobody is reading."""
        if conv.archived_at is not None:
            log.warning("dropping a bridged message for archived channel %s", conv.id)
            return None
        await self._admit(s, conv, author, display_name)
        return await post_relay_message(
            s, conv, author=author, body=text,
            mentions=parse_mentions(
                text, mentionable_in(conv, await enabled_agents(s),
                                     await explicit_members(s, conv.id)), author))

    async def _admit(self, s, conv: Conversation, author: str,
                     display_name: str | None) -> None:
        """Make sure the speaker is a member of the room, and remember what they
        are called.

        A bound room admits its Discord side BY DEFINITION: the binding is the
        decision that these people are in this room, so a first-time speaker
        joins rather than being refused — refusing would leave a channel that is
        visibly mirrored where nobody on the other side may speak, which no
        operator would read as intended. In an open channel the row grants
        nothing (everyone is a member there); it is where the display name
        lives, and the name is the whole reason a client can render a person
        instead of a snowflake."""
        row = await s.get(RelayParticipant, {"channel_id": conv.id,
                                             "participant": author})
        if row is None:
            row = RelayParticipant(channel_id=conv.id, participant=author)
            s.add(row)
        if display_name and row.display_name != display_name:
            # People rename themselves; the room should follow rather than keep
            # showing whoever they were the first time they spoke.
            row.display_name = display_name

    async def _resolve(self, s, connector: str, external_ref):
        """The channel this external room maps to, and the binding that says so.

        The binding is authoritative — it is the row `POST /bindings` writes and
        the only one the bridge consults — and the legacy columns are the
        fallback for a thread that predates it. The binding comes back with the
        channel because it is also the answer to "is this room two-sided?",
        which decides whether an inbound message is a turn or a message."""
        if external_ref:
            binding = (await s.execute(select(RelayBinding).where(
                RelayBinding.connector == connector,
                RelayBinding.external_ref == external_ref))).scalars().first()
            if binding is not None:
                conv = await s.get(Conversation, binding.channel_id)
                if conv is not None:
                    if conv.status != "active":
                        # An inbound message is the room saying it is alive
                        # again. The binding is unique, so a closed channel on
                        # the other end of it would strand every future message
                        # from that thread rather than starting a new one.
                        conv.status = "active"
                        conv.updated_at = utcnow()
                        await s.commit()
                    return conv, binding
        return (await s.execute(select(Conversation).where(
            Conversation.connector == connector,
            Conversation.external_ref == external_ref,
            Conversation.status == "active"))).scalars().first(), None

    def _bind(self, s, conv: Conversation, connector: str, external_ref,
              author: str) -> None:
        """A room seen for the first time joins Relay properly: bound to its
        external ref, with the two participants that make it a DM. Without them
        it is a channel with no members, which every membership rule reads as
        nobody being allowed to speak."""
        if external_ref:
            s.add(RelayBinding(channel_id=conv.id, connector=connector,
                               external_ref=external_ref))
        for participant in [author] + ([f"agent:{conv.agent}"] if conv.agent else []):
            s.add(RelayParticipant(channel_id=conv.id, participant=participant))

    async def run_forever(self) -> None:
        consumer = AIOKafkaConsumer(
            TOPIC_CONVERSATION_INBOUND, bootstrap_servers=self.settings.kafka_bootstrap,
            group_id="conversation-ingest", enable_auto_commit=False,
        )
        await consumer.start()
        try:
            await consume_forever(consumer, self.producer,
                                  lambda msg, data: self.handle(data))
        finally:
            await consumer.stop()
