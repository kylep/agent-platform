"""One-time Judgment App definition and snapshot recipe for the state migration.

The one-shot cutover records this reviewed bundle and copies source data
into Postgres. This file is a copy recipe, not a fresh-install provisioner.
Remove it after cutover and restore proof.
No records or credentials belong here.
"""
from __future__ import annotations

import json
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text

from agentplatform.appdata import quotas
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.models import AppDataApp, AppDataRecord, AppDataRecordVersion
from agentplatform.appdata.records import (bump_counters, load_app, normalize_value,
                                           side_columns)


PRIVATE = {"read": ["owner", "kyle"]}
OUTCOMES = ["supported", "contradicted", "mixed", "context_changed", "unresolved"]
CONFIDENCE = ["low", "medium", "high"]


def bundle() -> dict:
    collections = [
        {"collection": "beliefs", "write_mode": "versioned",
         "description": "Kai's current beliefs and their complete revision history.",
         "fields": {
             # Domain version can move backwards when Kyle deletes a version.
             # The record store's optimistic-concurrency version never does.
             "number": {"type": "int", "min": 1, "required": True},
             "claim": {"type": "text", "max": 1000, "required": True},
             "scope": {"type": "text", "max": 4000},
             "evidence": {"type": "text", "max": 4000},
             "provenance": {"type": "enum", "values": ["kyle_confirmed", "kyle_relayed",
                                                      "observed", "inference", "imported"],
                            "required": True},
             "source_ref": {"type": "string", "max": 200},
             "confidence": {"type": "enum", "values": CONFIDENCE, "required": True},
             "reason": {"type": "text", "max": 4000},
             "feedback": {"type": "ref", "collection": "feedback", "on_delete": "unlink"},
             "status": {"type": "enum", "values": ["active", "superseded", "rejected"],
                        "required": True},
         },
         "access": {**PRIVATE, "create": ["owner"], "update": ["owner", "kyle"],
                    "delete": ["kyle"]},
         "writers": {"create": ["tool:judgment"], "update": ["tool:judgment"]},
         "indexed": ["status", "provenance"]},
        {"collection": "belief_versions", "write_mode": "immutable",
         "description": "Stable IDs for old and new belief versions referenced by predictions.",
         "fields": {
             "belief": {"type": "ref", "collection": "beliefs", "on_delete": "restrict",
                        "required": True},
             "number": {"type": "int", "min": 1, "required": True},
             "claim": {"type": "text", "max": 1000, "required": True},
             "provenance": {"type": "enum", "values": ["kyle_confirmed", "kyle_relayed",
                                                      "observed", "inference", "imported"],
                            "required": True},
             "scope": {"type": "text", "max": 4000},
             "evidence": {"type": "text", "max": 4000},
             "source_ref": {"type": "string", "max": 200},
             "confidence": {"type": "enum", "values": CONFIDENCE},
             "reason": {"type": "text", "max": 4000},
             "feedback": {"type": "ref", "collection": "feedback", "on_delete": "unlink"},
         },
         "access": {**PRIVATE, "create": ["owner", "kyle"], "update": [], "delete": ["kyle"]},
         "writers": {"create": ["tool:judgment"]},
         "rules": [{"kind": "unique", "fields": ["belief", "number"]}],
         "indexed": ["belief", "number"]},
        {"collection": "predictions", "write_mode": "immutable",
         "description": "Prospective or retrospective decisions Kai predicted.",
         "fields": {
             "scenario": {"type": "text", "max": 4000, "required": True},
             "alternatives": {"type": "list", "items": {"type": "string", "max": 500},
                              "max_items": 10},
             "predicted_choice": {"type": "string", "max": 1000, "required": True},
             "rationale": {"type": "text", "max": 4000},
             "confidence": {"type": "enum", "values": CONFIDENCE, "required": True},
             "timing": {"type": "enum", "values": ["prospective", "retrospective"],
                        "required": True},
             "question_ref": {"type": "string", "max": 200},
         },
         "access": {**PRIVATE, "create": ["owner"], "update": [], "delete": ["kyle"]},
         "writers": {"create": ["tool:judgment"]}, "indexed": ["timing"]},
        {"collection": "prediction_beliefs", "write_mode": "immutable",
         "description": "A prediction's claim about one exact belief version.",
         "fields": {
             "prediction": {"type": "ref", "collection": "predictions", "required": True,
                            "on_delete": "restrict"},
             # A prediction deliberately keeps its pinned version number even
             # after Kyle deletes that version. The old UI then shows a null
             # claim. A strict ref would prevent that deletion.
             "belief": {"type": "string", "max": 32, "required": True},
             "belief_version": {"type": "int", "min": 1, "required": True},
         },
         "access": {**PRIVATE, "create": ["owner"], "update": [], "delete": ["kyle"]},
         "writers": {"create": ["tool:judgment"]},
         "rules": [{"kind": "unique", "fields": ["prediction", "belief",
                                                "belief_version"]}],
         "indexed": ["prediction", "belief"]},
        {"collection": "feedback", "description": "Kyle's feedback, separate from Kai's interpretation.",
         "fields": {
             "prediction": {"type": "ref", "collection": "predictions", "on_delete": "unlink"},
             "belief_version": {"type": "ref", "collection": "belief_versions",
                                "on_delete": "unlink"},
             "kyle_words": {"type": "text", "max": 4000, "required": True},
             "source_ref": {"type": "string", "max": 200},
             "source_at": {"type": "datetime"},
             "outcome": {"type": "enum", "values": OUTCOMES, "required": True},
             "interpretation": {"type": "text", "max": 4000,
                                "access": {"read": ["owner"]}},
             "confirmed_at": {"type": "datetime"},
         },
         "access": {**PRIVATE, "create": ["owner"], "update": ["kyle"],
                    "delete": ["kyle"]},
         "writers": {"create": ["tool:judgment"]},
         "rules": [{"kind": "immutable_after_create",
                    "fields": ["prediction", "belief_version", "source_at", "outcome"]}],
         "indexed": ["prediction", "belief_version", "source_at"]},
    ]
    views = [
        {"view": "beliefs_recent", "collection": "beliefs",
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True},
        {"view": "belief_detail", "collection": "beliefs",
         "params": {"id": {"type": "string", "required": True}},
         "filter": [{"field": "id", "op": "eq", "value": {"param": "id"}}], "limit": 1},
        {"view": "predictions_recent", "collection": "predictions",
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True},
        {"view": "prediction_detail", "collection": "predictions",
         "params": {"id": {"type": "string", "required": True}},
         "filter": [{"field": "id", "op": "eq", "value": {"param": "id"}}], "limit": 1},
        {"view": "feedback_recent", "collection": "feedback",
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True},
        {"view": "belief_search", "collection": "beliefs",
         "params": {"query": {"type": "string", "required": True}},
         "filter": [{"field": "claim", "op": "contains",
                     "value": {"param": "query"}}],
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True},
        {"view": "versions_for_belief", "collection": "belief_versions",
         "params": {"belief": {"type": "string", "required": True}},
         "filter": [{"field": "belief", "op": "eq",
                     "value": {"param": "belief"}}],
         "sort": [{"field": "number", "dir": "desc"}], "paging": True},
        {"view": "version_by_number", "collection": "belief_versions",
         "params": {"belief": {"type": "string", "required": True},
                    "number": {"type": "int", "required": True}},
         "filter": [{"field": "belief", "op": "eq",
                     "value": {"param": "belief"}},
                    {"field": "number", "op": "eq",
                     "value": {"param": "number"}}], "limit": 1},
        {"view": "links_for_prediction", "collection": "prediction_beliefs",
         "params": {"prediction": {"type": "string", "required": True}},
         "filter": [{"field": "prediction", "op": "eq",
                     "value": {"param": "prediction"}}], "limit": 20},
        {"view": "feedback_for_prediction", "collection": "feedback",
         "params": {"prediction": {"type": "string", "required": True}},
         "filter": [{"field": "prediction", "op": "eq",
                     "value": {"param": "prediction"}}], "paging": True},
    ]
    pages = [
        {"page": "review", "title": "Judgment review", "blocks": [
            {"kind": "table", "view": "beliefs_recent", "title": "Beliefs",
             "columns": [{"field": "claim"}, {"field": "status"}],
             "row_link": {"page": "belief", "param": "id"}},
            {"kind": "table", "view": "predictions_recent", "title": "Predictions",
             "columns": [{"field": "scenario"}, {"field": "predicted_choice"}],
             "row_link": {"page": "prediction", "param": "id"},
             "actions": ["delete_prediction"]},
            {"kind": "table", "view": "feedback_recent", "title": "Feedback",
             "columns": [{"field": "kyle_words"}, {"field": "outcome"}],
             "actions": ["confirm_feedback", "delete_feedback"]},
        ], "actions": [
            {"kind": "tool_action", "name": "delete_prediction",
             "operation": "judgment_delete_prediction", "collection": "predictions",
             "label": "Delete prediction"},
            {"kind": "tool_action", "name": "confirm_feedback",
             "operation": "judgment_confirm_feedback", "collection": "feedback",
             "label": "Confirm Kyle's feedback", "editable_fields": ["kyle_words"]},
            {"kind": "tool_action", "name": "delete_feedback",
             "operation": "judgment_delete_feedback", "collection": "feedback",
             "label": "Delete feedback"},
        ]},
        {"page": "belief", "title": "Belief", "params": {"id": {"type": "string"}},
         "blocks": [{"kind": "detail", "view": "belief_detail", "history": True,
                     "params": {"id": {"page_param": "id"}},
                     "fields": ["claim", "scope", "evidence", "provenance", "confidence",
                                "status", "reason", "created_at", "updated_at"],
                     "actions": ["confirm_belief", "correct_belief", "reject_belief",
                                 "delete_belief"]}],
         "actions": [
             {"kind": "tool_action", "name": "confirm_belief",
              "operation": "judgment_confirm_belief", "collection": "beliefs",
              "label": "Confirm belief"},
             {"kind": "tool_action", "name": "correct_belief",
              "operation": "judgment_correct_belief", "collection": "beliefs",
              "label": "Correct belief", "editable_fields": ["claim", "scope", "reason"]},
             {"kind": "tool_action", "name": "reject_belief",
              "operation": "judgment_reject_belief", "collection": "beliefs",
              "label": "Reject belief", "editable_fields": ["reason"]},
             {"kind": "tool_action", "name": "delete_belief",
              "operation": "judgment_delete_belief", "collection": "beliefs",
              "label": "Delete belief"},
         ]},
        {"page": "prediction", "title": "Prediction", "params": {"id": {"type": "string"}},
         "blocks": [{"kind": "detail", "view": "prediction_detail",
                     "params": {"id": {"page_param": "id"}},
                     "fields": ["scenario", "alternatives", "predicted_choice", "rationale",
                                "confidence", "timing", "question_ref", "created_at"],
                     "actions": ["delete_prediction"]}],
         "actions": [{"kind": "tool_action", "name": "delete_prediction",
                      "operation": "judgment_delete_prediction", "collection": "predictions",
                      "label": "Delete prediction"}]},
    ]
    tools = [{"tool": "judgment", "roles": {
        "beliefs": {"collection": "beliefs", "verbs": ["read", "create", "update"]},
        "versions": {"collection": "belief_versions", "verbs": ["read", "create"]},
        "predictions": {"collection": "predictions", "verbs": ["read", "create"]},
        "links": {"collection": "prediction_beliefs", "verbs": ["read", "create"]},
        "feedback": {"collection": "feedback", "verbs": ["read", "create"]},
    }}]
    return {"collections": collections, "views": views, "pages": pages,
            "app_tools": tools}


