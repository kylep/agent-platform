"""News digest writer for the database-owned News App."""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import uuid
from datetime import date as date_cls, datetime, time, timedelta, timezone
from urllib.parse import urlsplit

from sqlalchemy import select

from agentplatform.appdata import news_digest as dg, quotas
from agentplatform.appdata.models import AppDataApp, AppDataRecord
from agentplatform.appdata.records import bump_counters, load_app, normalize_value, side_columns
from agentplatform.db import Report
from agentplatform.reportsanitize import sanitize_report_html

log = logging.getLogger("news-state-ingest")
INBOUND, INGESTED, REJECTED = (
    "app.news.inbound", "app.news.item.ingested", "app.news.item.rejected")


def unwrap(raw: bytes) -> dict:
    value = json.loads(raw)
    if isinstance(value, dict) and "schema_version" in value and "data" in value:
        return value["data"] if isinstance(value["data"], dict) else {}
    return value if isinstance(value, dict) else {}


def make_record(ctx, collection: str, doc: dict) -> AppDataRecord:
    spec = ctx.collection(collection)
    normalized = {key: normalize_value(ctx, spec, key, value)
                  for key, value in doc.items() if value is not None}
    size = quotas.doc_bytes(normalized)
    return AppDataRecord(app_id=ctx.app_id, collection=collection,
                         id=uuid.uuid4().hex, current_version=1,
                         author="tool:news", collection_version=ctx.versions[collection],
                         doc=normalized, size_bytes=size,
                         **side_columns(spec, normalized))


async def ingest_digest(sf, result_text: str | None, run_id: str | None = None,
                        *, max_age_days: int | None = None) -> dict:
    digest = dg.parse_digest(result_text)
    if digest is None:
        return {"issue": "invalid-digest", "day": "", "new": [], "rejected": []}
    items = dg.valid_items(digest)
    date = dg.parse_day(digest.get("date"))
    day = (date or datetime.now(timezone.utc).date()).isoformat()
    if not items:
        return {"issue": "empty-digest", "day": day, "new": [], "rejected": []}
    max_age = max_age_days if max_age_days is not None else int(
        os.environ.get("NEWS_MAX_AGE_DAYS", "2"))
    result = {"issue": None, "day": day, "new": [], "rejected": []}
    async with sf() as session:
        async with session.begin():
            app = (await session.execute(select(AppDataApp).where(
                AppDataApp.name == "news", AppDataApp.status == "active"))).scalar_one()
            ctx = await load_app(session, app.id)
            keys = [dg.norm_url(item["url"]) for item in items]
            keys += [f"{key}#{day}" for key in keys]
            hashes = [hashlib.sha256(key.encode()).hexdigest() for key in keys]
            archived = (await session.execute(select(AppDataRecord.doc).where(
                AppDataRecord.app_id == app.id, AppDataRecord.collection == "items",
                AppDataRecord.ix_text2.in_(hashes)))).scalars().all()
            seen = {row["dedup_hash"] for row in archived}
            since = datetime.combine(
                (date or datetime.now(timezone.utc).date()) - timedelta(days=7),
                time(), tzinfo=timezone.utc)
            recent = (await session.execute(select(AppDataRecord.doc).where(
                AppDataRecord.app_id == app.id, AppDataRecord.collection == "items",
                AppDataRecord.ix_time1 >= since))).scalars().all()
            told = [row["title"] for row in recent]
            topics = {row.doc["slug"]: row for row in (await session.execute(
                select(AppDataRecord).where(AppDataRecord.app_id == app.id,
                                            AppDataRecord.collection == "topics")
            )).scalars()}
            written = []
            for item in items:
                url = dg.norm_url(item["url"])
                title = dg.sanitize(item.get("headline", ""))[:512]
                published = dg.parse_day(item.get("published"))
                daily = dg.is_daily_section(item.get("section", ""))
                key = f"{url}#{day}" if daily else url
                if key in seen:
                    reason = "duplicate-url"
                elif published is None:
                    reason = "undated"
                elif ((date or datetime.now(timezone.utc).date()) - published).days > max_age:
                    reason = "stale"
                elif dg.is_hub_url(url):
                    reason = "hub-url"
                elif not daily and any(dg.same_story(title, old) for old in told):
                    reason = "duplicate-story"
                else:
                    reason = None
                if reason:
                    result["rejected"].append((item, reason))
                    continue
                slug = dg.slugify(item.get("section", "") or "Other")
                topic = topics.get(slug)
                if topic is None:
                    topic = make_record(ctx, "topics", {
                        "slug": slug, "label": dg.sanitize(item.get("section", ""))[:128] or slug,
                        "color": len(topics) % 8 + 1})
                    topics[slug] = topic
                    session.add(topic)
                    written.append(topic)
                summary = dg.sanitize(item.get("why", ""))[:16000]
                story = make_record(ctx, "items", {
                    "title": title, "url": url, "source": urlsplit(url).netloc[:128],
                    "summary": summary, "search_text": (title + " " + summary)[:16000],
                    "topic": topic.id, "topic_slug": slug, "topic_label": topic.doc["label"],
                    "color": topic.doc["color"], "day": day, "day_key": day,
                    "published": published.isoformat(), "run_id": run_id,
                    "dedup_hash": key,
                    "url_hash": hashlib.sha256(key.encode()).hexdigest(),
                    "raw_json": (lambda raw: raw if len(raw) <= 16000 else "{}")(
                        json.dumps(item, sort_keys=True, ensure_ascii=False))})
                session.add(story)
                written.append(story)
                seen.add(key)
                told.append(title)
                result["new"].append(item)
            if written:
                quotas.note(session, ctx, records=len(written),
                            bytes=sum(row.size_bytes for row in written),
                            writes=len(written))
                await quotas.settle(session)
                await bump_counters(session, app.id, {"topics", "items"})
                await session.flush()
                await write_daily_report(session, app.id, day)
    return result


