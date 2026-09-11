"""Connector ingest consumer: the platform side of a connector's inbound path.
Reads `conversation.inbound` (produced by the Discord connector, etc.), maps the
external channel ref to a channel (creating one on first contact), and adds the
message as a turn via the shared continue_conversation logic.

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
        async with self.sf() as s:
            conv = await self._resolve(s, connector, external_ref)
            if conv is None:
                conv = Conversation(connector=connector, external_ref=external_ref,
                                    agent=agent, title=f"{connector}:{external_ref}")
                s.add(conv)
                await s.flush()
                self._bind(s, conv, connector, external_ref, author_of(requested_by))
                try:
                    await s.commit()
                except IntegrityError:
                    # The binding's unique (connector, external_ref) caught two
                    # inbound messages racing for a room nobody had seen yet.
                    # The winner's channel IS the room, so use it rather than
                    # forking the thread's history in two.
                    await s.rollback()
                    conv = await self._resolve(s, connector, external_ref)
                    if conv is None:
                        raise
            conv_id = conv.id
        await continue_conversation(self.sf, self.producer, conv_id, text, requested_by)

    async def _resolve(self, s, connector: str, external_ref):
        """The channel this external room maps to. The binding is authoritative
        — it is the row a `/relay bind` writes and the only one T10's bridge
        will consult — and the legacy columns are the fallback for a thread that
        predates it."""
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
                    return conv
        return (await s.execute(select(Conversation).where(
            Conversation.connector == connector,
            Conversation.external_ref == external_ref,
            Conversation.status == "active"))).scalars().first()

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
