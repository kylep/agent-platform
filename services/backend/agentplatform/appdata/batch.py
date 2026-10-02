"""Batch writes and batch jobs (design 39, "Collections" → "Batch writes").

`batch` writes up to 5,000 records or 5 MiB into one collection in one
transaction. Every record goes through the same `_insert` / `_update` path a
single write takes, so access, tool-only writers, rules, refs and `unique`
apply exactly as they do one at a time.

Modes:
- **insert:** every record is new; a `unique` clash is that record's error.
- **upsert:** keyed on a declared `unique` rule (refused if the collection has
  none; named with `key` when it has several). A match in an `editable`
  collection is updated in place under its current version, with history,
  patching only the fields given. A match in an `immutable` collection is
  replaced whole, and only when its values differ: the version moves so
  readers and caches see the change, but nothing goes to `record_versions`,
  since an immutable record has no history to keep. A replace needs the
  update verb as well as create.
- **skip_existing:** a record clashing with any `unique` rule is skipped and
  answers with the id of the record already there (TCMS runs re-sent after a
  retry). Also refused without a `unique` rule: ids are the server's, so
  nothing else could make a record "existing".

Every mode can create, so the caller must hold the create verb up front.

Errors are per record (`index`, `code`, `message`). A call is all-or-nothing
by default: one bad record and nothing commits, and the refusal
(`AD-BATCH-REJECTED`) lists every error found, so the caller fixes them all
in one round. `on_error: "skip"` commits the valid records and reports the
rest. Only `RecordError`s are per record, and the engine raises them before
it changes anything, so skipping one leaves the transaction clean; any other
failure aborts the call.

**Batch jobs** stage larger writes: `open_staging_set` binds a set to its
creator (principal, tool, call) and the collection versions it was opened
against; `stage` adds batches to it, each checked for shape and limits but
invisible, because staged records live in their own table that no read
touches; `commit_staging_set` re-validates the App, the schema versions, the
creator's authority and the quota hook, then replays the staged records in
arrival order and publishes them in one transaction. A commit is always
all-or-nothing, and a refused commit leaves the set open so the creator can
abandon it or, after a transient refusal, retry. Sets expire 24 hours after
they open; `prune_staging_sets` is the sweep.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field as dc_field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, insert, select, update

from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.appdata import quotas
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import SYSTEM_FIELDS, CollectionDef, UniqueRule
from agentplatform.appdata.models import AppDataRecord, AppDataStagedRecord, AppDataStagingSet
from agentplatform.appdata.records import (R, AppContext, _check_input,
                                           _check_refs, _check_unique, _check_writer_rules,
                                           _insert, _require_active, _update,
                                           bump_counters, field_expr, format_datetime,
                                           load_app, normalize_value, scope, set_size,
                                           side_columns, write_lock)
from agentplatform.db import utcnow

MAX_BATCH_RECORDS = 5_000
MAX_BATCH_BYTES = 5 * 1024 * 1024
# A refusal lists this many errors and counts the rest.
MAX_REPORTED_ERRORS = 100
STAGING_TTL = timedelta(hours=24)
MODES = ("insert", "upsert", "skip_existing")
ON_ERROR = ("fail", "skip")
# Staged records are replayed this many at a time, so a large commit never
# holds the whole set in memory.
_REPLAY_CHUNK = 1_000
_IN_CHUNK = 500

S = AppDataStagedRecord
SS = AppDataStagingSet


async def check_quotas(session, ctx: AppContext, *, records: int, bytes: int) -> None:
    """The quota pre-check (design 39, "Storage" → "Quotas"): refuse up front
    when the App or its owner has no room left, before replaying anything.
    `records` and `bytes` are what the write may add at most (an upsert that
    matches adds none); the exact charge is `quotas.settle`, just before the
    commit."""
    await quotas.precheck(session, ctx, records=records, bytes=bytes)


# --- input --------------------------------------------------------------------------------

def _check_payload(records: Any) -> int:
    """The call's size in bytes, or a refusal of the whole call."""
    if not isinstance(records, list) or not all(isinstance(r, dict) for r in records):
        raise RecordError("AD-INVALID-VALUE", "records must be a list of objects", 422)
    if len(records) > MAX_BATCH_RECORDS:
        raise RecordError("AD-BATCH-TOO-LARGE", f"at most {MAX_BATCH_RECORDS} records per "
                          "call; use a batch job for more", 413,
                          {"max_records": MAX_BATCH_RECORDS, "records": len(records)})
    size = len(json.dumps(records, separators=(",", ":"), ensure_ascii=False,
                          default=str).encode())
    if size > MAX_BATCH_BYTES:
        raise RecordError("AD-BATCH-TOO-LARGE", f"at most {MAX_BATCH_BYTES} bytes per call; "
                          "use a batch job for more", 413,
                          {"max_bytes": MAX_BATCH_BYTES, "bytes": size})
    return size


