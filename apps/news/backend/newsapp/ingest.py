"""Digest ingestion: the app.news.inbound consumer and the write path.

Flow (docs/design/11): the recorder publishes each successful gatherer run's
result text here. We parse it defensively, run every story through the
freshness gates (dated, recent, an article URL, not a story already told this
week), dedup against the archive (dedup_hash = canonicalized URL — the
successor of shared_news), auto-create topics from sections, and for anything
NEW: emit one app.news.item.ingested event per item, post the Discord digest
(only new stories), and upsert today's daily-news report. Every rejected story
becomes an app.news.item.rejected event — the gates are observable, not
silent."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from newsapp import digest as dg
from newsapp.db import Item, Topic

log = logging.getLogger("news-ingest")

TOPIC_INBOUND = "app.news.inbound"
TOPIC_INGESTED = "app.news.item.ingested"
TOPIC_REJECTED = "app.news.item.rejected"
TOPIC_CHANNEL_POST = "discord.channel.post"

# A story is "stale" when its published date is more than this many days
# before the digest's date. Two covers a morning digest reporting yesterday's
# late news across a weekend gap without admitting last week's.
DEFAULT_MAX_AGE_DAYS = 2
# How far back the headline-similarity dedup looks. Longer than the age gate
# so a story that squeaked in on day N can't come back re-worded on day N+3.
STORY_WINDOW_DAYS = 7


@dataclass
class IngestResult:
    new: list[dict] = field(default_factory=list)      # accepted, digest shape
    day: str = ""
    rejected: list[tuple[dict, str]] = field(default_factory=list)  # (item, reason)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for _, reason in self.rejected:
            out[reason] = out.get(reason, 0) + 1
        return out


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


async def ingest_digest(sf, result_text: str | None, run_id: str | None = None,
                        max_age_days: int | None = None) -> IngestResult:
    """Parse + gate + dedup + store. `new` keeps the digest's own item shape
    (headline/why/url/section/published) for post composition."""
    if max_age_days is None:
        max_age_days = int(os.environ.get("NEWS_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS))
    digest = dg.parse_digest(result_text)
    if digest is None:
        return IngestResult()
    items = dg.valid_items(digest)
    if not items:
        return IngestResult()
    day_date = dg.parse_day(digest.get("date")) or datetime.now(timezone.utc).date()
    day = day_date.isoformat()
    window_start = (day_date - timedelta(days=STORY_WINDOW_DAYS)).isoformat()
    res = IngestResult(day=day)
    async with sf() as s:
        hashes = [dg.norm_url(it["url"]) for it in items]
        seen = set((await s.execute(
            select(Item.dedup_hash).where(Item.dedup_hash.in_(hashes)))).scalars())
        told = list((await s.execute(
            select(Item.title).where(Item.day >= window_start))).scalars())
        for it in items:
            h = dg.norm_url(it["url"])
            headline = dg.sanitize(it.get("headline", ""))[:512]
            published = dg.parse_day(it.get("published"))
            if h in seen:                       # archived, or intra-batch dup
                reason = "duplicate-url"
            elif published is None:
                reason = "undated"
            elif (day_date - published).days > max_age_days:
                reason = "stale"
            elif dg.is_hub_url(h):
                reason = "hub-url"
            elif any(dg.same_story(headline, t) for t in told):
                reason = "duplicate-story"
            else:
                reason = None
            if reason is not None:
                res.rejected.append((it, reason))
                continue
            seen.add(h)
            told.append(headline)
            topic = await get_or_create_topic(s, it.get("section", "") or "Other")
            from urllib.parse import urlsplit
            try:
                source = urlsplit(h).netloc
            except ValueError:
                source = ""
            s.add(Item(title=headline, url=h, source=source[:128],
                       summary=dg.sanitize(it.get("why", "")),
                       topic_id=topic.id, day=day, run_id=run_id,
                       published=published.isoformat(),
                       dedup_hash=h, raw=it))
            res.new.append(it)
        await s.commit()
    return res


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
        run_id = data.get("run_id")
        res = await ingest_digest(self.sf, data.get("result"), run_id)
        for it, reason in res.rejected:
            url = dg.norm_url(it["url"])
            await producer.send_and_wait(
                TOPIC_REJECTED, _envelope("news.item.rejected", url, {
                    "day": res.day, "headline": dg.sanitize(it.get("headline", "")),
                    "url": url, "published": it.get("published"),
                    "reason": reason, "run_id": run_id}))
        if res.rejected:
            log.info("rejected %d items for %s: %s", len(res.rejected), res.day,
                     res.counts())
        if not res.new:
            return
        log.info("ingested %d new items for %s", len(res.new), res.day)
        for it in res.new:
            await producer.send_and_wait(
                TOPIC_INGESTED, _envelope("news.item.ingested", dg.norm_url(it["url"]),
                                          {"day": res.day, "headline": it.get("headline"),
                                           "url": dg.norm_url(it["url"]),
                                           "section": it.get("section", "")}))
        post = dg.format_post(res.day, res.new, filtered=res.counts())
        await producer.send_and_wait(
            TOPIC_CHANNEL_POST, _envelope("channel.post", self.channel,
                                          {"channel": self.channel, "text": post}))
        if self.report_writer is not None:
            try:
                await self.report_writer(self.sf, res.day)
            except Exception:
                log.exception("daily report write failed for %s", res.day)

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
