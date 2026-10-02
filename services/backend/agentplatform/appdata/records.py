"""The records engine (design 39, "Collections", "Rules", "Storage").

Creates, reads, updates and deletes the records of a published collection,
for one caller, under the collection's access, rules and refs. It works on the
platform's async SQLAlchemy session and runs the same on SQLite (tests) and
Postgres.

- **System fields** (`id`, `created_at`, `updated_at`, `author`, `via`,
  `version`, `collection_version`) are stamped here and never taken from
  input: a write naming one is refused rather than quietly ignored, so a
  caller can't believe it set them.
- **Values** are checked against the field's type and bounds and stored
  normalized in `doc`: a datetime as fixed-width UTC (`…T…:…:…….ffffffZ`) so
  that comparing the stored strings orders them in time on both databases,
  and an unset field is absent rather than `null`.
- **Indexed fields** are copied into the typed side columns the fixed indexes
  cover. A text value over 256 characters can't be indexed and is refused.
- **Reads** return every field; a field the caller can't read is `null` and
  named in the row's `restricted` list (the web contract in
  `services/web/src/lib/appData.ts`).
- **Deletes** run a server-computed plan in one transaction: `restrict` refs
  block it, `unlink` refs are cleared.
- **Quotas** (appdata/quotas.py): each write notes what it adds or frees,
  and the public writes settle the charge just before their commit.
- **Every write holds its collections' locks** (`write_lock`): a
  transaction-scoped advisory lock on Postgres, a process lock on SQLite
  (which serializes writers anyway). A write to a collection with `unique`
  rules takes it exclusive, so the check and the insert are one step; any
  other write takes it shared, so plain writes still run side by side. A
  publish takes it exclusive for every collection it changes, so its check
  over the stored records can't race a write; a write that waited behind a
  publish re-reads the definitions it was checked against and is refused if
  they moved.
- **Artifact fields** make the artifact App-owned on write and delete it with
  its last reference (`appdata/artifacts.py`), in the write's transaction.

Transactions: like `ticket_store`, every public write makes exactly one
commit, at the end, and rolls back on refusal. The underscore helpers don't
commit, so the batch writer can run many of them in one transaction.
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
import weakref
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, time, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select

from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.appdata import quotas
from agentplatform.appdata.access import Access, Caller, RecordError, matches
from agentplatform.appdata.definitions import (
    SYSTEM_FIELDS, AppBundle, CollectionDef, DefinitionError, RefField, UniqueRule,
    WriterRule, ImmutableAfterCreateRule, _fits_field, index_columns, validate_app)
from agentplatform.appdata.models import (AppDataApp, AppDataDefinition, AppDataRecord,
                                          AppDataRecordVersion, AppDataWriteCounter)
from agentplatform.db import utcnow

# The side columns are String(256); a longer value can't be indexed.
MAX_INDEXED_TEXT = 256
# IN lists stay well under every driver's bound-parameter limit.
_CHUNK = 500

# --- the App a call works on -------------------------------------------------------


@dataclass
class AppContext:
    """An App's published definitions plus what checks need of the App row."""
    app_id: str
    owner: str                      # kyle, or agent:<name>
    bundle: AppBundle
    timezone: str = "UTC"
    status: str = "active"
    name: str = ""
    # collection -> the published definition version records are stamped with.
    versions: dict[str, int] = dc_field(default_factory=dict)
    # The App's approved version these definitions were read at.
    approved_version: int | None = None

    def collection(self, name: str) -> CollectionDef:
        c = self.bundle.collections.get(name)
        if c is None:
            raise RecordError("AD-NO-COLLECTION", f"no published collection {name}", 404)
        return c

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    def access(self, collection: CollectionDef, caller: Caller) -> Access:
        return Access(collection, caller, self.owner)

    def refs_to(self, target: str) -> list[tuple[CollectionDef, str, RefField]]:
        """Every (collection, field) in the App that refers to `target`."""
        return [(c, name, spec) for c in self.bundle.collections.values()
                for name, spec in c.fields.items()
                if isinstance(spec, RefField) and spec.collection == target]


