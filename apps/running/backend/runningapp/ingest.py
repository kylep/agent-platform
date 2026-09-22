"""Activity + brief ingestion: the app.running.inbound consumer and write path.

Flow: the Strava tool publishes normalized activities directly, while the
recorder publishes each successful `running-coach` note. We parse either shape
defensively (brief.py), upsert one row per Strava activity
id (a re-send corrects, never duplicates), and — when the payload carries a
weekly note — store it under THIS week's Monday (the app's clock, not the
agent's), posting to Discord and writing the report only the first time that
week is seen. Subsequent same-week sends refresh the text silently.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from runningapp import brief as bf
from runningapp.db import RUN_TYPES, Activity, Brief
from runningapp.report import write_weekly_report

log = logging.getLogger("running-ingest")

TOPIC_INBOUND = "app.running.inbound"
TOPIC_POSTED = "app.running.brief.posted"
TOPIC_CHANNEL_POST = "discord.channel.post"


def _envelope(type_: str, key: str, data: dict) -> bytes:
    return json.dumps({
        "type": type_, "schema_version": 1, "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "source": "app-running", "data": data,
    }).encode()


def _unwrap(raw: bytes) -> dict:
    value = json.loads(raw)
    if isinstance(value, dict) and "data" in value and "schema_version" in value:
        data = value["data"] if isinstance(value["data"], dict) else {}
        return {**data, "_event_ts": value.get("ts")}
    return value if isinstance(value, dict) else {}


def _monday(d: date) -> str:
    return (d - timedelta(days=d.weekday())).isoformat()


async def store_activities(sf, acts: list[dict]) -> int:
    if not acts:
        return 0
    async with sf() as s:
        for a in acts:
            row = await s.get(Activity, a["id"])
            if row is None:
                row = Activity(id=a["id"])
                s.add(row)
            row.day = a["day"]
            row.name = a["name"]
            row.type = a["type"]
            row.distance_m = a["distance_m"]
            row.moving_time_s = a["moving_time_s"]
            row.elevation_m = a["elevation_m"]
            row.avg_hr = a["avg_hr"]
            row.max_hr = a["max_hr"]
        await s.commit()
    return len(acts)


async def _week_stats(sf, week_start: str) -> dict:
    """Distance + run count the app computes for a week — never trusting the
    agent's own totals. Sunday is week_start + 6 days."""
    end = (date.fromisoformat(week_start) + timedelta(days=6)).isoformat()
    async with sf() as s:
        rows = (await s.execute(
            select(Activity.type, Activity.distance_m)
            .where(Activity.day >= week_start, Activity.day <= end))).all()
    dist = sum(r[1] or 0 for r in rows)
    runs = sum(1 for r in rows if r[0] in RUN_TYPES)
    return {"distance_m": dist, "distance_km": round(dist / 1000, 2), "runs": runs}


async def store_brief(sf, cleaned: dict, week_start: str, run_id: str | None,
                      stats: dict) -> tuple[Brief, bool]:
    """Upsert the week's brief. Returns (row, first_post) — first_post is True
    only the first time this week is stored, gating the Discord post + report."""
    async with sf() as s:
        row = await s.get(Brief, week_start)
        first_post = row is None or not row.posted
        if row is None:
            row = Brief(week_start=week_start)
            s.add(row)
        row.body = cleaned["body"]
        row.highlights = cleaned["highlights"]
        row.tags = cleaned["tags"]
        row.distance_m = stats["distance_m"]
        row.runs = stats["runs"]
        row.run_id = run_id
        if first_post:
            row.posted = True
        await s.commit()
        await s.refresh(row)
    return row, first_post


class IngestLoop:
    """Consume app.running.inbound forever; store activities, and on a weekly
    brief's first sighting fan out to Discord + the report."""

    def __init__(self, sf, kafka_bootstrap: str, channel: str = "running"):
        self.sf = sf
        self.bootstrap = kafka_bootstrap
        self.channel = channel
        self.started_at = datetime.now(timezone.utc)
        self.status = {"consumer_started": False, "consumer_assigned": False,
                       "last_message_at": None, "last_activity_sync_at": None,
                       "last_error": None}

    async def handle(self, producer, raw: bytes) -> None:
        data = _unwrap(raw)
        # Direct sync events already contain validated tool output. Coach runs
        # arrive through the recorder as result text and retain the old parser.
        payload = (data if isinstance(data.get("activities"), list)
                   else bf.parse_payload(data.get("result")))
        if payload is None:
            log.info("inbound result held no usable payload; skipped")
            return
        n = await store_activities(self.sf, bf.clean_activities(payload.get("activities")))
        self.status["last_message_at"] = datetime.now(timezone.utc).isoformat()
        if "synced_at" in data or data.get("activities") is not None:
            self.status["last_activity_sync_at"] = (
                data.get("synced_at") or self.status["last_message_at"])
        if n:
            log.info("stored/updated %d activities", n)

        cleaned = bf.clean_brief(payload.get("brief"))
        if cleaned is None:
            return
        # A new consumer group intentionally replays the activity archive, but
        # old coaching prose belongs to its original week. The legacy envelope
        # did not carry that week, so silently discard replayed briefs rather
        # than relabel one as current or post it again.
        event_ts = data.get("_event_ts")
        try:
            event_at = datetime.fromisoformat(str(event_ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            event_at = self.started_at
        if event_at < self.started_at:
            log.info("discarded replayed brief with no trustworthy week")
            return
        week_start = _monday(datetime.now(timezone.utc).date())
        stats = await _week_stats(self.sf, week_start)
        row, first_post = await store_brief(self.sf, cleaned, week_start,
                                            data.get("run_id"), stats)
        if not first_post:
            log.info("brief for week %s already posted; text refreshed", week_start)
            return
        log.info("stored weekly brief for %s (%d runs, %.1f km)",
                 week_start, stats["runs"], stats["distance_km"])
        await producer.send_and_wait(
            TOPIC_POSTED, _envelope("running.brief.posted", week_start, {
                "week_start": week_start, "runs": stats["runs"],
                "distance_km": stats["distance_km"], "tags": cleaned["tags"]}))
        await producer.send_and_wait(
            TOPIC_CHANNEL_POST, _envelope("channel.post", self.channel, {
                "channel": self.channel,
                "text": bf.format_post(week_start, cleaned, stats)}))
        try:
            await write_weekly_report(self.sf, week_start)
        except Exception:
            log.exception("weekly-running report write failed for %s", week_start)

    async def run_forever(self) -> None:
        from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
        while True:
            try:
                consumer = AIOKafkaConsumer(
                    TOPIC_INBOUND, bootstrap_servers=self.bootstrap,
                    group_id="running-coach-app-v2", enable_auto_commit=False,
                    auto_offset_reset="earliest")
                producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap)
                await consumer.start()
                await producer.start()
                self.status.update(consumer_started=True, last_error=None)
                try:
                    while True:
                        batches = await consumer.getmany(timeout_ms=1000)
                        self.status["consumer_assigned"] = bool(consumer.assignment())
                        handled = False
                        for messages in batches.values():
                            for msg in messages:
                                handled = True
                                try:
                                    await self.handle(producer, msg.value)
                                except Exception:
                                    log.exception("payload handling failed; skipping")
                        if handled:
                            await consumer.commit()
                finally:
                    self.status.update(consumer_started=False,
                                       consumer_assigned=False)
                    await consumer.stop()
                    await producer.stop()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.status.update(consumer_started=False,
                                   consumer_assigned=False,
                                   last_error="Kafka ingest loop failed; retrying")
                log.exception("ingest loop crashed; restarting in 10s")
                await asyncio.sleep(10)
