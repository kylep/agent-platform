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
from agentplatform.db import (DEFAULT_DISCORD_IDENTITY, ChatIdentity, Conversation, RelayBinding,
                              RelayMessage, RelayParticipant, utcnow)
from agentplatform.events import TOPIC_CONVERSATION_INBOUND, consume_forever
from agentplatform.relay import mentionable_in, parse_mentions, room_dispatch_mode
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
        identity_id = data.get("identity_id") or (DEFAULT_DISCORD_IDENTITY
                                                   if connector == "discord" else None)
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
            if connector == "discord":
                identity = await s.get(ChatIdentity, identity_id)
                if identity is None or identity.connector != connector or identity.status != "active":
                    log.warning("dropping Discord message from an inactive chat identity")
                    return
            conv, binding = await self._resolve(s, connector, external_ref)
            if binding is not None and binding.identity_id not in (None, identity_id):
                log.warning("dropping Discord message for another chat identity's route")
                return
            if conv is not None and conv.status != "active":
                conv.status = "active"
                conv.updated_at = utcnow()
                await s.commit()
            if conv is None:
                conv = Conversation(connector=connector, external_ref=external_ref,
                                    agent=agent, default_agent=agent,
                                    home="external", reply_mode="linear",
                                    dispatch_mode="default",
                                    title=(data.get("external_title")
                                           or f"{connector} thread {external_ref}"))
                s.add(conv)
                await s.flush()
                binding = await self._bind(s, conv, connector, external_ref, author, data,
                                           identity_id)
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
                    if binding is not None and binding.identity_id not in (None, identity_id):
                        log.warning("dropping Discord message for another chat identity's route")
                        return
            elif binding is None and external_ref:
                # A pre-Relay conversation found through its legacy columns.
                # Promote its connector identity before accepting another
                # message so reconnect recovery and deduplication work now.
                binding = await self._bind(s, conv, connector, external_ref, author, data,
                                           identity_id)
                conv.home = "external"
                conv.reply_mode = "linear"
                conv.dispatch_mode = "default"
                conv.default_agent = conv.default_agent or conv.agent or agent
                await s.commit()
            if binding is not None:
                # The first design-19 rows knew only a snowflake. Let ordinary
                # traffic repair their endpoint label/link, and keep them
                # current when a Discord thread is renamed.
                title = (data.get("external_title") or "").strip()
                external_url = (data.get("external_url") or "").strip()
                if title:
                    binding.display_name = title
                    if conv.home == "external":
                        conv.title = title
                if external_url:
                    binding.external_url = external_url
                if data.get("external_parent_ref"):
                    binding.parent_external_ref = str(data["external_parent_ref"])
                if data.get("external_kind"):
                    binding.external_kind = data["external_kind"]
            conv_id = conv.id
            # Every router-owned room enters through the same durable message
            # path. Connected threads differ only in having a default target;
            # the connector no longer materializes their run itself.
            if binding is not None and room_dispatch_mode(conv) != "facade":
                msg = await self._post(s, conv, text, author,
                                       data.get("display_name"), binding,
                                       data.get("external_message_id"))
                if msg is None:
                    return
                try:
                    await s.commit()
                except IntegrityError:
                    # Kafka replay or a reconnect delivered the same source
                    # message twice. The unique endpoint/message identity is
                    # the arbiter; the first copy already owns routing.
                    await s.rollback()
                    return
                posted = (msg, await outbound_for_message(s, conv, msg))
        if posted is not None:
            msg, outbound = posted
            await publish_relay_message(self.producer, conv, msg, outbound=outbound)
            return
        await continue_conversation(self.sf, self.producer, conv_id, text, requested_by)

    async def _post(self, s, conv: Conversation, text: str, author: str,
                    display_name: str | None, binding: RelayBinding,
                    external_message_id: str | None):
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
        if external_message_id and (await s.execute(select(RelayMessage.id).where(
                RelayMessage.source_binding_id == binding.id,
                RelayMessage.external_message_id == external_message_id))).first():
            return None
        await self._admit(s, conv, author, display_name)
        return await post_relay_message(
            s, conv, author=author, body=text,
            source_binding_id=binding.id,
            external_message_id=external_message_id,
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
                    return conv, binding
        return (await s.execute(select(Conversation).where(
            Conversation.connector == connector,
            Conversation.external_ref == external_ref,
            Conversation.status == "active"))).scalars().first(), None

    async def _bind(self, s, conv: Conversation, connector: str, external_ref,
                    author: str, data: dict, identity_id: str | None) -> RelayBinding | None:
        """A room seen for the first time joins Relay properly: bound to its
        external ref, with the two participants that make it a DM. Without them
        it is a channel with no members, which every membership rule reads as
        nobody being allowed to speak."""
        binding = None
        if external_ref:
            binding = RelayBinding(
                channel_id=conv.id, connector=connector, external_ref=external_ref,
                identity_id=identity_id,
                external_kind=data.get("external_kind") or "thread",
                parent_external_ref=data.get("external_parent_ref"),
                display_name=data.get("external_title") or "",
                external_url=data.get("external_url") or "")
            s.add(binding)
        for participant in [author] + ([f"agent:{conv.agent}"] if conv.agent else []):
            if await s.get(RelayParticipant, {"channel_id": conv.id,
                                              "participant": participant}) is None:
                s.add(RelayParticipant(channel_id=conv.id, participant=participant))
        return binding

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