def owner_principal(app: AppDataApp) -> str:
    return "kyle" if app.owner_kind == "kyle" else f"agent:{app.owner_id}"


def state_at(rows, version: int | None = None) -> dict[tuple[str, str], AppDataDefinition]:
    """(kind, name) -> the published row in force at `version` (all of them
    when None): the newest at or below it, unless that one is a removal."""
    latest: dict[tuple[str, str], AppDataDefinition] = {}
    for row in rows:
        if row.state != "published" or (version is not None and row.version > version):
            continue
        key = (row.kind, row.name)
        if key not in latest or row.version > latest[key].version:
            latest[key] = row
    return {key: row for key, row in latest.items() if not row.removed}


async def published_rows(session, app_id: str) -> list[AppDataDefinition]:
    return list((await session.execute(
        select(AppDataDefinition).where(AppDataDefinition.app_id == app_id,
                                        AppDataDefinition.state == "published")
    )).scalars().all())


async def load_app(session, app_id: str) -> AppContext:
    """The App as of its approved version: the newest published version of
    each definition at or below it, removals dropped."""
    app = await session.get(AppDataApp, app_id)
    if app is None:
        raise RecordError("AD-NO-APP", f"no App {app_id}", 404)
    latest = state_at(await published_rows(session, app_id), app.approved_version)
    bundle = {"collections": [], "views": [], "pages": []}
    versions: dict[str, int] = {}
    for (kind, name), row in sorted(latest.items()):
        bundle[kind + "s"].append(row.body)
        if kind == "collection":
            versions[name] = row.version
    try:
        validated = validate_app(bundle)
    except DefinitionError as exc:
        # The same refusal a broken page answers with: the App's published
        # state no longer validates, so nothing reads or writes through it.
        raise RecordError("AD-DEFINITION-INVALID", "the App's published definitions no "
                          "longer validate", 503, exc.as_dict()["errors"]) from exc
    return AppContext(app_id=app.id, owner=owner_principal(app), bundle=validated,
                      timezone=app.timezone or "UTC", status=app.status, name=app.name,
                      versions=versions, approved_version=app.approved_version)


# --- values ---------------------------------------------------------------------------

def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


def format_datetime(dt: datetime) -> str:
    """Fixed-width UTC, so string order is time order."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return _utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_datetime(value: str, tz: ZoneInfo) -> datetime:
    """An aware datetime; a value without an offset is read in App time."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt


def _date_instant(value: str) -> datetime:
    # A date's side-column encoding: midnight UTC. Only ever compared with
    # other dates encoded the same way.
    return datetime.combine(date.fromisoformat(value), time(), tzinfo=timezone.utc)


def normalize_value(ctx: AppContext, c: CollectionDef, name: str, value: Any) -> Any:
    """The value as stored, or a refusal naming the field."""
    if value is None:
        return None
    spec = c.fields[name]
    if not _fits_field(c, name, value, bounds=True):
        raise RecordError("AD-INVALID-VALUE", f"{name}: value does not fit the "
                          f"{spec.type} field", 422, {"field": name})
    if spec.type == "datetime":
        return format_datetime(parse_datetime(value, ctx.tz))
    if spec.type == "number" and isinstance(value, int):
        return float(value)
    return value


def side_columns(c: CollectionDef, doc: dict) -> dict:
    out = {"ix_text1": None, "ix_text2": None, "ix_num1": None, "ix_time1": None}
    for name, column in index_columns(c).items():
        value = doc.get(name)
        if value is None:
            continue
        if column.startswith("ix_text"):
            if len(value) > MAX_INDEXED_TEXT:
                raise RecordError("AD-INDEX-TOO-LONG", f"{name} is indexed, so it holds at "
                                  f"most {MAX_INDEXED_TEXT} characters", 422,
                                  {"field": name})
            out[column] = value
        elif column == "ix_num1":
            out[column] = float(value)
        else:
            out[column] = side_value(c, name, value)
    return out


