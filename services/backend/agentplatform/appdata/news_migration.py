"""One-shot News archive copy and its reviewed state App definition.

The original Postgres tables remain untouched for rollback. This recipe is
not a fresh-install provisioner; the old Kafka consumer must be stopped before
the one-shot cutover takes the source locks.
"""
from __future__ import annotations

import json
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, text

from agentplatform.appdata import quotas
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import AppDataApp, AppDataRecord
from agentplatform.appdata.records import bump_counters, load_app, normalize_value, side_columns


def bundle() -> dict:
    read = ["owner", "kyle", "agent:news-librarian", "agent:pai"]
    private = {"read": read, "create": ["owner"],
               "update": ["owner"], "delete": ["kyle"]}
    return {
        "collections": [
            {"collection": "topics", "description": "News sections and their display colors.",
             "fields": {
                 "slug": {"type": "string", "max": 64, "required": True},
                 "label": {"type": "string", "max": 128, "required": True},
                 "color": {"type": "int", "min": 1, "max": 8, "required": True},
             }, "access": private,
             "writers": {"create": ["tool:news"], "update": ["tool:news"]},
             "rules": [{"kind": "unique", "fields": ["slug"]}],
             "indexed": ["slug"]},
            {"collection": "items", "description": "Fresh, deduplicated news stories.",
             "fields": {
                 "title": {"type": "string", "max": 512, "required": True},
                 "url": {"type": "url", "max": 512, "required": True, "link": True},
                 "source": {"type": "string", "max": 128},
                 "summary": {"type": "text", "max": 16000},
                 "search_text": {"type": "text", "max": 16000},
                 "topic": {"type": "ref", "collection": "topics", "required": True,
                           "on_delete": "restrict"},
                 "topic_slug": {"type": "string", "max": 64, "required": True},
                 "topic_label": {"type": "string", "max": 128, "required": True},
                 "color": {"type": "int", "min": 1, "max": 8, "required": True},
                 "day": {"type": "date", "required": True},
                 "day_key": {"type": "string", "max": 10, "required": True},
                 "published": {"type": "date"},
                 "run_id": {"type": "string", "max": 32},
                 "dedup_hash": {"type": "string", "max": 512, "required": True},
                 "url_hash": {"type": "string", "max": 64, "required": True},
                 "raw_json": {"type": "text", "max": 16000},
             }, "access": private,
             "writers": {"create": ["tool:news"], "update": ["tool:news"]},
             "rules": [{"kind": "unique", "fields": ["dedup_hash"]}],
             "indexed": ["topic_slug", "url_hash", "day"]},
        ],
        "views": [
            {"view": "today_count", "collection": "items",
             "filter": [{"field": "day", "op": "within_last", "value": "1d"}],
             "aggregates": [{"fn": "count", "as": "n"}]},
            {"view": "total", "collection": "items",
             "aggregates": [{"fn": "count", "as": "n"}]},
            {"view": "recent_count", "collection": "items",
             "filter": [{"field": "day", "op": "within_last", "value": "7d"}],
             "aggregates": [{"fn": "count", "as": "n"}]},
            {"view": "topic_count", "collection": "topics",
             "aggregates": [{"fn": "count", "as": "n"}]},
            {"view": "daily_counts", "collection": "items",
             "filter": [{"field": "day", "op": "within_last", "value": "26w"}],
             "group_by": {"field": "day", "bucket": "day"},
             "aggregates": [{"fn": "count", "as": "n"}], "limit": 200},
            {"view": "items_recent", "collection": "items",
             "sort": [{"field": "day", "dir": "desc"}], "paging": True,
             "limit": 100},
            {"view": "items_search", "collection": "items",
             "params": {"q": {"type": "string"}},
             "filter": [{"field": "search_text", "op": "contains",
                         "value": {"param": "q"}}],
             "sort": [{"field": "day", "dir": "desc"}], "paging": True,
             "limit": 100},
            {"view": "items_by_day", "collection": "items",
             "params": {"day": {"type": "string", "required": True}},
             "filter": [{"field": "day_key", "op": "eq", "value": {"param": "day"}}],
             "sort": [{"field": "topic_slug", "dir": "asc"}], "paging": True,
             "limit": 100},
            {"view": "items_by_topic", "collection": "items",
             "params": {"topic": {"type": "string", "required": True}},
             "filter": [{"field": "topic", "op": "eq",
                         "value": {"param": "topic"}}],
             "sort": [{"field": "day", "dir": "desc"}], "paging": True,
             "limit": 100},
            {"view": "topics", "collection": "topics",
             "sort": [{"field": "label", "dir": "asc"}], "limit": 100},
            {"view": "item_by_id", "collection": "items",
             "params": {"id": {"type": "string", "required": True}},
             "filter": [{"field": "id", "op": "eq", "value": {"param": "id"}}],
             "limit": 1},
        ],
        "pages": [
            {"page": "home", "title": "News", "layout": (
                '<section class="ap-stack"><header class="ap-hero">'
                '<h1>News archive</h1><p class="ap-muted">Recent stories and the '
                'topics they belong to.</p></header><div class="ap-grid">'
                '<ap-view name="today"></ap-view><ap-view name="week"></ap-view>'
                '<ap-view name="counts"></ap-view><ap-view name="topic_count"></ap-view>'
                '</div>'
                '<div class="ap-grid"><div class="ap-card">'
                '<ap-view name="calendar"></ap-view></div><div class="ap-card">'
                '<ap-view name="topics"></ap-view></div></div>'
                '<ap-view name="stories"></ap-view></section>'),
             "blocks": [
                 {"kind": "metric", "slot": "today", "view": "today_count",
                  "label": "Today"},
                 {"kind": "metric", "slot": "week", "view": "recent_count",
                  "label": "Last 7 days"},
                 {"kind": "metric", "slot": "counts", "view": "total",
                  "label": "Stories archived"},
                 {"kind": "metric", "slot": "topic_count", "view": "topic_count",
                  "label": "Topics"},
                 {"kind": "calendar", "slot": "calendar", "view": "daily_counts",
                  "title": "Story volume", "day": "day", "value": "n",
                  "day_link": {"page": "day", "param": "day"}},
                 {"kind": "table", "slot": "topics", "view": "topics",
                  "title": "Topics", "columns": [{"field": "label"},
                                                 {"field": "color", "format": "number"}],
                  "row_link": {"page": "topic", "param": "topic"}},
                 {"kind": "table", "slot": "stories", "view": "items_search",
                  "title": "Stories", "params": {"q": {"page_param": "q"}},
                  "columns": [{"field": "title"},
                              {"field": "topic_label"},
                              {"field": "source"},
                              {"field": "day", "format": "date"}],
                  "row_link": {"page": "item", "param": "id"}},
             ], "params": {"q": {"type": "string"}}},
            {"page": "day", "title": "News by day",
             "params": {"day": {"type": "string", "required": True}},
             "blocks": [{"kind": "table", "view": "items_by_day",
                         "params": {"day": {"page_param": "day"}},
                         "columns": [{"field": "title"},
                                     {"field": "topic_label"},
                                     {"field": "summary"},
                                     {"field": "published", "format": "date"}],
                         "row_link": {"page": "item", "param": "id"}}]},
            {"page": "topic", "title": "News by topic",
             "params": {"topic": {"type": "string", "required": True}},
             "blocks": [{"kind": "table", "view": "items_by_topic",
                         "params": {"topic": {"page_param": "topic"}},
                         "columns": [{"field": "title"},
                                     {"field": "day", "format": "date"},
                                     {"field": "summary"}],
                         "row_link": {"page": "item", "param": "id"}}]},
            {"page": "item", "title": "News story",
             "params": {"id": {"type": "string", "required": True}},
             "blocks": [{"kind": "detail", "view": "item_by_id",
                         "params": {"id": {"page_param": "id"}},
                         "fields": ["title", "url", "summary", "source",
                                    "topic_label", "day"]}]},
        ],
        "app_tools": [{"tool": "news", "roles": {
            "topics": {"collection": "topics", "verbs": ["read", "create", "update"]},
            "items": {"collection": "items", "verbs": ["read", "create"]},
        }}],
    }


