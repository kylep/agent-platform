"""Reviewed Running App state and a lossless legacy snapshot mapping.

This is a migration recipe, not a seed provisioner: a fresh install creates
zero Apps. The one-shot cutover records this reviewed bundle and copies data
in one transaction. No Strava credential or private activity lives here.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text

from agentplatform.appdata import quotas
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import AppDataApp, AppDataRecord
from agentplatform.appdata.records import bump_counters, load_app, normalize_value, side_columns


def bundle() -> dict:
    private = {"read": ["owner", "kyle"], "create": ["owner"],
               "update": ["owner"], "delete": ["kyle"]}
    activities = {
        "collection": "activities", "description": "Strava activities, one per external ID.",
        "fields": {
            "strava_id": {"type": "string", "max": 24, "required": True},
            "day": {"type": "date", "required": True},
            "name": {"type": "string", "max": 200},
            "type": {"type": "string", "max": 32, "required": True},
            "distance_m": {"type": "int", "min": 0, "required": True},
            "moving_time_s": {"type": "int", "min": 0, "required": True},
            "elevation_m": {"type": "number"},
            "avg_hr": {"type": "number"},
            "max_hr": {"type": "number"},
        }, "access": private,
        "writers": {"create": ["tool:strava"],
                    "update": ["tool:strava"]},
        "rules": [{"kind": "unique", "fields": ["strava_id"]}],
        "indexed": ["strava_id", "day", "type"]}
    briefs = {
        "collection": "briefs", "description": "One coach brief per completed week.",
        "fields": {
            "week_start": {"type": "date", "required": True},
            "body": {"type": "text", "max": 16000},
            "highlights": {"type": "list", "items": {"type": "string", "max": 500},
                           "max_items": 20},
            "tags": {"type": "list", "items": {"type": "string", "max": 64},
                     "max_items": 20},
            "distance_m": {"type": "int", "min": 0, "required": True},
            "runs": {"type": "int", "min": 0, "required": True},
            "run_id": {"type": "string", "max": 32},
            "posted": {"type": "bool", "required": True},
        }, "access": private,
        "writers": {"create": ["tool:running"],
                    "update": ["tool:running"]},
        "rules": [{"kind": "unique", "fields": ["week_start"]}],
        "indexed": ["week_start"]}
    sync = {
        "collection": "sync_state", "description": "Last completed Strava ingest.",
        "fields": {
            "source": {"type": "enum", "values": ["strava"], "required": True},
            "completed_at": {"type": "datetime", "required": True},
            "after": {"type": "date"},
            "count": {"type": "int", "min": 0},
        }, "access": private,
        "writers": {"create": ["tool:strava"],
                    "update": ["tool:strava"]},
        "rules": [{"kind": "unique", "fields": ["source"]}],
        "indexed": ["source", "completed_at"]}
    return {
        "collections": [activities, briefs, sync],
        "views": [
            {"view": "dashboard", "tool": "running", "action": "dashboard",
             "sources": ["activities"]},
            {"view": "calendar", "tool": "running", "action": "calendar",
             "sources": ["activities"]},
            {"view": "weekly", "collection": "activities",
             "filter": [
                 {"field": "day", "op": "within_last", "value": "12w"},
                 {"field": "type", "op": "in",
                  "value": ["Run", "TrailRun", "VirtualRun"]}],
             "group_by": {"field": "day", "bucket": "week"},
             "aggregates": [
                 {"fn": "sum", "field": "distance_m", "divide_by": 1000,
                  "as": "distance_km"},
                 {"fn": "count", "as": "runs"},
                 {"fn": "sum", "field": "moving_time_s", "as": "moving_time_s"}],
             "limit": 12},
            {"view": "activities_recent", "collection": "activities",
             "sort": [{"field": "day", "dir": "desc"}], "limit": 100,
             "paging": True},
            {"view": "briefs_recent", "collection": "briefs",
             "sort": [{"field": "week_start", "dir": "desc"}], "limit": 60,
             "paging": True},
            {"view": "sync_status", "collection": "sync_state", "limit": 1},
            {"view": "activity_by_strava_id", "collection": "activities",
             "params": {"strava_id": {"type": "string", "required": True}},
             "filter": [{"field": "strava_id", "op": "eq",
                         "value": {"param": "strava_id"}}], "limit": 1},
            {"view": "brief_by_week", "collection": "briefs",
             "params": {"week_start": {"type": "date", "required": True}},
             "filter": [{"field": "week_start", "op": "eq",
                         "value": {"param": "week_start"}}], "limit": 1},
        ],
        "pages": [{"page": "home", "title": "Running Coach",
                   "layout": (
                       '<section class="ap-stack">'
                       '<header class="ap-hero"><h1>Your running history</h1>'
                       '<p class="ap-muted">Activities, trends and your coach\'s notes.</p>'
                       '</header>'
                       '<ap-view name="summary"></ap-view>'
                       '<div class="ap-grid"><div class="ap-card">'
                       '<ap-view name="calendar"></ap-view></div>'
                       '<div class="ap-card"><ap-view name="weekly"></ap-view></div></div>'
                       '<ap-view name="records"></ap-view>'
                       '<div class="ap-grid"><div class="ap-card">'
                       '<ap-view name="activities"></ap-view></div>'
                       '<div class="ap-card"><ap-view name="briefs"></ap-view></div></div>'
                       '</section>'),
                   "blocks": [
            {"kind": "stat_row", "slot": "summary", "view": "dashboard", "title": "At a glance",
             "columns": [{"field": "total_km", "label": "Distance (km)",
                          "format": "number"},
                         {"field": "activities", "label": "Activities", "format": "number"},
                         {"field": "runs", "label": "Runs", "format": "number"},
                         {"field": "total_elevation_m", "label": "Climbing (m)",
                          "format": "number"}]},
            {"kind": "calendar", "slot": "calendar", "view": "calendar", "title": "Activity calendar",
             "day": "day", "value": "distance_km", "unit": "km"},
            {"kind": "chart", "slot": "weekly", "view": "weekly", "title": "Weekly distance",
             "x": "day", "y": "distance_km", "unit": "km"},
            {"kind": "stat_row", "slot": "records", "view": "dashboard", "title": "Personal records",
             "columns": [{"field": "longest_run_km", "label": "Longest run (km)",
                          "format": "number"},
                         {"field": "fastest_5k", "label": "Fastest 5K"},
                         {"field": "fastest_10k", "label": "Fastest 10K"},
                         {"field": "longest_streak", "label": "Longest streak",
                          "format": "number"},
                         {"field": "current_streak", "label": "Current streak",
                          "format": "number"}]},
            {"kind": "table", "slot": "activities", "view": "activities_recent", "title": "Recent activities",
             "columns": [{"field": "day"}, {"field": "name"},
                         {"field": "distance_m"}, {"field": "moving_time_s"}]},
            {"kind": "table", "slot": "briefs", "view": "briefs_recent", "title": "Coach's briefs",
             "columns": [{"field": "week_start"}, {"field": "body"}]},
        ]}],
        "app_tools": [{"tool": "running", "roles": {
            "activities": {"collection": "activities", "verbs": ["read"]},
            "briefs": {"collection": "briefs", "verbs": ["read", "create", "update"]},
            "sync": {"collection": "sync_state", "verbs": ["read"]},
        }}, {"tool": "strava", "roles": {
            "activities": {"collection": "activities", "verbs": ["read", "create", "update"]},
            "sync": {"collection": "sync_state", "verbs": ["read", "create", "update"]},
        }}],
    }


@dataclass(frozen=True)
class ImportRecord:
    collection: str
    id: str
    doc: dict[str, Any]
    created_at: datetime


def _id(collection: str, key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"running:{collection}:{key}").hex


def convert_snapshot(source: dict[str, list[dict]]) -> dict[str, list[ImportRecord]]:
    if not {"activities", "briefs"} <= source.keys():
        raise ValueError("Running snapshot needs activities and briefs")
    result: dict[str, list[ImportRecord]] = {"activities": [], "briefs": []}
    activity_ids, brief_weeks = set(), set()
    for a in source["activities"]:
        sid = str(a["id"])
        if sid in activity_ids:
            raise ValueError("duplicate Strava activity ID")
        activity_ids.add(sid)
        result["activities"].append(ImportRecord("activities", _id("activity", sid), {
            "strava_id": sid, "day": a["day"], "name": a["name"],
            "type": a["type"], "distance_m": a["distance_m"],
            "moving_time_s": a["moving_time_s"], "elevation_m": a["elevation_m"],
            "avg_hr": a["avg_hr"], "max_hr": a["max_hr"]}, a["created_at"]))
    for b in source["briefs"]:
        week = b["week_start"]
        if week in brief_weeks:
            raise ValueError("duplicate coach brief week")
        brief_weeks.add(week)
        result["briefs"].append(ImportRecord("briefs", _id("brief", week), {
            "week_start": week, "body": b["body"],
            "highlights": b["highlights"] or [], "tags": b["tags"] or [],
            "distance_m": b["distance_m"], "runs": b["runs"],
            "run_id": b["run_id"], "posted": b["posted"]}, b["created_at"]))
    return result


async def read_source(session) -> dict[str, list[dict]]:
    """Read a consistent legacy snapshot while the cutover has frozen its writer."""
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("legacy Running copy requires PostgreSQL")
    await session.execute(text("LOCK TABLE app_running.activities, app_running.briefs IN SHARE MODE"))
    return {table: [dict(row) for row in (await session.execute(
        text(f"SELECT * FROM app_running.{table}"))).mappings()]
            for table in ("activities", "briefs")}


async def import_snapshot(session, source: dict[str, list[dict]], *,
                          app_name: str = "running", dry_run: bool = True) -> dict:
    """Validate and atomically copy a frozen source into an empty approved App.

    The importer deliberately has no overwrite mode. A caller must first
    freeze the old Kafka consumer, hold a source snapshot, and verify that
    the new writer has not started. Any failure rolls the destination back.
    """
    records = convert_snapshot(source)
    app = (await session.execute(select(AppDataApp).where(
        AppDataApp.name == app_name))).scalar_one_or_none()
    if app is None or app.owner_kind != "agent" or app.owner_id != "running-coach":
        raise ValueError("an approved Running Coach-owned state App is required")
    ctx = await load_app(session, app.id)
    if ctx.bundle != validate_app(bundle()):
        raise ValueError("published Running definitions differ from the migration recipe")
    existing = (await session.execute(select(func.count()).select_from(AppDataRecord).where(
        AppDataRecord.app_id == app.id))).scalar_one()
    if existing:
        raise ValueError(f"destination is not empty ({existing} records); refusing recopy")
    prepared = []
    for collection, items in records.items():
        c = ctx.collection(collection)
        for item in items:
            unknown = set(item.doc) - set(c.fields)
            if unknown:
                raise ValueError(f"{collection}: undeclared fields: {sorted(unknown)}")
            doc = {key: normalize_value(ctx, c, key, value)
                   for key, value in item.doc.items() if value is not None}
            missing = {key for key, field in c.fields.items()
                       if field.required and doc.get(key) is None}
            if missing:
                raise ValueError(f"{collection}: missing fields: {sorted(missing)}")
            prepared.append((c, item, doc))
    counts = {name: len(items) for name, items in records.items()}
    if dry_run:
        return {"dry_run": True, "counts": counts, "app_id": app.id}

    bytes_used = 0
    for c, item, doc in prepared:
        size = quotas.doc_bytes(doc)
        bytes_used += size
        session.add(AppDataRecord(
            app_id=app.id, collection=c.collection, id=item.id,
            current_version=1, created_at=item.created_at,
            updated_at=item.created_at, author="running-coach", via=None,
            collection_version=ctx.versions[c.collection], doc=doc,
            size_bytes=size, **side_columns(c, doc)))
    await session.flush()
    quotas.note(session, ctx, records=len(prepared), bytes=bytes_used)
    await quotas.settle(session)
    await bump_counters(session, app.id, list(records))
    return {"dry_run": False, "counts": counts, "app_id": app.id}