def _unique_rules(c: CollectionDef) -> list[list[str]]:
    return [list(rule.fields) for rule in c.rules if isinstance(rule, UniqueRule)]


def _key_fields(c: CollectionDef, mode: str, key: list[str] | None) -> list[str] | None:
    """The upsert key: the fields of one declared `unique` rule."""
    if mode not in MODES:
        raise RecordError("AD-BATCH-MODE", f"mode must be one of {', '.join(MODES)}", 422)
    if key is not None and mode != "upsert":
        raise RecordError("AD-BATCH-KEY", "key applies only to upsert", 422)
    if mode == "insert":
        return None
    rules = _unique_rules(c)
    if not rules:
        raise RecordError("AD-BATCH-NO-UNIQUE", f"{mode} matches records on a unique rule, "
                          f"and {c.collection} declares none", 422)
    if mode == "skip_existing":
        return None
    if key is None:
        if len(rules) > 1:
            raise RecordError("AD-BATCH-KEY", f"{c.collection} has several unique rules; "
                              "name the one to upsert on with key", 422, {"unique": rules})
        return rules[0]
    for fields in rules:
        if isinstance(key, list) and set(key) == set(fields) and len(key) == len(fields):
            return fields
    raise RecordError("AD-BATCH-KEY", "key must name the fields of one of "
                      f"{c.collection}'s unique rules", 422, {"unique": rules})


def _check_shape(ctx: AppContext, c: CollectionDef, values: dict) -> None:
    """Fields and values, without access: what a record can be checked for
    before the mode decides whether it's a create or an update."""
    for name, value in values.items():
        if name in SYSTEM_FIELDS:
            raise RecordError("AD-SYSTEM-FIELD", f"{name} is a system field; the platform "
                              "sets it", 422, {"field": name})
        if name not in c.fields:
            raise RecordError("AD-UNKNOWN-FIELD", f"{c.collection} has no field {name}", 422,
                              {"field": name})
        normalize_value(ctx, c, name, value)


# --- applying one record ---------------------------------------------------------------------

async def _find(session, ctx: AppContext, c: CollectionDef, fields: list[str],
                values: dict) -> AppDataRecord | None:
    """The record matching `values` on a unique rule's fields, compared as
    `_check_unique` compares them (a missing value matches a missing one)."""
    conds = [scope(ctx, c.collection)]
    for name in fields:
        expr, conv = field_expr(c, name)
        value = normalize_value(ctx, c, name, values.get(name))
        conds.append(expr.is_(None) if value is None else expr == conv(value))
    return (await session.execute(select(R).where(*conds).limit(1))).scalars().first()


async def _replace(session, ctx: AppContext, caller: Caller, c: CollectionDef,
                   record: AppDataRecord, values: dict) -> bool:
    """An immutable upsert: replace the record whole when its values differ.
    No history row, by design."""
    access = ctx.access(c, caller)
    access.require_verb("update")
    # A replace writes a whole record, so every given field needs create; and
    # it overwrites one, so every changed field needs update too.
    _check_input(c, access, values, "create")
    doc = {name: normalize_value(ctx, c, name, value) for name, value in values.items()}
    doc = {name: value for name, value in doc.items() if value is not None}
    missing = [name for name, spec in c.fields.items() if spec.required and name not in doc]
    if missing:
        raise RecordError("AD-REQUIRED", f"required fields missing: {', '.join(missing)}",
                          422, {"fields": missing})
    changed = {name: doc.get(name) for name in {*doc, *record.doc}
               if doc.get(name) != record.doc.get(name)}
    if not changed:
        return False
    _check_input(c, access, changed, "update")
    _check_writer_rules(ctx, c, caller, changed)
    await _check_refs(session, ctx, c, changed)
    sides = side_columns(c, doc)
    await _check_unique(session, ctx, c, doc, record.id)
    # As _update does: the new artifacts first, then release the old ones.
    moved = [name for name in app_artifacts.artifact_fields(c) if name in changed]
    await app_artifacts.attach(session, ctx, caller, c, record.id,
                               {name: changed[name] for name in moved})
    await app_artifacts.detach_fields(session, ctx, c.collection, record.id,
                                      {name: record.doc[name] for name in moved
                                       if record.doc.get(name) is not None})
    record.doc = doc
    set_size(session, ctx, record, writes=1)
    for column, value in sides.items():
        setattr(record, column, value)
    record.current_version += 1
    record.updated_at = utcnow()
    record.author, record.via = caller.author, caller.via
    record.collection_version = ctx.versions.get(c.collection, record.collection_version)
    await session.flush()
    return True