def side_value(c: CollectionDef, name: str, value: Any) -> Any:
    """A normalized doc value as its side column stores it."""
    ftype = c.fields[name].type
    if ftype == "date":
        return _date_instant(value)
    if ftype == "datetime":
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    if ftype in ("int", "number"):
        return float(value)
    return value


# --- SQL over fields ------------------------------------------------------------------

R = AppDataRecord
_SYSTEM_COLUMNS = {"id": R.id, "created_at": R.created_at, "updated_at": R.updated_at,
                   "author": R.author, "via": R.via, "version": R.current_version,
                   "collection_version": R.collection_version}


def field_type(c: CollectionDef, name: str) -> str:
    spec = c.fields.get(name)
    return spec.type if spec is not None else SYSTEM_FIELDS[name]


def field_expr(c: CollectionDef, name: str):
    """(SQL expression, converter from a normalized value to its bind value).

    An indexed field reads its side column, so the fixed indexes serve it;
    anything else is extracted from `doc` portably (JSON_EXTRACT on SQLite,
    `->>` on Postgres)."""
    if name in _SYSTEM_COLUMNS:
        if name in ("created_at", "updated_at"):
            return _SYSTEM_COLUMNS[name], lambda v: _utc(
                datetime.fromisoformat(v.replace("Z", "+00:00")))
        return _SYSTEM_COLUMNS[name], lambda v: v
    column = index_columns(c).get(name)
    if column is not None:
        return getattr(R, column), lambda v: side_value(c, name, v)
    ftype = c.fields[name].type
    if ftype in ("int", "number"):
        return R.doc[name].as_float(), float
    if ftype == "bool":
        return R.doc[name].as_boolean(), bool
    return R.doc[name].as_string(), lambda v: v


def scope(ctx: AppContext, collection: str):
    return and_(R.app_id == ctx.app_id, R.collection == collection)


# --- rows as the caller sees them -------------------------------------------------------

def _system_values(record: AppDataRecord) -> dict:
    return {"id": record.id, "created_at": format_datetime(record.created_at),
            "updated_at": format_datetime(record.updated_at), "author": record.author,
            "via": record.via, "version": record.current_version,
            "collection_version": record.collection_version}


def present(access: Access, record: AppDataRecord, fields: list[str] | None = None) -> dict:
    """`{id, values, restricted}`: unreadable fields are null and named."""
    c = access.collection
    names = fields if fields is not None else [*c.fields, *SYSTEM_FIELDS]
    system = _system_values(record)
    values: dict[str, Any] = {}
    restricted: list[str] = []
    for name in names:
        if not access.can_read(name):
            values[name] = None
            restricted.append(name)
        elif name in system:
            values[name] = system[name]
        else:
            values[name] = record.doc.get(name)
    return {"id": record.id, "values": values, "restricted": restricted}


# --- locks and counters -----------------------------------------------------------------

_process_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict]" = (
    weakref.WeakKeyDictionary())


