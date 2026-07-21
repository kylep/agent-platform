"""Connector ingest consumer: the platform side of a connector's inbound path.
Reads `conversation.inbound` (produced by the Discord connector, etc.), maps the
external channel ref to a Conversation (creating one on first contact), and adds
the message as a turn via the shared continue_conversation logic."""
import logging

from aiokafka import AIOKafkaConsumer
from sqlalchemy import select

from agentplatform.conversation import continue_conversation
from agentplatform.db import Conversation
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
        async with self.sf() as s:
            conv = (await s.execute(select(Conversation).where(
                Conversation.connector == connector,
                Conversation.external_ref == external_ref,
                Conversation.status == "active"))).scalars().first()
            if conv is None:
                conv = Conversation(connector=connector, external_ref=external_ref,
                                    agent=agent, title=f"{connector}:{external_ref}")
                s.add(conv)
                await s.commit()
            conv_id = conv.id
        await continue_conversation(self.sf, self.producer, conv_id, text,
                                    f"connector:{connector}:{external_user}")

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
