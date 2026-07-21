"""Discord conversation connector: a thin, long-lived bridge between Discord and
the platform's conversation bus. It holds nothing stateful of its own — the
platform owns conversations and history.

Inbound:  a mention (or a message in a thread it's active in) → produce a
          `conversation.message` envelope to `conversation.inbound`
          (external_ref = the Discord thread id).
Outbound: consume `conversation.outbound`, filter to `connector==discord`, and
          post the reply text back to the thread named by external_ref.

Activates only when DISCORD_BOT_TOKEN is set (the deployment is gated off by
default). The envelope format matches agentplatform.events so the platform's
conversation-ingest consumer can unwrap it.
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone

import discord
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

log = logging.getLogger("connector-discord")

TOPIC_IN = "conversation.inbound"
TOPIC_OUT = "conversation.outbound"
SCHEMA_VERSION = 1


def _envelope(type_: str, key: str, data: dict) -> bytes:
    return json.dumps({
        "type": type_, "schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "source": "connector:discord", "data": data,
    }).encode()


def _unwrap(raw: bytes) -> dict:
    v = json.loads(raw)
    return v.get("data", v) if isinstance(v, dict) else v


class DiscordConnector:
    def __init__(self):
        self.bootstrap = os.environ.get("AP_KAFKA_BOOTSTRAP", "ap-kafka:9092")
        self.agent = os.environ.get("CONNECTOR_AGENT", "echo")
        self.token = os.environ["DISCORD_BOT_TOKEN"]
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.producer: AIOKafkaProducer | None = None
        self._active_threads: set[int] = set()   # threads we've replied in
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self):
        log.info("discord connector ready as %s", self.client.user)

    async def on_message(self, message: discord.Message):
        if self.client.user is None or message.author.id == self.client.user.id:
            return
        mentioned = self.client.user in message.mentions
        in_active_thread = isinstance(message.channel, discord.Thread) and message.channel.id in self._active_threads
        if not (mentioned or in_active_thread):
            return
        # Converse in a thread; create one off a channel mention so each
        # conversation maps to a stable external_ref (the thread id).
        if isinstance(message.channel, discord.Thread):
            thread = message.channel
        else:
            thread = await message.create_thread(name=f"chat-{message.id}")
        self._active_threads.add(thread.id)
        text = message.clean_content
        if self.client.user.name:
            text = text.replace(f"@{self.client.user.name}", "").strip()
        await self.producer.send_and_wait(TOPIC_IN, key=str(thread.id).encode(),
            value=_envelope("conversation.message", str(thread.id), {
                "connector": "discord", "external_ref": str(thread.id),
                "external_user": message.author.name, "text": text, "agent": self.agent}))
        log.info("→ conversation.inbound thread=%s user=%s", thread.id, message.author.name)

    async def consume_outbound(self):
        await self.client.wait_until_ready()
        consumer = AIOKafkaConsumer(
            TOPIC_OUT, bootstrap_servers=self.bootstrap,
            group_id="connector-discord", auto_offset_reset="latest")
        await consumer.start()
        log.info("consuming conversation.outbound")
        try:
            async for msg in consumer:
                try:
                    data = _unwrap(msg.value)
                    if data.get("connector") != "discord" or not data.get("external_ref"):
                        continue
                    tid = int(data["external_ref"])
                    channel = self.client.get_channel(tid) or await self.client.fetch_channel(tid)
                    if channel is not None:
                        await channel.send((data.get("text") or "")[:1900])
                        log.info("← posted reply to thread=%s", tid)
                except Exception:
                    log.exception("failed to deliver outbound reply")
        finally:
            await consumer.stop()

    async def run(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap, enable_idempotence=True,
            acks="all", compression_type="gzip")
        await self.producer.start()
        asyncio.create_task(self.consume_outbound())
        await self.client.start(self.token)


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(DiscordConnector().run())


if __name__ == "__main__":
    main()
