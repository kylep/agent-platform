"""Reviewed page action admission, separate from agent-callable tool actions.

These names are code-owned. Publishing an App page only binds one of the
listed operations to its fixed collection and editable fields; it cannot
define new executable behavior. The dispatch implementation lives beside the
record engine so the confirmation, operation, and receipt can share a DB
transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
import hashlib
import json
import uuid

from sqlalchemy import select

from agentplatform.appdata import records as rec
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.models import AppDataRecord
from agentplatform.db import utcnow


@dataclass(frozen=True)
class PageToolAction:
    app: str
    collection: str
    editable_fields: tuple[str, ...]


REVIEWED: dict[str, PageToolAction] = {
    "judgment_confirm_belief": PageToolAction("judgment", "beliefs", ()),
    "judgment_correct_belief": PageToolAction(
        "judgment", "beliefs", ("claim", "scope", "reason")),
    "judgment_reject_belief": PageToolAction("judgment", "beliefs", ("reason",)),
    "judgment_delete_belief": PageToolAction("judgment", "beliefs", ()),
    "judgment_delete_prediction": PageToolAction("judgment", "predictions", ()),
    "judgment_confirm_feedback": PageToolAction(
        "judgment", "feedback", ("kyle_words",)),
    "judgment_delete_feedback": PageToolAction("judgment", "feedback", ()),
}


KYLE_REVIEW = Caller("kyle", via_tool="tool:judgment")
_BELIEF_FIELDS = ("claim", "scope", "evidence", "provenance", "source_ref",
                  "confidence", "reason", "feedback")


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def _text(value, field: str, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise RecordError("JD-REVIEW-VALUE", f"{field} needs nonempty text", 422)
    return value.strip()


async def _rows(session, ctx) -> dict[str, dict[str, AppDataRecord]]:
    """A small, consistent Judgment plan under the caller's all-collection lock."""
    names = ("beliefs", "belief_versions", "predictions", "prediction_beliefs",
             "feedback")
    rows = (await session.execute(select(AppDataRecord).where(
        AppDataRecord.app_id == ctx.app_id,
        AppDataRecord.collection.in_(names)))).scalars().all()
    return {name: {row.id: row for row in rows if row.collection == name}
            for name in names}


def _deletion_closure(rows: dict, operation: str, record_id: str) -> tuple[list[tuple[str, str]], dict]:
    """Preserve Judgment's target-loss rules without inventing a generic cascade.

    A feedback deletion removes versions it caused. Feedback that then loses
    both its targets goes too. A belief with no versions goes. Links to a
    deleted belief or prediction go, but a pin to a deleted *version* survives
    with no claim, as in the legacy review.
    """
    source = {"judgment_delete_belief": "beliefs",
              "judgment_delete_prediction": "predictions",
              "judgment_delete_feedback": "feedback"}[operation]
    if record_id not in rows[source]:
        raise RecordError("AD-NOT-FOUND", "no such Judgment record", 404)
    gone = {name: set() for name in rows}
    gone[source].add(record_id)
    changed = True
    while changed:
        before = sum(map(len, gone.values()))
        gone["belief_versions"].update(
            rid for rid, row in rows["belief_versions"].items()
            if row.doc.get("belief") in gone["beliefs"]
            or row.doc.get("feedback") in gone["feedback"])
        gone["prediction_beliefs"].update(
            rid for rid, row in rows["prediction_beliefs"].items()
            if row.doc.get("prediction") in gone["predictions"]
            or row.doc.get("belief") in gone["beliefs"])
        for rid, row in rows["feedback"].items():
            if rid in gone["feedback"]:
                continue
            prediction = row.doc.get("prediction")
            version = row.doc.get("belief_version")
            lost_target = ((prediction is not None and prediction in gone["predictions"])
                           or (version is not None and version in gone["belief_versions"]))
            if (lost_target and (prediction is None or prediction in gone["predictions"])
                    and (version is None or version in gone["belief_versions"])):
                gone["feedback"].add(rid)
        for bid in rows["beliefs"]:
            if bid in gone["beliefs"]:
                continue
            versions = {rid for rid, row in rows["belief_versions"].items()
                        if row.doc.get("belief") == bid}
            if versions and versions <= gone["belief_versions"]:
                gone["beliefs"].add(bid)
        changed = sum(map(len, gone.values())) != before

    heads = {}
    for bid, belief in rows["beliefs"].items():
        if bid in gone["beliefs"]:
            continue
        current = belief.doc.get("number")
        remaining = [row for rid, row in rows["belief_versions"].items()
                     if rid not in gone["belief_versions"]
                     and row.doc.get("belief") == bid]
        if remaining and not any(row.doc.get("number") == current for row in remaining):
            head = max(remaining, key=lambda row: row.doc["number"])
            heads[bid] = {"number": head.doc["number"],
                          **{field: head.doc.get(field) for field in _BELIEF_FIELDS}}
    targets = sorted((name, rid) for name, ids in gone.items() for rid in ids)
    return targets, heads