def lock_key(app_id: str, collection: str) -> int:
    """A stable signed 64-bit key for pg_advisory_xact_lock."""
    digest = hashlib.blake2b(f"app_data:{app_id}:{collection}".encode(),
                             digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


def dialect(session) -> str:
    return session.get_bind().dialect.name


@asynccontextmanager
async def collection_lock(session, app_id: str, collection: str, *, shared: bool = False):
    """One collection's lock. On Postgres the lock is the transaction's and is
    released by its commit or rollback; `shared` holders exclude only an
    exclusive one. On SQLite a process lock is held across the block (shared
    or not), so the caller commits inside it."""
    if dialect(session) == "postgresql":
        fn = func.pg_advisory_xact_lock_shared if shared else func.pg_advisory_xact_lock
        await session.execute(select(fn(lock_key(app_id, collection))))
        yield
        return
    locks = _process_locks.setdefault(asyncio.get_running_loop(), {})
    lock = locks.setdefault((app_id, collection), asyncio.Lock())
    async with lock:
        yield


@asynccontextmanager
async def locked_collections(session, app_id: str, exclusive=(), shared=()):
    """Take several collections' locks in one sorted order, so two holders of
    overlapping sets can't deadlock. A name in both sets is taken exclusive."""
    exclusive = set(exclusive)
    async with AsyncExitStack() as stack:
        for name in sorted(exclusive | set(shared)):
            await stack.enter_async_context(collection_lock(
                session, app_id, name, shared=name not in exclusive))
        yield


@asynccontextmanager
async def write_lock(session, ctx: AppContext, collections, *, delete_target: str | None = None):
    """Hold the locks a record write needs: exclusive on a collection with
    `unique` rules, shared otherwise. Once they're held no publish can be
    mid-check on these collections; one that committed while this write
    waited may have changed the definitions `ctx` holds, so the write is
    refused rather than checked against stale ones."""
    names = set(collections)
    exclusive = {n for n in names
                 if n in ctx.bundle.collections and _has_unique(ctx.bundle.collections[n])}
    async with locked_collections(session, ctx.app_id, exclusive, names - exclusive):
        await _require_current(session, ctx, names, delete_target=delete_target)
        yield


async def _require_current(session, ctx: AppContext, names: set[str], *,
                           delete_target: str | None = None) -> None:
    approved = (await session.execute(select(AppDataApp.approved_version).where(
        AppDataApp.id == ctx.app_id))).scalar_one_or_none()
    if approved == ctx.approved_version:
        return
    # A publish that left these collections alone (a view, a page, another
    # collection) doesn't make this write stale.
    rows = await published_rows(session, ctx.app_id)
    now = state_at(rows, approved)
    if delete_target is not None:
        # Publishing a new incoming ref also locks its target. A delete
        # waiting there must notice refs absent from its original context,
        # even when the target collection's own definition hasn't changed.
        names = names | {name for (kind, name), row in now.items()
                         if kind == "collection" and any(
                             spec.get("type") == "ref"
                             and spec.get("collection") == delete_target
                             for spec in row.body.get("fields", {}).values())}
    moved = sorted(n for n in names
                   if (now[("collection", n)].version if ("collection", n) in now else None)
                   != ctx.versions.get(n))
    if moved:
        raise RecordError("AD-DEFINITIONS-MOVED", "the App published new definitions "
                          "for this write's collections while it waited; read them "
                          "and retry", 409, {"collections": moved,
                                             "approved_version": approved})


async def bump_counters(session, app_id: str, collections) -> None:
    """One committed write retires every cached view result over these
    collections (design 39, "Tool views" → cache key)."""
    if dialect(session) == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    t = AppDataWriteCounter.__table__
    now = utcnow()
    for collection in sorted(set(collections)):
        stmt = insert(t).values(app_id=app_id, collection=collection, counter=1,
                                updated_at=now)
        await session.execute(stmt.on_conflict_do_update(
            index_elements=[t.c.app_id, t.c.collection],
            set_={"counter": t.c.counter + 1, "updated_at": now}))


async def write_counter(session, app_id: str, collection: str) -> int:
    row = await session.get(AppDataWriteCounter, (app_id, collection))
    return row.counter if row is not None else 0


# --- checks ------------------------------------------------------------------------------

def _require_active(ctx: AppContext) -> None:
    if ctx.status != "active":
        raise RecordError("AD-APP-RETIRED", f"App {ctx.name or ctx.app_id} is retired; "
                          "its records are read-only", 409)


def _check_input(c: CollectionDef, access: Access, values: Any, verb: str) -> None:
    if not isinstance(values, dict):
        raise RecordError("AD-INVALID-VALUE", "values must be an object", 422)
    for name in values:
        if name in SYSTEM_FIELDS:
            raise RecordError("AD-SYSTEM-FIELD", f"{name} is a system field; the platform "
                              "sets it", 422, {"field": name})
        if name not in c.fields:
            raise RecordError("AD-UNKNOWN-FIELD", f"{c.collection} has no field {name}", 422,
                              {"field": name})
        if not access.can_write(name, verb):
            raise RecordError("AD-FIELD-FORBIDDEN", f"{access.caller.principal} may not "
                              f"{verb} {name}", 403, {"field": name})


def _check_writer_rules(ctx: AppContext, c: CollectionDef, caller: Caller,
                        changed: dict[str, Any]) -> None:
    """`writer`: only the rule's writers may set the field (or that value)."""
    for rule in c.rules:
        if not isinstance(rule, WriterRule) or rule.field not in changed:
            continue
        if "value" in rule.model_fields_set and changed[rule.field] != rule.value:
            continue
        if not matches(caller, rule.writers, ctx.owner):
            raise RecordError("AD-RULE-WRITER", f"only {', '.join(rule.writers)} may set "
                              f"{rule.field}" + (f" to {rule.value!r}"
                                                 if "value" in rule.model_fields_set else ""),
                              403, {"field": rule.field})


async def _check_refs(session, ctx: AppContext, c: CollectionDef, changed: dict) -> None:
    for name, value in changed.items():
        spec = c.fields[name]
        if not isinstance(spec, RefField) or value is None:
            continue
        # FOR SHARE on Postgres: a concurrent delete of the target waits for
        # this transaction, then finds the reference its plan must honour.
        found = (await session.execute(
            select(R.id).where(scope(ctx, spec.collection), R.id == value)
            .with_for_update(read=True))).scalar_one_or_none()
        if found is None:
            raise RecordError("AD-REF-MISSING", f"{name}: no {spec.collection} record "
                              f"{value}", 422, {"field": name})


async def _check_unique(session, ctx: AppContext, c: CollectionDef, doc: dict,
                        record_id: str) -> None:
    """Nulls compare equal here: two records both missing a unique field
    collide, which is the stricter reading of "unique"."""
    for rule in c.rules:
        if not isinstance(rule, UniqueRule):
            continue
        conds = [scope(ctx, c.collection), R.id != record_id]
        for name in rule.fields:
            expr, conv = field_expr(c, name)
            value = doc.get(name)
            conds.append(expr.is_(None) if value is None else expr == conv(value))
        clash = (await session.execute(select(R.id).where(*conds).limit(1))).first()
        if clash is not None:
            raise RecordError("AD-UNIQUE", f"another {c.collection} record has the same "
                              f"{', '.join(rule.fields)}", 409, {"fields": rule.fields})


def _has_unique(c: CollectionDef) -> bool:
    return any(isinstance(rule, UniqueRule) for rule in c.rules)


# --- create ---------------------------------------------------------------------------------

async def _insert(session, ctx: AppContext, caller: Caller, c: CollectionDef,
                  values: dict, record_id: str | None = None) -> AppDataRecord:
    """Validate and add one record. The caller holds `write_lock` on the
    collection, and commits."""
    _require_active(ctx)
    access = ctx.access(c, caller)
    access.require_verb("create")
    _check_input(c, access, values, "create")
    doc = {name: normalize_value(ctx, c, name, value) for name, value in values.items()}
    doc = {name: value for name, value in doc.items() if value is not None}
    missing = [name for name, spec in c.fields.items() if spec.required and name not in doc]
    if missing:
        raise RecordError("AD-REQUIRED", f"required fields missing: {', '.join(missing)}",
                          422, {"fields": missing})
    _check_writer_rules(ctx, c, caller, doc)
    await _check_refs(session, ctx, c, doc)
    sides = side_columns(c, doc)
    record_id = record_id or uuid.uuid4().hex
    await _check_unique(session, ctx, c, doc, record_id)
    await app_artifacts.attach(session, ctx, caller, c, record_id, doc)
    now = utcnow()
    size = quotas.doc_bytes(doc)
    record = AppDataRecord(app_id=ctx.app_id, collection=c.collection, id=record_id,
                           current_version=1, created_at=now, updated_at=now,
                           author=caller.author, via=caller.via,
                           collection_version=ctx.versions.get(c.collection, 1),
                           doc=doc, size_bytes=size, **sides)
    session.add(record)
    await session.flush()
    quotas.note(session, ctx, records=1, bytes=size, writes=1)
    return record


async def create_record(session, ctx: AppContext, caller: Caller, collection: str,
                        values: dict) -> dict:
    c = ctx.collection(collection)
    try:
        async with write_lock(session, ctx, [c.collection]):
            record = await _insert(session, ctx, caller, c, values)
            await bump_counters(session, ctx.app_id, [c.collection])
            row = present(ctx.access(c, caller), record)
            await quotas.settle(session)
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return row


def delete_lock(session, ctx: AppContext, collection: str):
    """A delete's locks: the target collection and every collection whose
    refs to it the plan may unlink."""
    return write_lock(session, ctx, {collection} | {c.collection
                                                    for c, _, _ in ctx.refs_to(collection)},
                      delete_target=collection)


# --- read -------------------------------------------------------------------------------------

async def get_record(session, ctx: AppContext, caller: Caller, collection: str,
                     record_id: str) -> dict:
    c = ctx.collection(collection)
    access = ctx.access(c, caller)
    access.require_rows()
    record = await _fetch(session, ctx, c, record_id)
    return present(access, record)


async def _fetch(session, ctx: AppContext, c: CollectionDef, record_id: str, *,
                 lock: bool = False) -> AppDataRecord:
    stmt = select(R).where(scope(ctx, c.collection), R.id == record_id)
    if lock:
        stmt = stmt.with_for_update()
    record = (await session.execute(stmt)).scalar_one_or_none()
    if record is None:
        raise RecordError("AD-NOT-FOUND", f"no {c.collection} record {record_id}", 404)
    return record


# --- update -----------------------------------------------------------------------------------

def set_size(session, ctx: AppContext, record: AppDataRecord, *, writes: int = 0) -> None:
    """Re-measure a record whose doc changed and note the difference."""
    size = quotas.doc_bytes(record.doc)
    quotas.note(session, ctx, bytes=size - (record.size_bytes or 0), writes=writes)
    record.size_bytes = size


def _history(record: AppDataRecord) -> AppDataRecordVersion:
    return AppDataRecordVersion(
        app_id=record.app_id, collection=record.collection, record_id=record.id,
        version=record.current_version, author=record.author, via=record.via,
        collection_version=record.collection_version, updated_at=record.updated_at,
        doc=dict(record.doc))


async def _update(session, ctx: AppContext, caller: Caller, c: CollectionDef,
                  record_id: str, values: dict, expected_version: int) -> AppDataRecord:
    _require_active(ctx)
    if c.write_mode == "immutable":
        raise RecordError("AD-IMMUTABLE", f"{c.collection} is immutable: records are "
                          "written once", 409)
    access = ctx.access(c, caller)
    access.require_verb("update")
    _check_input(c, access, values, "update")
    record = await _fetch(session, ctx, c, record_id, lock=True)
    if record.current_version != expected_version:
        raise RecordError("AD-VERSION-CONFLICT", f"{c.collection} record {record_id} is at "
                          f"version {record.current_version}, not {expected_version}", 409,
                          {"current_version": record.current_version})
    patch = {name: normalize_value(ctx, c, name, value) for name, value in values.items()}
    changed = {name: value for name, value in patch.items()
               if record.doc.get(name) != value}
    if not changed:
        return record
    cleared = [name for name, value in changed.items()
               if value is None and c.fields[name].required]
    if cleared:
        raise RecordError("AD-REQUIRED", f"required fields can't be cleared: "
                          f"{', '.join(cleared)}", 422, {"fields": cleared})
    for rule in c.rules:
        if isinstance(rule, ImmutableAfterCreateRule):
            frozen = [name for name in rule.fields if name in changed]
            if frozen:
                raise RecordError("AD-RULE-IMMUTABLE-FIELD", f"{', '.join(frozen)} can't "
                                  "change after create", 409, {"fields": frozen})
    _check_writer_rules(ctx, c, caller, changed)
    await _check_refs(session, ctx, c, changed)
    doc = {**record.doc, **changed}
    doc = {name: value for name, value in doc.items() if value is not None}
    sides = side_columns(c, doc)
    await _check_unique(session, ctx, c, doc, record.id)
    moved = [name for name in app_artifacts.artifact_fields(c) if name in changed]
    # The new values first, so an artifact moving between two fields of this
    # record is never without a reference in between.
    await app_artifacts.attach(session, ctx, caller, c, record.id,
                               {name: changed[name] for name in moved})
    await app_artifacts.detach_fields(session, ctx, c.collection, record.id,
                                      {name: record.doc[name] for name in moved
                                       if record.doc.get(name) is not None})
    session.add(_history(record))
    record.doc = doc
    set_size(session, ctx, record, writes=1)
    for column, value in sides.items():
        setattr(record, column, value)
    record.current_version += 1
    record.updated_at = utcnow()
    record.author = caller.author
    record.via = caller.via
    record.collection_version = ctx.versions.get(c.collection, record.collection_version)
    await session.flush()
    return record


async def update_record(session, ctx: AppContext, caller: Caller, collection: str,
                        record_id: str, values: dict, *, expected_version: int) -> dict:
    """Update in place under `expected_version`; another write in between is
    a 409 with the current version, never a silent overwrite."""
    c = ctx.collection(collection)
    try:
        async with write_lock(session, ctx, [c.collection]):
            record = await _update(session, ctx, caller, c, record_id, values,
                                   expected_version)
            await bump_counters(session, ctx.app_id, [c.collection])
            row = present(ctx.access(c, caller), record)
            await quotas.settle(session)
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return row


# --- delete plans -------------------------------------------------------------------------------

@dataclass
class DeletePlan:
    """What deleting a set of records does, computed by the server.

    `blocked` lists the `restrict` refs that stop it: (collection, id,
    referencing collection, referencing id, field). The plan executes only
    when it's empty."""
    deletes: list[tuple[str, str]] = dc_field(default_factory=list)
    unlinks: list[tuple[str, str, str]] = dc_field(default_factory=list)
    blocked: list[tuple[str, str, str, str, str]] = dc_field(default_factory=list)

    def summary(self, ctx: AppContext, caller: Caller) -> dict:
        """The plan as the caller may see it: counts per collection, and
        record ids only from collections whose rows the caller can see."""
        def visible(collection: str) -> bool:
            return ctx.access(ctx.collection(collection), caller).can_see_rows()

        def group(items) -> dict:
            out: dict[str, dict] = {}
            for key, collection, rid in items:
                entry = out.setdefault(key, {"count": 0, "ids": []})
                entry["count"] += 1
                if visible(collection):
                    entry["ids"].append(rid)
            return out

        return {"deletes": group((c, c, rid) for c, rid in self.deletes),
                "unlinks": group((f"{c}.{f}", c, rid) for c, rid, f in self.unlinks),
                "blocked": group((f"{c}.{f}", c, rid) for _, _, c, rid, f in self.blocked),
                "ok": not self.blocked}


async def compute_plan(session, ctx: AppContext, targets: list[tuple[str, str]]) -> DeletePlan:
    """Follow every ref in the App that points at a target. A referencing
    record that is itself being deleted doesn't block or need unlinking."""
    plan = DeletePlan(deletes=list(dict.fromkeys(targets)))
    doomed = set(plan.deletes)
    by_collection: dict[str, list[str]] = {}
    for collection, rid in plan.deletes:
        by_collection.setdefault(collection, []).append(rid)
    for target, ids in by_collection.items():
        for c, name, spec in ctx.refs_to(target):
            expr, _ = field_expr(c, name)
            for i in range(0, len(ids), _CHUNK):
                chunk = ids[i:i + _CHUNK]
                rows = (await session.execute(
                    select(R.id, expr).where(scope(ctx, c.collection), expr.in_(chunk))
                    .order_by(R.id))).all()
                for ref_id, value in rows:
                    if (c.collection, ref_id) in doomed:
                        continue
                    if spec.on_delete == "restrict":
                        plan.blocked.append((target, value, c.collection, ref_id, name))
                    else:
                        plan.unlinks.append((c.collection, ref_id, name))
    return plan


async def _authorize_delete(session, ctx: AppContext, caller: Caller, collection: str,
                            ids: list[str], expected_versions: dict[str, int] | None) -> None:
    _require_active(ctx)
    c = ctx.collection(collection)
    ctx.access(c, caller).require_verb("delete")
    for rid in ids:
        record = await _fetch(session, ctx, c, rid, lock=True)
        if expected_versions and rid in expected_versions and (
                record.current_version != expected_versions[rid]):
            raise RecordError("AD-VERSION-CONFLICT", f"{collection} record {rid} is at "
                              f"version {record.current_version}, not "
                              f"{expected_versions[rid]}", 409,
                              {"id": rid, "current_version": record.current_version})


async def plan_delete(session, ctx: AppContext, caller: Caller, collection: str,
                      ids: list[str], *, check_plan=None) -> dict:
    """The preview: the same plan `delete_records` would run, without running it.
    `check_plan` refuses it the way the delete itself would be refused."""
    try:
        await _authorize_delete(session, ctx, caller, collection, ids, None)
        plan = await compute_plan(session, ctx, [(collection, rid) for rid in ids])
        if check_plan is not None:
            check_plan(plan)
        return plan.summary(ctx, caller)
    finally:
        await session.rollback()


async def execute_plan(session, ctx: AppContext, caller: Caller, plan: DeletePlan) -> None:
    """Apply a plan with no blockers. Doesn't commit."""
    touched: set[str] = set()
    now = utcnow()
    for collection, rid, name in plan.unlinks:
        c = ctx.collection(collection)
        record = await _fetch(session, ctx, c, rid, lock=True)
        if name not in record.doc:
            continue
        session.add(_history(record))
        record.doc = {k: v for k, v in record.doc.items() if k != name}
        set_size(session, ctx, record)
        for column, value in side_columns(c, record.doc).items():
            setattr(record, column, value)
        record.current_version += 1
        record.updated_at = now
        record.author, record.via = caller.author, caller.via
        touched.add(collection)
    by_collection: dict[str, list[str]] = {}
    for collection, rid in plan.deletes:
        by_collection.setdefault(collection, []).append(rid)
    for collection, ids in by_collection.items():
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            await app_artifacts.detach_records(session, ctx, collection, chunk)
            # Release exactly what these records were charged.
            gone, size = (await session.execute(
                select(func.count(), func.coalesce(func.sum(R.size_bytes), 0)).where(
                    R.app_id == ctx.app_id, R.collection == collection, R.id.in_(chunk))
            )).one()
            quotas.note(session, ctx, records=-gone, bytes=-int(size))
            await session.execute(R.__table__.delete().where(
                R.app_id == ctx.app_id, R.collection == collection, R.id.in_(chunk)))
            await session.execute(AppDataRecordVersion.__table__.delete().where(
                AppDataRecordVersion.app_id == ctx.app_id,
                AppDataRecordVersion.collection == collection,
                AppDataRecordVersion.record_id.in_(chunk)))
        touched.add(collection)
    await session.flush()
    await bump_counters(session, ctx.app_id, touched)


async def delete_records(session, ctx: AppContext, caller: Caller, collection: str,
                         ids: list[str], *,
                         expected_versions: dict[str, int] | None = None) -> dict:
    """Compute the plan and run it in one transaction, or refuse with the plan
    when a `restrict` ref blocks it."""
    try:
        ctx.collection(collection)
        async with delete_lock(session, ctx, collection):
            await _authorize_delete(session, ctx, caller, collection, ids,
                                    expected_versions)
            plan = await compute_plan(session, ctx, [(collection, rid) for rid in ids])
            summary = plan.summary(ctx, caller)
            if plan.blocked:
                raise RecordError("AD-REF-RESTRICT", "referenced by records whose ref is "
                                  "on_delete: restrict", 409, summary)
            await execute_plan(session, ctx, caller, plan)
            await quotas.settle(session)
            await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return summary


async def delete_record(session, ctx: AppContext, caller: Caller, collection: str,
                        record_id: str, *, expected_version: int | None = None) -> dict:
    expected = {record_id: expected_version} if expected_version is not None else None
    return await delete_records(session, ctx, caller, collection, [record_id],
                                expected_versions=expected)
