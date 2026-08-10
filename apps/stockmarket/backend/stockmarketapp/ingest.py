"""Brief ingestion: the app.stockmarket.inbound consumer and the write path.

Flow: the recorder publishes each successful `stockmarket` run's result text
here. We parse it defensively (brief.py), store one row per session — keyed by
day, so a re-run corrects rather than duplicates — then emit
app.stockmarket.brief.posted and post the brief to Discord.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from stockmarketapp import brief as bf
from stockmarketapp.db import Brief

log = logging.getLogger("stockmarket-ingest")

TOPIC_INBOUND = "app.stockmarket.inbound"
TOPIC_POSTED = "app.stockmarket.brief.posted"
TOPIC_CHANNEL_POST = "discord.channel.post"


def _envelope(type_: str, key: str, data: dict) -> bytes:
    return json.dumps({
        "type": type_, "schema_version": 1, "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "source": "app-stockmarket", "data": data,
    }).encode()


def _unwrap(raw: bytes) -> dict:
    value = json.loads(raw)
    if isinstance(value, dict) and "data" in value and "schema_version" in value:
        return value["data"]
    return value if isinstance(value, dict) else {}


async def ingest_brief(sf, result_text: str | None, run_id: str | None = None
                       ) -> dict | None:
    """Parse + validate + store. Returns the stored brief, or None when the
    result held nothing usable."""
    raw = bf.parse_brief(result_text)
    if raw is None:
        return None
    cleaned = bf.clean_brief(raw)
    if cleaned is None:
        return None
    async with sf() as s:
        row = await s.get(Brief, cleaned["day"])
        if row is None:
            row = Brief(day=cleaned["day"])
            s.add(row)
        # A re-run for the same session replaces it: the day is the identity,
        # and the newer attempt is the better one.
        row.body = cleaned["body"]
        row.tags = cleaned["tags"]
        row.indexes = cleaned["indexes"]
        row.movers = cleaned["movers"]
        row.run_id = run_id
        await s.commit()
    return cleaned


class IngestLoop:
    """Consume app.stockmarket.inbound forever; on a stored brief, fan out."""

    def __init__(self, sf, kafka_bootstrap: str, channel: str = "markets"):
        self.sf = sf
        self.bootstrap = kafka_bootstrap
        self.channel = channel

    async def handle(self, producer, raw: bytes) -> None:
        data = _unwrap(raw)
        stored = await ingest_brief(self.sf, data.get("result"), data.get("run_id"))
        if stored is None:
            log.info("inbound result held no usable brief; skipped")
            return
        log.info("stored brief for %s (%d movers)", stored["day"],
                 len(stored["movers"]))
        await producer.send_and_wait(
            TOPIC_POSTED, _envelope("stockmarket.brief.posted", stored["day"], {
                "day": stored["day"], "tags": stored["tags"],
                "indexes": stored["indexes"]}))
        await producer.send_and_wait(
            TOPIC_CHANNEL_POST, _envelope("channel.post", self.channel, {
                "channel": self.channel, "text": bf.format_post(stored)}))

    async def run_forever(self) -> None:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
        while True:
            try:
                consumer = AIOKafkaConsumer(
                    TOPIC_INBOUND, bootstrap_servers=self.bootstrap,
                    group_id="stockmarket-app", enable_auto_commit=False,
                    auto_offset_reset="earliest")
                producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap)
                await consumer.start()
                await producer.start()
                try:
                    async for msg in consumer:
                        try:
                            await self.handle(producer, msg.value)
                        except Exception:
                            # A poison brief is logged and skipped — tomorrow's
                            # run re-produces anything that mattered.
                            log.exception("brief handling failed; skipping")
                        await consumer.commit()
                finally:
                    await consumer.stop()
                    await producer.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ingest loop crashed; restarting in 10s")
                await asyncio.sleep(10)