async def write_daily_report(session, app_id: str, day: str) -> None:
    rows = (await session.execute(select(AppDataRecord).where(
        AppDataRecord.app_id == app_id, AppDataRecord.collection == "items",
        AppDataRecord.ix_time1 == datetime.combine(
            date_cls.fromisoformat(day), time(), tzinfo=timezone.utc)
        ).order_by(AppDataRecord.ix_text1,
                                                AppDataRecord.created_at))).scalars().all()
    sections: dict[str, list[dict]] = {}
    for row in rows:
        sections.setdefault(row.doc["topic_label"], []).append(row.doc)
    esc = lambda value: html.escape(str(value or ""), quote=True)
    parts = ['<header class="rk-header">', f'<h1 class="rk-title">Daily news — {esc(day)}</h1>',
             f'<p class="rk-meta">{len(rows)} items · {len(sections)} topics · gathered by the news agent</p>',
             '</header><div class="rk-stat-row">',
             f'<div class="rk-stat"><span class="rk-stat-value">{len(rows)}</span><span class="rk-stat-label">items</span></div>',
             f'<div class="rk-stat"><span class="rk-stat-value">{len(sections)}</span><span class="rk-stat-label">topics</span></div></div>']
    for label, stories in sections.items():
        parts.append('<section class="rk-section">')
        parts.append(f'<h2>{esc(label)} <span class="rk-chip">{len(stories)} items</span></h2>')
        for story in stories:
            parts.append('<div class="rk-item"><span class="rk-item-title">'
                         f'<a href="{esc(story["url"])}">{esc(story["title"])}</a></span>'
                         f'<span class="rk-item-src">{esc(story["source"])}</span>'
                         f'<p class="rk-item-sum">{esc(story["summary"])}</p></div>')
        parts.append('</section>')
    parts.append(f'<footer class="rk-footer">news app · daily-news · {esc(day)}</footer>')
    clean = sanitize_report_html("".join(parts))
    existing = (await session.execute(select(Report).where(
        Report.type == "daily-news", Report.date == day, Report.time == ""))).scalar_one_or_none()
    latest_run = next((row.doc.get("run_id") for row in reversed(rows)
                       if row.doc.get("run_id")), None)
    meta = {"items": len(rows), "topics": len(sections)}
    if existing is None:
        session.add(Report(type="daily-news", date=day, time="",
                           title=f"Daily news — {day}", html=clean,
                           meta=meta, run_id=latest_run))
    else:
        existing.html, existing.meta = clean, meta
        existing.run_id = latest_run or existing.run_id


class NewsStateIngestor:
    def __init__(self, sf, bootstrap: str, producer):
        self.sf, self.bootstrap, self.producer = sf, bootstrap, producer

    async def ready(self) -> bool:
        async with self.sf() as session:
            return (await session.execute(select(AppDataApp.id).where(
                AppDataApp.name == "news", AppDataApp.status == "active"))).scalar_one_or_none() is not None

    async def handle(self, raw: bytes) -> dict:
        data = unwrap(raw)
        run_id = data.get("run_id")
        result = await ingest_digest(self.sf, data.get("result"), run_id)
        if result["issue"]:
            await self.producer.publish(REJECTED, run_id or "unknown",
                                        {"day": result["day"], "reason": result["issue"],
                                         "run_id": run_id}, type="news.digest.rejected")
            return result
        for item, reason in result["rejected"]:
            url = dg.norm_url(item["url"])
            await self.producer.publish(REJECTED, url, {
                "day": result["day"], "headline": dg.sanitize(item.get("headline", "")),
                "url": url, "published": item.get("published"),
                "reason": reason, "run_id": run_id}, type="news.item.rejected")
        for item in result["new"]:
            url = dg.norm_url(item["url"])
            await self.producer.publish(INGESTED, url, {
                "day": result["day"], "headline": item.get("headline"),
                "url": url, "section": item.get("section", "")},
                type="news.item.ingested")
        return result

    async def run_forever(self) -> None:
        from aiokafka import AIOKafkaConsumer
        while True:
            try:
                if not await self.ready():
                    await asyncio.sleep(10)
                    continue
                consumer = AIOKafkaConsumer(
                    INBOUND, bootstrap_servers=self.bootstrap, group_id="news-app",
                    enable_auto_commit=False, auto_offset_reset="earliest")
                await consumer.start()
                try:
                    async for message in consumer:
                        try:
                            await self.handle(message.value)
                        except Exception:
                            log.exception("News digest failed; retrying the same offset")
                            await asyncio.sleep(10)
                            break
                        await consumer.commit()
                finally:
                    await consumer.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("News consumer crashed; restarting")
                await asyncio.sleep(10)