async def _apply(session, ctx: AppContext, caller: Caller, c: CollectionDef, values: dict,
                 mode: str, key: list[str] | None) -> tuple[str, AppDataRecord]:
    """(outcome, record) for one record: inserted, updated, unchanged or skipped."""
    if mode == "insert":
        return "inserted", await _insert(session, ctx, caller, c, values)
    _check_shape(ctx, c, values)
    if mode == "skip_existing":
        _check_input(c, ctx.access(c, caller), values, "create")
    existing = None
    for fields in ([key] if mode == "upsert" else _unique_rules(c)):
        existing = await _find(session, ctx, c, fields, values)
        if existing is not None:
            break
    if existing is None:
        return "inserted", await _insert(session, ctx, caller, c, values)
    if mode == "skip_existing":
        return "skipped", existing
    if c.write_mode == "immutable":
        changed = await _replace(session, ctx, caller, c, existing, values)
    else:
        before = existing.current_version
        existing = await _update(session, ctx, caller, c, existing.id, values, before)
        changed = existing.current_version != before
    return ("updated" if changed else "unchanged"), existing


@dataclass
class _Run:
    """What a batch or a commit did, record by record."""
    keep_ids: bool = True
    counts: dict = dc_field(default_factory=lambda: {
        "inserted": 0, "updated": 0, "unchanged": 0, "skipped": 0})
    ids: list = dc_field(default_factory=list)
    errors: list = dc_field(default_factory=list)
    error_count: int = 0
    touched: set = dc_field(default_factory=set)

    def fail(self, index: int, exc: RecordError, collection: str | None = None) -> None:
        self.error_count += 1
        if len(self.errors) < MAX_REPORTED_ERRORS:
            entry = {"index": index}
            if collection is not None:
                entry["collection"] = collection
            self.errors.append({**entry, **exc.as_dict()})

    async def apply(self, index: int, session, ctx: AppContext, caller: Caller,
                    c: CollectionDef, values: dict, mode: str, key, *,
                    collection: str | None = None) -> None:
        try:
            outcome, record = await _apply(session, ctx, caller, c, values, mode, key)
        except RecordError as exc:
            self.fail(index, exc, collection)
            if self.keep_ids:
                self.ids.append(None)
            return
        self.counts[outcome] += 1
        if outcome in ("inserted", "updated"):
            self.touched.add(c.collection)
        if self.keep_ids:
            self.ids.append(record.id)
        # Flushed already; dropping it keeps a 140k-record commit's identity
        # map from holding every row it wrote.
        session.expunge(record)

    def rejection(self) -> RecordError:
        return RecordError("AD-BATCH-REJECTED", f"{self.error_count} record(s) refused; "
                           "nothing was written", 422,
                           {"errors": self.errors, "error_count": self.error_count})

    def result(self) -> dict:
        out = {**self.counts, "errors": self.errors, "error_count": self.error_count}
        if self.keep_ids:
            out["ids"] = self.ids
        return out


# --- batch -------------------------------------------------------------------------------------

async def batch(session, ctx: AppContext, caller: Caller, collection: str, records: list,
                *, mode: str = "insert", key: list[str] | None = None,
                on_error: str = "fail") -> dict:
    """Write up to 5,000 records in one transaction. Returns the counts per
    outcome, `ids` aligned with `records` (None where a record failed), and
    the errors; refuses the whole call when any record fails, unless
    `on_error` is `skip`."""
    c = ctx.collection(collection)
    if on_error not in ON_ERROR:
        raise RecordError("AD-BATCH-MODE", f"on_error must be one of {', '.join(ON_ERROR)}",
                          422)
    key_fields = _key_fields(c, mode, key)
    size = _check_payload(records)
    run = _Run()
    try:
        _require_active(ctx)
        ctx.access(c, caller).require_verb("create")
        await check_quotas(session, ctx, records=len(records), bytes=size)
        async with write_lock(session, ctx, [c.collection]):
            for i, values in enumerate(records):
                await run.apply(i, session, ctx, caller, c, values, mode, key_fields)
            if run.error_count and on_error == "fail":
                raise run.rejection()
            if run.touched:
                await bump_counters(session, ctx.app_id, run.touched)
            await quotas.settle(session)
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return run.result()