async def _delete_preview(session, ctx, operation: str, record_id: str) -> tuple[str, dict]:
    rows = await _rows(session, ctx)
    targets, heads = _deletion_closure(rows, operation, record_id)
    for name, rid in targets:
        await rec._authorize_delete(session, ctx, KYLE_REVIEW, name, [rid], None)
    plan = await rec.compute_plan(session, ctx, targets)
    if plan.blocked:
        raise RecordError("AD-REF-RESTRICT", "Judgment deletion has a blocking reference",
                          409, plan.summary(ctx, KYLE_REVIEW))
    summary = {"deletes": {name: sorted(rid for n, rid in targets if n == name)
                           for name in rows},
               "unlinks": sorted(plan.unlinks), "head_changes": heads}
    return _digest(summary), summary


async def prepare(session, ctx, operation: str, record_id: str | None,
                  editable: dict) -> tuple[dict, int, str | None, dict]:
    spec = REVIEWED[operation]
    if not isinstance(record_id, str) or not record_id or len(record_id) > 128:
        raise RecordError("JD-REVIEW-TARGET", "a Judgment review needs a record", 422)
    if not isinstance(editable, dict) or set(editable) - set(spec.editable_fields):
        raise RecordError("JD-REVIEW-VALUE", "undeclared review input", 422)
    collection = ctx.collection(spec.collection)
    row = await rec._fetch(session, ctx, collection, record_id)
    current = rec.present(ctx.access(collection, Caller("kyle")), row)
    if operation.startswith("judgment_delete_"):
        if editable:
            raise RecordError("JD-REVIEW-VALUE", "delete takes no input", 422)
        digest, summary = await _delete_preview(session, ctx, operation, record_id)
        return {}, row.current_version, digest, {
            "current": current, "resulting_values": None, "changes": {},
            "delete_plan": summary}

    values = dict(editable)
    if operation == "judgment_correct_belief":
        values["claim"] = _text(values.get("claim"), "claim", required=True)
        if "scope" in values:
            values["scope"] = _text(values["scope"], "scope")
        if "reason" in values:
            values["reason"] = _text(values["reason"], "reason")
    elif operation == "judgment_reject_belief":
        values["reason"] = _text(values.get("reason"), "reason", required=True)
    elif operation == "judgment_confirm_feedback" and "kyle_words" in values:
        values["kyle_words"] = _text(values["kyle_words"], "kyle_words", required=True)
    resulting = _resulting(operation, row.doc, values)
    # The record engine validates types and bounds again while applying.
    for field, value in resulting.items():
        if field in collection.fields and value is not None and not (
                field == "confirmed_at" and value == "on confirmation"):
            rec.normalize_value(ctx, collection, field, value)
    return values, row.current_version, None, {
        "current": current, "resulting_values": resulting,
        "changes": {key: resulting.get(key) for key in resulting
                    if resulting.get(key) != row.doc.get(key)},
        "delete_plan": None}