@dataclass(frozen=True)
class ImportRecord:
    collection: str
    id: str
    doc: dict[str, Any]
    author: str
    created_at: datetime
    updated_at: datetime
    version: int = 1
    history: tuple[dict[str, Any], ...] = ()


def _doc(values: dict[str, Any]) -> dict[str, Any]:
    """The normal record store omits absent fields and encodes dates as ISO."""
    return {key: value.isoformat() if isinstance(value, datetime) else value
            for key, value in values.items() if value is not None}


def convert_snapshot(source: dict[str, list[dict]]) -> dict[str, list[ImportRecord]]:
    """Map one consistent legacy snapshot, preserving IDs, versions and authors.

    This is pure: it never reads a live database or writes anything. The copy
    runner must take one snapshot and compare source/destination counts before
    it enables the new writer. A missing ref is a refusal, never silently
    dropped or rewritten.
    """
    required = {"beliefs", "belief_versions", "predictions", "prediction_beliefs",
                "feedback"}
    if not required <= source.keys():
        raise ValueError(f"missing source tables: {sorted(required - source.keys())}")
    versions: dict[str, list[dict]] = defaultdict(list)
    version_ids: dict[tuple[str, int], str] = {}
    for item in source["belief_versions"]:
        key = (item["belief_id"], item["version"])
        if key in version_ids:
            raise ValueError(f"duplicate belief version {key}")
        version_ids[key] = item["id"]
        versions[item["belief_id"]].append(item)
    belief_ids = {item["id"] for item in source["beliefs"]}
    prediction_ids = {item["id"] for item in source["predictions"]}
    prediction_by_id = {item["id"]: item for item in source["predictions"]}
    feedback_ids = {item["id"] for item in source["feedback"]}
    if len(belief_ids) != len(source["beliefs"]) or len(prediction_ids) != len(source["predictions"]):
        raise ValueError("duplicate source record ID")
    out: dict[str, list[ImportRecord]] = {name: [] for name in required}

    def belief_doc(v: dict, status: str | None) -> dict:
        if v.get("feedback_id") and v["feedback_id"] not in feedback_ids:
            raise ValueError("belief version cites missing feedback")
        return _doc({"number": v["version"], "claim": v["claim"], "scope": v["scope"],
                     "evidence": v["evidence"], "provenance": v["provenance"],
                     "source_ref": v["source_ref"], "confidence": v["confidence"],
                     "reason": v["reason"], "feedback": v["feedback_id"],
                     "status": status})

    for belief in source["beliefs"]:
        bid = belief["id"]
        ordered = sorted(versions[bid], key=lambda v: v["version"])
        if not ordered or ordered[-1]["version"] != belief["current_version"]:
            raise ValueError(f"belief {bid} has no current version")
        current = ordered[-1]
        # Legacy stored status on the head only. Do not invent historical
        # status values for old snapshots.
        past = tuple({"version": v["version"], "doc": belief_doc(v, None),
                      "author": v["author"], "updated_at": v["created_at"]}
                     for v in ordered[:-1])
        out["beliefs"].append(ImportRecord(
            "beliefs", bid, belief_doc(current, belief["status"]), current["author"],
            belief["created_at"], current["created_at"], belief["current_version"], past))
    for v in source["belief_versions"]:
        if v["belief_id"] not in belief_ids:
            raise ValueError("orphan belief version")
        out["belief_versions"].append(ImportRecord(
            "belief_versions", v["id"], _doc({
                "belief": v["belief_id"], "number": v["version"], "claim": v["claim"],
                "scope": v["scope"], "evidence": v["evidence"],
                "provenance": v["provenance"], "source_ref": v["source_ref"],
                "confidence": v["confidence"], "reason": v["reason"],
                "feedback": v["feedback_id"]}), v["author"], v["created_at"],
            v["created_at"]))
    for p in source["predictions"]:
        alternatives = json.loads(p["alternatives"])
        if not isinstance(alternatives, list):
            raise ValueError("prediction alternatives are not a list")
        out["predictions"].append(ImportRecord(
            "predictions", p["id"], _doc({
                "scenario": p["scenario"], "alternatives": alternatives,
                "predicted_choice": p["predicted_choice"], "rationale": p["rationale"],
                "confidence": p["confidence"], "timing": p["timing"],
                "question_ref": p["question_ref"]}), p["author"], p["created_at"],
            p["created_at"]))
    for link in source["prediction_beliefs"]:
        pid, bid, number = (link["prediction_id"], link["belief_id"],
                            link["belief_version"])
        if pid not in prediction_ids:
            raise ValueError("orphan prediction link")
        # A deleted belief/version deliberately leaves this historical pin.
        key = f"{pid}:{bid}:{number}"
        out["prediction_beliefs"].append(ImportRecord(
            "prediction_beliefs", uuid.uuid5(uuid.NAMESPACE_OID, key).hex,
            {"prediction": pid, "belief": bid, "belief_version": number},
            prediction_by_id[pid]["author"], prediction_by_id[pid]["created_at"],
            prediction_by_id[pid]["created_at"]))
    for f in source["feedback"]:
        if f["prediction_id"] and f["prediction_id"] not in prediction_ids:
            raise ValueError("feedback cites missing prediction")
        ref = None
        if f["belief_id"]:
            ref = version_ids.get((f["belief_id"], f["belief_version"]))
            if ref is None:
                raise ValueError("feedback cites missing belief version")
        out["feedback"].append(ImportRecord(
            "feedback", f["id"], _doc({
                "prediction": f["prediction_id"], "belief_version": ref,
                "kyle_words": f["kyle_words"], "source_ref": f["source_ref"],
                "source_at": f["source_at"], "outcome": f["outcome"],
                "interpretation": f["interpretation"],
                "confirmed_at": f["confirmed_at"]}), f["author"], f["created_at"],
            f["confirmed_at"] or f["created_at"]))
    return out