@dataclass(frozen=True)
class ImportRecord:
    collection: str
    id: str
    doc: dict
    created_at: datetime


def topic_id(legacy_id: int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"news:topic:{legacy_id}").hex


def convert_snapshot(source: dict[str, list[dict]]) -> dict[str, list[ImportRecord]]:
    topics = {int(row["id"]): row for row in source["topics"]}
    result = {"topics": [], "items": []}
    for row in topics.values():
        result["topics"].append(ImportRecord("topics", topic_id(int(row["id"])), {
            "slug": row["slug"], "label": row["label"], "color": row["color"]},
            row.get("created_at") or datetime(1970, 1, 1, tzinfo=timezone.utc)))
    seen = set()
    for row in source["items"]:
        topic = topics.get(int(row["topic_id"]))
        if topic is None:
            raise ValueError("News item refers to a missing topic")
        key = row["dedup_hash"]
        if key in seen:
            raise ValueError("duplicate News dedup hash")
        seen.add(key)
        raw = json.dumps(row.get("raw") or {}, sort_keys=True, ensure_ascii=False)
        if len(raw) > 16000:
            raise ValueError("News raw item exceeds the archive field")
        result["items"].append(ImportRecord("items", str(row["id"]), {
            "title": row["title"], "url": row["url"], "source": row["source"],
            "summary": row["summary"],
            "search_text": row["title"] + " " + row["summary"],
            "topic": topic_id(int(row["topic_id"])),
            "topic_slug": topic["slug"], "topic_label": topic["label"],
            "color": topic["color"], "day": row["day"],
            "day_key": row["day"],
            "published": row["published"], "run_id": row["run_id"],
            "dedup_hash": key, "raw_json": raw,
            "url_hash": hashlib.sha256(key.encode()).hexdigest(),
        }, row["ingested_at"]))
    return result


