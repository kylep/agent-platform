"""Digest ingestion: the app.news.inbound consumer and the write path.

Flow (docs/design/11): the recorder publishes each successful gatherer run's
result text here. We parse it defensively, dedup every story against the
archive (dedup_hash = canonicalized URL — the successor of shared_news),
auto-create topics from sections, and for anything NEW: emit one
app.news.item.ingested event per item, post the Discord digest (only new
stories), and upsert today's daily-news report."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from newsapp import digest as dg
from newsapp.db import Item, Topic

log = logging.getLogger("news-ingest")

TOPIC_INBOUND = "app.news.inbound"
TOPIC_INGESTED = "app.news.item.ingested"
TOPIC_CHANNEL_POST = "discord.channel.post"


def _envelope(type_: str, key: str, data: dict) -> bytes:
    return json.dumps({
        "type": type_, "schema_version": 1, "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "source": "app-news", "data": data,
    }).encode()


def _unwrap(raw: bytes) -> dict:
    value = json.loads(raw)
    if isinstance(value, dict) and "data" in value and "schema_version" in value:
        return value["data"]
    return value if isinstance(value, dict) else {}


async def get_or_create_topic(s, section: str) -> Topic:
    slug = dg.slugify(section)
    t = (await s.execute(select(Topic).where(Topic.slug == slug))).scalar_one_or_none()
    if t is None:
        n = len((await s.execute(select(Topic.id))).all())
        t = Topic(slug=slug, label=dg.sanitize(section)[:128] or slug,
                  color=(n % 8) + 1)
        s.add(t)
        await s.flush()
    return t


async def ingest_digest(sf, result_text: str | None, run_id: str | None = None
                        ) -> tuple[list[dict], str]:
    """Parse + dedup + store. Returns (new_items, day) — new_items in the
    digest's own shape (headline/why/url/section) for post composition."""
    digest = dg.parse_digest(result_text)
    if digest is None:
        return [], ""
    items = dg.valid_items(digest)
    if not items:
        return [], ""
    date = str(digest.get("date") or "")
    day = date if len(date) == 10 and date[4] == "-" else \
        datetime.now(timezone.utc).date().isoformat()
    new: list[dict] = []
    async with sf() as s:
        hashes = [dg.norm_url(it["url"]) for it in items]
        seen = set((await s.execute(
            select(Item.dedup_hash).where(Item.dedup_hash.in_(hashes)))).scalars())
        for it in items:
            h = dg.norm_url(it["url"])
            if h in seen:                       # archived, or intra-batch dup
                continue
            seen.add(h)
            topic = await get_or_create_topic(s, it.get("section", "") or "Other")
            from urllib.parse import urlsplit
            try:
                source = urlsplit(h).netloc
            except ValueError:
                source = ""
            s.add(Item(title=dg.sanitize(it.get("headline", ""))[:512],
                       url=h, source=source[:128],
                       summary=dg.sanitize(it.get("why", "")),
                       topic_id=topic.id, day=day, run_id=run_id,
                       dedup_hash=h, raw=it))
            new.append(it)
        await s.commit()
    return new, day


class IngestLoop:
    """Consume app.news.inbound forever; on new items, fan out the effects."""

    def __init__(self, sf, kafka_bootstrap: str, channel: str = "news",
                 report_writer=None):
        self.sf = sf
        self.bootstrap = kafka_bootstrap
        self.channel = channel
        self.report_writer = report_writer   # async (sf, day) -> None

    async def handle(self, producer, raw: bytes) -> None:
        data = _unwrap(raw)
        new, day = await ingest_digest(self.sf, data.get("result"), data.get("run_id"))
        if not new:
            return
        log.info("ingested %d new items for %s", len(new), day)
        for it in new:
            await producer.send_and_wait(
                TOPIC_INGESTED, _envelope("news.item.ingested", dg.norm_url(it["url"]),
                                          {"day": day, "headline": it.get("headline"),
                                           "url": dg.norm_url(it["url"]),
                                           "section": it.get("section", "")}))
        post = dg.format_post(day, new)
        await producer.send_and_wait(
            TOPIC_CHANNEL_POST, _envelope("channel.post", self.channel,
                                          {"channel": self.channel, "text": post}))
        if self.report_writer is not None:
            try:
                await self.report_writer(self.sf, day)
            except Exception:
                log.exception("daily report write failed for %s", day)

    async def run_forever(self) -> None:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
        while True:
            try:
                consumer = AIOKafkaConsumer(
                    TOPIC_INBOUND, bootstrap_servers=self.bootstrap,
                    group_id="news-app", enable_auto_commit=False,
                    auto_offset_reset="earliest")
                producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap)
                await consumer.start()
                await producer.start()
                try:
                    async for msg in consumer:
                        try:
                            await self.handle(producer, msg.value)
                        except Exception:
                            # Poison digests are logged and skipped — the next
                            # gather re-produces anything that mattered.
                            log.exception("digest handling failed; skipping")
                        await consumer.commit()
                finally:
                    await consumer.stop()
                    await producer.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("ingest loop crashed; restarting in 10s")
                await asyncio.sleep(10)