def _resulting(operation: str, current: dict, values: dict) -> dict:
    if operation == "judgment_confirm_feedback":
        return {**current, **values,
                "confirmed_at": current.get("confirmed_at") or "on confirmation"}
    number = current["number"]
    if operation == "judgment_confirm_belief":
        return {**current, "number": number + 1,
                "provenance": "kyle_confirmed", "status": "active",
                "reason": f"Kyle confirmed version {number} on his page"}
    if operation == "judgment_correct_belief":
        return {**current, "number": number + 1, "claim": values["claim"],
                "scope": values.get("scope", current.get("scope")),
                "reason": values.get("reason") or "Kyle corrected the claim on his page",
                "evidence": None, "source_ref": None,
                "provenance": "kyle_confirmed", "status": "active"}
    if operation == "judgment_reject_belief":
        return {**current, "number": number + 1, "reason": values["reason"],
                "status": "rejected"}
    raise RecordError("JD-REVIEW-ACTION", "unknown Judgment review action", 404)


async def apply(session, ctx, operation: str, record_id: str, values: dict,
                expected_version: int, intent_id: str) -> dict:
    spec = REVIEWED[operation]
    if operation.startswith("judgment_delete_"):
        rows = await _rows(session, ctx)
        targets, heads = _deletion_closure(rows, operation, record_id)
        for name, rid in targets:
            await rec._authorize_delete(session, ctx, KYLE_REVIEW, name, [rid], None)
        for bid, head in heads.items():
            row = rows["beliefs"][bid]
            await rec._update(session, ctx, KYLE_REVIEW, ctx.collection("beliefs"),
                              bid, head, row.current_version)
        plan = await rec.compute_plan(session, ctx, targets)
        if plan.blocked:
            raise RecordError("AD-REF-RESTRICT", "Judgment deletion changed", 409)
        await rec.execute_plan(session, ctx, KYLE_REVIEW, plan)
        if heads:
            await rec.bump_counters(session, ctx.app_id, ["beliefs"])
        return {"collection": spec.collection, "id": record_id, "deleted": True,
                "plan": plan.summary(ctx, KYLE_REVIEW)}

    collection = ctx.collection(spec.collection)
    row = await rec._fetch(session, ctx, collection, record_id, lock=True)
    if row.current_version != expected_version:
        raise RecordError("AD-VERSION-CONFLICT", "the reviewed record changed", 409)
    if operation == "judgment_confirm_feedback":
        patch = {**values}
        if not row.doc.get("confirmed_at"):
            patch["confirmed_at"] = utcnow().replace(tzinfo=timezone.utc).isoformat()
        result = await rec._update(session, ctx, KYLE_REVIEW, collection, record_id,
                                   patch, expected_version)
        await rec.bump_counters(session, ctx.app_id, [spec.collection])
        return {"collection": spec.collection, "id": record_id,
                "version": result.current_version}

    new_doc = _resulting(operation, row.doc, values)
    result = await rec._update(session, ctx, KYLE_REVIEW, collection, record_id,
                               new_doc, expected_version)
    version_doc = {field: result.doc.get(field) for field in _BELIEF_FIELDS}
    version_doc.update(belief=record_id, number=result.doc["number"])
    version_id = uuid.uuid5(uuid.NAMESPACE_URL,
                            f"judgment-review:{intent_id}:version").hex
    await rec._insert(session, ctx, KYLE_REVIEW, ctx.collection("belief_versions"),
                      version_doc, version_id)
    await rec.bump_counters(session, ctx.app_id, ["beliefs", "belief_versions"])
    return {"collection": "beliefs", "id": record_id,
            "version": result.current_version, "belief_version": result.doc["number"]}