# --- batch jobs ---------------------------------------------------------------------------------

def _aware(dt: datetime) -> datetime:
    # SQLite hands timestamps back naive; they were written in UTC.
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _set_summary(st: AppDataStagingSet) -> dict:
    return {"set_id": st.id, "state": st.state, "collection_versions": st.collection_versions,
            "record_count": st.record_count, "bytes": st.bytes,
            "expires_at": format_datetime(_aware(st.expires_at))}


async def open_staging_set(session, ctx: AppContext, caller: Caller,
                           collections: list[str] | dict[str, int], *,
                           call_id: str | None = None, run_id: str | None = None,
                           now: datetime | None = None) -> dict:
    """Open a set for these collections. A dict pins the versions the caller
    built its records against, refused at once if one has already moved; a
    list takes the published versions."""
    now = now or utcnow()
    pinned = dict(collections) if isinstance(collections, dict) else dict.fromkeys(
        collections or ())
    if not pinned:
        raise RecordError("AD-INVALID-VALUE", "name at least one collection", 422)
    try:
        _require_active(ctx)
        versions: dict[str, int] = {}
        moved: dict[str, dict] = {}
        for name, want in pinned.items():
            c = ctx.collection(name)
            ctx.access(c, caller).require_verb("create")
            versions[name] = ctx.versions.get(name, 1)
            if want is not None and want != versions[name]:
                moved[name] = {"requested": want, "published": versions[name]}
        if moved:
            raise RecordError("AD-STAGING-SCHEMA-CHANGED", "a collection's published "
                              "version has moved", 409, {"changed": moved})
        st = SS(id=uuid.uuid4().hex, app_id=ctx.app_id, creator=caller.principal,
                tool=caller.via_tool, call_id=call_id, run_id=run_id,
                collection_versions=versions, state="open", record_count=0, bytes=0,
                created_at=now, expires_at=now + STAGING_TTL)
        session.add(st)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return _set_summary(st)


async def _load_set(session, set_id: str, caller: Caller, call_id: str | None) -> SS:
    """The open set, locked, if this caller is its creator: the same
    principal, through the same tool, in the same call."""
    st = (await session.execute(select(SS).where(SS.id == set_id).with_for_update())
          ).scalar_one_or_none()
    if st is None:
        raise RecordError("AD-STAGING-NOT-FOUND", f"no staging set {set_id}", 404)
    if (st.creator != caller.principal or st.tool != caller.via_tool
            or (st.call_id is not None and st.call_id != call_id)):
        raise RecordError("AD-STAGING-CREATOR", "a staging set is used only by the "
                          "principal, tool and call that opened it", 403)
    if st.state != "open":
        raise RecordError("AD-STAGING-CLOSED", f"staging set {set_id} is {st.state}", 409,
                          {"state": st.state})
    return st


async def _drop_staged(session, set_id: str) -> int:
    return (await session.execute(delete(S).where(S.set_id == set_id))).rowcount


async def _expire_if_due(session, st: SS, now: datetime) -> None:
    if _aware(st.expires_at) > now:
        return
    st.state = "expired"
    await _drop_staged(session, st.id)
    await session.commit()
    raise RecordError("AD-STAGING-EXPIRED", f"staging set {st.id} expired "
                      f"{STAGING_TTL.total_seconds() / 3600:.0f}h after it opened", 410)


def _moved(ctx: AppContext, pinned: dict[str, int]) -> dict[str, dict]:
    return {name: {"opened": version, "published": ctx.versions.get(name)}
            for name, version in pinned.items() if ctx.versions.get(name) != version}


