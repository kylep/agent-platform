"""Confirmed, single-use writes from published typed/v2 page templates.

The browser can name a page, one of its bound templates, and editable values.
The server freezes the resulting operation for five minutes. Dispatch takes
the same collection locks as ordinary record writes, recomputes the target and
delete plan, and commits the write with a unique receipt. A retry reads that
receipt instead of performing a second write.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta, timezone

from sqlalchemy import func, select

from agentplatform.appdata import lifecycle as L
from agentplatform.appdata import quotas
from agentplatform.appdata import records as rec
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import CreateTemplate, DeleteTemplate
from agentplatform.appdata.models import (
    AppDataApp,
    AppDataPageIntent,
    AppDataPageReceipt,
)
from agentplatform.db import utcnow

TTL = timedelta(minutes=5)
PRINCIPAL_CALLS_PER_HOUR = 30
APP_CALLS_PER_HOUR = 120
KYLE = Caller("kyle")


def _page_caller(template, collection) -> Caller:
    fields = frozenset(getattr(template, "editable_fields", [])) | frozenset(
        getattr(template, "presets", {}))
    verb = "update" if template.kind == "new_version" else template.kind
    return Caller("kyle", page_scope=(collection.collection, verb, fields))


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     default=str).encode()).hexdigest()


def _frozen_payload(intent: AppDataPageIntent) -> dict:
    """Rebuild the exact envelope signed when the intent was created."""
    return {"app_id": intent.app_id, "page": intent.page,
            "page_version": intent.page_version,
            "approved_version": intent.approved_version,
            "authority_generation": intent.authority_generation,
            "template": intent.template, "verb": intent.verb,
            "collection": intent.collection, "record_id": intent.record_id,
            "record_version": intent.record_version, "values": intent.values,
            "plan_digest": intent.plan_digest}


def _refuse(message: str, status: int = 409) -> RecordError:
    return RecordError("AD-CONFIRM-AGAIN", message + "; confirm again", status)


async def _published(session, app_ref: str, page_name: str, template_name: str):
    app = await L._app(session, app_ref)
    if app.status != "active":
        raise RecordError("AD-APP-RETIRED", "this App is retired", 409)
    ctx = await rec.load_app(session, app.id)
    page = ctx.bundle.pages.get(page_name)
    if page is None or not L.can_read_page(ctx, KYLE, page):
        raise RecordError("AD-NO-PAGE", "no readable published page", 404)
    template = next((t for t in page.actions if t.name == template_name), None)
    if template is None or not any(template_name in getattr(b, "actions", [])
                                   for b in page.blocks):
        raise RecordError("AD-NO-TEMPLATE", "no action template bound to this page", 404)
    collection = ctx.collection(template.collection)
    verb = "update" if template.kind == "new_version" else template.kind
    if collection.writers is not None and getattr(collection.writers, verb):
        raise RecordError("AD-TOOL-ONLY", "this collection writes only through its tool", 403)
    if template.kind == "new_version" and collection.write_mode != "versioned":
        raise RecordError("AD-NOT-VERSIONED", "new_version requires a versioned collection", 409)
    version = L.page_version(await L._rows(session, app.id), app, page_name)
    return app, ctx, template, collection, version


async def _snapshot(session, ctx, template, collection, record_id, editable):
    caller = _page_caller(template, collection)
    if not isinstance(editable, dict):
        raise RecordError("AD-TEMPLATE-VALUES", "editable values must be an object", 422)
    if isinstance(template, DeleteTemplate):
        if editable:
            raise RecordError("AD-TEMPLATE-VALUES", "delete takes no editable values", 422)
        values = {}
    else:
        forbidden = sorted(set(editable) - set(template.editable_fields))
        if forbidden:
            raise RecordError("AD-TEMPLATE-VALUES", "only editable fields may be submitted",
                              422, {"fields": forbidden})
        values = {**template.presets, **editable}
    if isinstance(template, CreateTemplate):
        if record_id is not None:
            raise RecordError("AD-TEMPLATE-TARGET", "create has no target record", 422)
        normalized = rec._prepare(ctx, caller, collection, values)
        return normalized, None, None, {"current": None, "resulting_values": normalized,
                                        "changes": normalized, "delete_plan": None}
    if not isinstance(record_id, str) or not record_id or len(record_id) > 128:
        raise RecordError("AD-TEMPLATE-TARGET", "update and delete need a record id", 422)
    access = ctx.access(collection, caller)
    access.require_rows()
    access.require_verb("update" if template.kind == "new_version" else template.kind)
    row = await rec._fetch(session, ctx, collection, record_id)
    current = rec.present(access, row)
    if isinstance(template, DeleteTemplate):
        await rec._authorize_delete(session, ctx, caller, collection.collection,
                                    [record_id], {record_id: row.current_version})
        plan = await rec.compute_plan(session, ctx, [(collection.collection, record_id)])
        if plan.blocked:
            raise RecordError("AD-REF-RESTRICT", "delete is blocked by a reference", 409,
                              plan.summary(ctx, caller))
        raw_plan = {"deletes": sorted(plan.deletes), "unlinks": sorted(plan.unlinks),
                    "blocked": sorted(plan.blocked)}
        return values, row.current_version, _digest(raw_plan), {
            "current": current, "resulting_values": None, "changes": {},
            "delete_plan": plan.summary(ctx, caller)}
    rec._check_input(collection, access, values, "update")
    normalized = {k: rec.normalize_value(ctx, collection, k, v)
                  for k, v in values.items()}
    resulting = {**row.doc, **normalized}
    resulting = {k: v for k, v in resulting.items() if v is not None}
    readable = {k: v for k, v in resulting.items() if access.can_read(k)}
    return normalized, row.current_version, None, {
        "current": current, "resulting_values": readable, "changes": normalized,
        "restricted": sorted(set(resulting) - set(readable)), "delete_plan": None}


async def create(session, app_ref: str, *, page: str, template: str,
                 record_id: str | None = None, values: dict | None = None) -> dict:
    app, ctx, action, collection, version = await _published(session, app_ref, page,
                                                               template)
    normalized, record_version, plan_digest, confirmation = await _snapshot(
        session, ctx, action, collection, record_id,
        values if values is not None else {})
    payload = {"app_id": app.id, "page": page, "page_version": version,
               "approved_version": app.approved_version,
               "authority_generation": app.authority_generation,
               "template": template, "verb": action.kind,
               "collection": collection.collection, "record_id": record_id,
               "record_version": record_version, "values": normalized,
               "plan_digest": plan_digest}
    intent = AppDataPageIntent(
        **payload, payload_digest=_digest(payload), confirmation=confirmation,
        expires_at=utcnow() + TTL)
    session.add(intent)
    await session.commit()
    return {"intent_id": intent.id, "expires_at": intent.expires_at.isoformat(),
            "digest": intent.payload_digest, "confirmation": confirmation,
            "action": action.kind, "collection": collection.collection,
            "record_id": record_id}


async def _budget(session, app_id: str) -> None:
    await session.execute(select(AppDataApp.id).where(AppDataApp.id == app_id)
                          .with_for_update())
    since = utcnow() - timedelta(hours=1)
    base = AppDataPageReceipt.created_at >= since
    personal = (await session.execute(select(func.count()).select_from(
        AppDataPageReceipt).where(base, AppDataPageReceipt.principal == "kyle",
                                  AppDataPageReceipt.app_id == app_id))).scalar_one()
    total = (await session.execute(select(func.count()).select_from(
        AppDataPageReceipt).where(base, AppDataPageReceipt.app_id == app_id))).scalar_one()
    if personal >= PRINCIPAL_CALLS_PER_HOUR or total >= APP_CALLS_PER_HOUR:
        raise RecordError("AD-ACTION-BUDGET", "page action budget exhausted", 429)


async def dispatch(session, intent_id: str, digest: str) -> dict:
    first = await session.get(AppDataPageIntent, intent_id)
    if first is None:
        raise RecordError("AD-NO-INTENT", "unknown page intent", 404)
    app = await L._app(session, first.app_id)
    ctx = await rec.load_app(session, app.id)
    lock = (rec.delete_lock(session, ctx, first.collection) if first.verb == "delete"
            else rec.write_lock(session, ctx, [first.collection]))
    async with lock:
        intent = await session.get(AppDataPageIntent, intent_id, with_for_update=True,
                                   populate_existing=True)
        if _digest(_frozen_payload(intent)) != intent.payload_digest:
            raise _refuse("the frozen intent changed")
        prior = (await session.execute(select(AppDataPageReceipt).where(
            AppDataPageReceipt.intent_id == intent_id))).scalar_one_or_none()
        if prior is not None:
            if digest != intent.payload_digest:
                raise _refuse("the confirmation digest differs")
            return {**prior.result, "replayed": True}
        expires = intent.expires_at.replace(tzinfo=intent.expires_at.tzinfo or timezone.utc)
        if expires <= utcnow() or digest != intent.payload_digest:
            raise _refuse("the confirmation expired or changed")
        app, ctx, action, collection, version = await _published(
            session, intent.app_id, intent.page, intent.template)
        if (version != intent.page_version or app.approved_version != intent.approved_version
                or app.authority_generation != intent.authority_generation
                or action.kind != intent.verb or collection.collection != intent.collection):
            raise _refuse("the published App changed")
        editable = {k: v for k, v in intent.values.items()
                    if k in getattr(action, "editable_fields", [])}
        normalized, record_version, plan_digest, _ = await _snapshot(
            session, ctx, action, collection, intent.record_id, editable)
        if (record_version != intent.record_version or plan_digest != intent.plan_digest
                or normalized != intent.values):
            raise _refuse("the target or delete plan changed")
        await _budget(session, app.id)
        caller = _page_caller(action, collection)
        if intent.verb == "create":
            row = await rec._insert(session, ctx, caller, collection, intent.values)
            await rec.bump_counters(session, app.id, [collection.collection])
            result = {"collection": collection.collection, "id": row.id,
                      "version": row.current_version}
        elif intent.verb in ("update", "new_version"):
            row = await rec._update(session, ctx, caller, collection, intent.record_id,
                                    intent.values, intent.record_version)
            await rec.bump_counters(session, app.id, [collection.collection])
            result = {"collection": collection.collection, "id": row.id,
                      "version": row.current_version}
        else:
            plan = await rec.compute_plan(session, ctx,
                                          [(collection.collection, intent.record_id)])
            await rec.execute_plan(session, ctx, caller, plan)
            result = {"collection": collection.collection, "id": intent.record_id,
                      "deleted": True, "plan": plan.summary(ctx, caller)}
        session.add(AppDataPageReceipt(intent_id=intent_id, app_id=app.id,
                                       principal="kyle", result=result))
        await quotas.settle(session)
        await session.commit()
        return result