async def read_source(session) -> dict[str, list[dict]]:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("legacy News copy requires PostgreSQL")
    await session.execute(text("LOCK TABLE app_news.topics, app_news.items IN SHARE MODE"))
    return {name: [dict(row) for row in (await session.execute(
        text(f"SELECT * FROM app_news.{name}"))).mappings()]
        for name in ("topics", "items")}


async def import_snapshot(session, source: dict[str, list[dict]], *, dry_run=True) -> dict:
    prepared = convert_snapshot(source)
    app = (await session.execute(select(AppDataApp).where(
        AppDataApp.name == "news"))).scalar_one_or_none()
    if app is None or app.owner_kind != "agent" or app.owner_id != "news":
        raise ValueError("approved News-owned state App is required")
    ctx = await load_app(session, app.id)
    if ctx.bundle != validate_app(bundle()):
        raise ValueError("published News definitions differ from migration recipe")
    n = (await session.execute(select(func.count()).select_from(AppDataRecord).where(
        AppDataRecord.app_id == app.id))).scalar_one()
    if n:
        raise ValueError("destination is not empty; refusing recopy")
    counts = {name: len(rows) for name, rows in prepared.items()}
    if dry_run:
        return {"dry_run": True, "counts": counts, "app_id": app.id}
    used = 0
    for name, rows in prepared.items():
        c = ctx.collection(name)
        for row in rows:
            doc = {key: normalize_value(ctx, c, key, value)
                   for key, value in row.doc.items() if value is not None}
            size = quotas.doc_bytes(doc)
            used += size
            session.add(AppDataRecord(
                app_id=app.id, collection=name, id=row.id,
                current_version=1, created_at=row.created_at, updated_at=row.created_at,
                author="migration:news", collection_version=ctx.versions[name],
                doc=doc, size_bytes=size, **side_columns(c, doc)))
    await session.flush()
    quotas.note(session, ctx, records=sum(counts.values()), bytes=used)
    await quotas.settle(session)
    await bump_counters(session, app.id, counts)
    return {"dry_run": False, "counts": counts, "app_id": app.id}