async def stage(session, ctx: AppContext, caller: Caller, set_id: str, collection: str,
                records: list, *, mode: str = "insert", key: list[str] | None = None,
                call_id: str | None = None, now: datetime | None = None) -> dict:
    """Add a batch to an open set. The records are checked for shape (fields
    and values) under the per-call limits, and refused all together with
    per-record errors; access, rules, refs and `unique` are checked at commit,
    against the state the records will actually join."""
    now = now or utcnow()
    try:
        st = await _load_set(session, set_id, caller, call_id)
        if st.app_id != ctx.app_id:
            raise RecordError("AD-STAGING-NOT-FOUND", f"no staging set {set_id}", 404)
        await _expire_if_due(session, st, now)
        if collection not in st.collection_versions:
            raise RecordError("AD-STAGING-COLLECTION", f"staging set {set_id} wasn't opened "
                              f"for {collection}", 422,
                              {"collections": sorted(st.collection_versions)})
        moved = _moved(ctx, {collection: st.collection_versions[collection]})
        if moved:
            raise RecordError("AD-STAGING-SCHEMA-CHANGED", "a collection's published "
                              "version has moved; this set can't commit", 409,
                              {"changed": moved})
        c = ctx.collection(collection)
        key_fields = _key_fields(c, mode, key)
        size = _check_payload(records)
        run = _Run(keep_ids=False)
        for i, values in enumerate(records):
            try:
                _check_shape(ctx, c, values)
            except RecordError as exc:
                run.fail(i, exc)
        if run.error_count:
            raise run.rejection()
        base = st.record_count
        if records:
            await session.execute(insert(S), [
                {"set_id": st.id, "seq": base + i, "collection": collection,
                 "record_id": None, "mode": mode, "key": key_fields, "doc": values}
                for i, values in enumerate(records)])
        st.record_count = base + len(records)
        st.bytes = st.bytes + size
        summary = _set_summary(st)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return {**summary, "staged": len(records)}


async def commit_staging_set(session, caller: Caller, set_id: str, *,
                             call_id: str | None = None, now: datetime | None = None) -> dict:
    """Re-validate and publish a set in one transaction, or refuse and leave it
    open. Errors name the record by its `index` in arrival order across the
    set, and its collection."""
    now = now or utcnow()
    run = _Run(keep_ids=False)
    try:
        st = await _load_set(session, set_id, caller, call_id)
        await _expire_if_due(session, st, now)
        # The App as it is now, not as it was when the set opened.
        ctx = await load_app(session, st.app_id)
        _require_active(ctx)
        moved = _moved(ctx, st.collection_versions)
        if moved:
            raise RecordError("AD-STAGING-SCHEMA-CHANGED", "a collection's published "
                              "version has moved since the set opened", 409,
                              {"changed": moved})
        cols = {name: ctx.collection(name) for name in st.collection_versions}
        # Creator authority, re-checked: an owner transfer or a revoked grant
        # changes who `owner` is without moving any collection version.
        for c in cols.values():
            ctx.access(c, caller).require_verb("create")
        await check_quotas(session, ctx, records=st.record_count, bytes=st.bytes)
        async with write_lock(session, ctx, cols):
            last = -1
            while True:
                rows = (await session.execute(
                    select(S.seq, S.collection, S.mode, S.key, S.doc)
                    .where(S.set_id == st.id, S.seq > last).order_by(S.seq)
                    .limit(_REPLAY_CHUNK))).all()
                if not rows:
                    break
                for seq, collection, mode, key, doc in rows:
                    await run.apply(seq, session, ctx, caller, cols[collection], doc, mode,
                                    key, collection=collection)
                last = rows[-1].seq
            if run.error_count:
                raise run.rejection()
            if run.touched:
                await bump_counters(session, ctx.app_id, run.touched)
            await _drop_staged(session, st.id)
            st.state = "committed"
            st.committed_at = utcnow()
            await quotas.settle(session)
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return {"set_id": set_id, "state": "committed", **run.result()}


async def abandon_staging_set(session, caller: Caller, set_id: str, *,
                              call_id: str | None = None) -> dict:
    try:
        st = await _load_set(session, set_id, caller, call_id)
        dropped = await _drop_staged(session, st.id)
        st.state = "aborted"
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return {"set_id": set_id, "state": "aborted", "dropped": dropped}


async def prune_staging_sets(session, *, now: datetime | None = None) -> int:
    """Expire every open set past its 24 hours and drop its staged records.
    The set rows stay, as the record of what was attempted. Returns how many
    expired."""
    now = now or utcnow()
    try:
        ids = list((await session.execute(
            select(SS.id).where(SS.state == "open", SS.expires_at <= now))).scalars())
        for i in range(0, len(ids), _IN_CHUNK):
            chunk = ids[i:i + _IN_CHUNK]
            await session.execute(update(SS).where(SS.id.in_(chunk), SS.state == "open")
                                  .values(state="expired"))
            await session.execute(delete(S).where(S.set_id.in_(chunk)))
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return len(ids)