SOURCE_TABLES = ("beliefs", "belief_versions", "predictions",
                 "prediction_beliefs", "feedback")


async def read_source(session) -> dict[str, list[dict]]:
    """Take one locked, short-lived source snapshot inside the caller's txn.

    The caller must commit or roll back promptly. Locks close the gap between
    reading the five legacy tables and validating their cross references.
    They do *not* by themselves switch the writer; a cutover needs a separate
    freeze gate and rollback mechanism before this importer is used live.
    """
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("legacy Judgment copy requires PostgreSQL")
    await session.execute(text("LOCK TABLE " + ", ".join(
        f"app_judgment.{name}" for name in SOURCE_TABLES) + " IN SHARE MODE"))
    return {name: [dict(row) for row in (await session.execute(
        text(f"SELECT * FROM app_judgment.{name}"))).mappings()]
            for name in SOURCE_TABLES}


def _validated_doc(ctx, collection: str, doc: dict, *, historical: bool = False) -> dict:
    c = ctx.collection(collection)
    unknown = set(doc) - set(c.fields)
    if unknown:
        raise ValueError(f"{collection}: undeclared source fields: {sorted(unknown)}")
    normalized = {name: normalize_value(ctx, c, name, value)
                  for name, value in doc.items()}
    missing = {name for name, field in c.fields.items()
               if field.required and normalized.get(name) is None
               and not (historical and collection == "beliefs" and name == "status")}
    if missing:
        raise ValueError(f"{collection}: missing required fields: {sorted(missing)}")
    return {name: value for name, value in normalized.items() if value is not None}


async def import_snapshot(session, source: dict[str, list[dict]], *,
                          app_name: str = "judgment", dry_run: bool = True) -> dict:
    """Check, then atomically copy into an empty, approved state App.

    There is intentionally no overwrite mode. A final recopy after a source
    freeze must reset a *staging* App explicitly, after checking that the
    destination has never taken writes. Existing records are a hard refusal.
    """
    records = convert_snapshot(source)
    app = (await session.execute(select(AppDataApp).where(
        AppDataApp.name == app_name))).scalar_one_or_none()
    if app is None or app.owner_kind != "agent" or app.owner_id != "kai":
        raise ValueError("an approved Kai-owned Judgment state App is required")
    ctx = await load_app(session, app.id)
    if ctx.bundle != validate_app(bundle()):
        raise ValueError("published Judgment definitions differ from the migration recipe")
    existing = (await session.execute(select(func.count()).select_from(AppDataRecord).where(
        AppDataRecord.app_id == app.id))).scalar_one()
    if existing:
        raise ValueError(f"destination is not empty ({existing} records); refusing recopy")
    prepared = []
    for collection in SOURCE_TABLES:
        c = ctx.collection(collection)
        for item in records[collection]:
            doc = _validated_doc(ctx, collection, item.doc)
            history = tuple({**old, "doc": _validated_doc(ctx, collection, old["doc"],
                                                           historical=True)}
                            for old in item.history)
            prepared.append((c, item, doc, history))
    counts = {name: len(records[name]) for name in SOURCE_TABLES}
    if dry_run:
        return {"dry_run": True, "counts": counts, "app_id": app.id}

    bytes_used = 0
    for c, item, doc, history in prepared:
        size = quotas.doc_bytes(doc)
        bytes_used += size
        session.add(AppDataRecord(
            app_id=app.id, collection=c.collection, id=item.id,
            current_version=item.version, created_at=item.created_at,
            updated_at=item.updated_at, author=item.author, via=None,
            collection_version=ctx.versions[c.collection], doc=doc,
            size_bytes=size, **side_columns(c, doc)))
        for old in history:
            session.add(AppDataRecordVersion(
                app_id=app.id, collection=c.collection, record_id=item.id,
                version=old["version"], author=old["author"], via=None,
                collection_version=ctx.versions[c.collection],
                updated_at=old["updated_at"], doc=old["doc"]))
    await session.flush()
    quotas.note(session, ctx, records=len(prepared), bytes=bytes_used)
    await quotas.settle(session)
    await bump_counters(session, app.id, list(SOURCE_TABLES))
    return {"dry_run": False, "counts": counts, "app_id": app.id,
            "history": sum(len(item.history) for values in records.values()
                           for item in values)}


if __name__ == "__main__":
    print(json.dumps(bundle(), indent=2))
