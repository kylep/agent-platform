"""Quotas and scan budgets (design 39, "Storage" → "Quotas", "Tool views" →
"Bulk reads", "Collections" → artifact bytes).

Every limit here is platform-owned: Kyle sets it (`set_quota`), per App or per
owner, and an App's own definitions can't move it. A limit nobody has set is
the config default (`AP_APP_DATA_*`). Every breach fails closed with a stable
`AD-QUOTA-*` code and a detail naming the scope, the limit, its maximum and
the current use:

- **413** for what a scope holds or one execution reads: records, bytes, Apps,
  open drafts, open proposals, one scan's rows and seconds;
- **429** for what refills: writes per hour, scan rows per hour and concurrent
  scans, with `retry_after` seconds until the window turns.

**Storage** (records, bytes) is usage kept on the quota rows, maintained by the
records engine in the same transaction as the write. The engine notes each
change as it makes it (`note`, `add_bytes` for App-owned artifacts); `settle`
charges the transaction's total just before its commit. Charging locks the
App's row, then the owner's, so two writers can't both pass on the same
headroom: the second waits for the first's commit and sees its usage. It runs
after any collection lock the write took, so the lock order is always
collection, App quota, owner quota, and a batch can't deadlock a single write.
A transaction that noted usage and commits without settling is refused at
commit, so no write path can skip the charge by accident.

Bytes are each record's JSON doc as `doc_bytes` measures it, stored on the
record so a delete releases exactly what the write charged; history rows are
not counted. Usage is only released by deletes (retention's included), never
by retiring: a retired App's records still take space, so it keeps counting
against its owner, and it keeps counting as one of the owner's Apps.

**Writes per hour** count records created or changed (an unchanged upsert or
a skipped record isn't a write; deletes aren't), in fixed one-hour windows.

**Scan budgets** bound tool-view scans: one execution reads at most
2,000,000 rows in 60 seconds, an App runs at most two scans at once, and an
App and its owner each have scan rows per hour. `scan_budget` takes a lease
before the scan: a concurrency slot plus a reservation of the rows it may
read, so two concurrent scans can't both spend the same hourly headroom. A
lease outlives no scan by more than a grace period, so a crashed worker's
slot lapses by itself.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, AsyncIterable, AsyncIterator

from sqlalchemy import delete, event, func, select, update
from sqlalchemy.orm import Session

from agentplatform.appdata.access import RecordError
from agentplatform.appdata.models import (AppDataApp, AppDataBuildOp, AppDataDefinition,
                                          AppDataQuota, AppDataScanLease)
from agentplatform.config import Settings, get_settings
from agentplatform.db import utcnow

Q = AppDataQuota
L = AppDataScanLease

APP_LIMITS = ("max_records", "max_bytes", "writes_per_hour", "scan_rows_per_hour",
              "max_concurrent_scans", "max_open_drafts", "max_open_proposals")
OWNER_LIMITS = ("max_records", "max_bytes", "writes_per_hour", "scan_rows_per_hour",
                "max_apps", "max_open_proposals")
LIMITS = {"app": APP_LIMITS, "owner": OWNER_LIMITS}

# A lease lapses this long after its scan's deadline.
LEASE_GRACE = timedelta(seconds=30)
_USAGE_KEY = "appdata_quota_usage"


def _settings() -> Settings:
    return get_settings()


def defaults(scope_kind: str, settings: Settings | None = None) -> dict[str, int]:
    """The limits a scope has until Kyle sets its own."""
    s = settings or _settings()
    prefix = f"app_data_{scope_kind}_"
    return {name: getattr(s, prefix + name) for name in LIMITS[scope_kind]}


def doc_bytes(doc: dict) -> int:
    """A record's charge against the byte quota: its doc as compact JSON."""
    return len(json.dumps(doc, separators=(",", ":"), ensure_ascii=False, sort_keys=True,
                          default=str).encode())


def _hour(now: datetime) -> int:
    return int(now.timestamp()) // 3600


def _retry_after(now: datetime) -> int:
    return max(1, (_hour(now) + 1) * 3600 - int(now.timestamp()))



def _breach(code: str, status: int, scope: str, scope_id: str, limit: str, maximum: int,
            used: int, requested: int | None = None, now: datetime | None = None,
            what: str = "") -> RecordError:
    detail: dict[str, Any] = {"scope": scope, "scope_id": scope_id, "limit": limit,
                              "max": maximum, "used": used}
    if requested is not None:
        detail["requested"] = requested
    if status == 429 and now is not None:
        detail["retry_after"] = _retry_after(now)
    message = f"{scope} {scope_id} is at its {limit} quota: {used} of {maximum}"
    if requested is not None:
        message += f", and this needs {requested} more"
    return RecordError(code, message + (f" {what}" if what else ""), status, detail)


# --- the per-transaction tally ------------------------------------------------------------

@dataclass
class _Usage:
    records: int = 0
    bytes: int = 0
    writes: int = 0


def note(session, ctx, *, records: int = 0, bytes: int = 0, writes: int = 0) -> None:
    """Record a change the open transaction made to `ctx`'s storage; `settle`
    charges it. Negative records and bytes release."""
    if not session.in_transaction():
        # Tie the tally to a transaction, so a rollback clears it even when
        # nothing was executed yet.
        session.sync_session.begin()
    tally = session.info.setdefault(_USAGE_KEY, {})
    usage = tally.setdefault((ctx.app_id, ctx.owner), _Usage())
    usage.records += records
    usage.bytes += bytes
    usage.writes += writes


def add_bytes(session, ctx, delta: int) -> None:
    """The hook for App-owned artifacts (A8): bytes an artifact written into
    (or freed from) one of `ctx`'s fields adds to its byte quota, charged with
    the transaction that references it."""
    note(session, ctx, bytes=delta)


@event.listens_for(Session, "before_commit")
def _refuse_unsettled(session) -> None:
    if session.info.get(_USAGE_KEY):
        session.info.pop(_USAGE_KEY, None)
        raise RuntimeError("app data usage was noted but never settled; call "
                           "quotas.settle before committing")


@event.listens_for(Session, "after_soft_rollback")
def _forget(session, previous_transaction) -> None:
    session.info.pop(_USAGE_KEY, None)


# --- quota rows -----------------------------------------------------------------------------

def _insert_stmt(session):
    if session.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


async def _ensure(session, scope_kind: str, scope_id: str) -> None:
    insert = _insert_stmt(session)
    await session.execute(insert(Q).values(
        scope_kind=scope_kind, scope_id=scope_id, used_records=0, used_bytes=0,
        write_window=0, writes_in_window=0, scan_window=0, scan_rows_in_window=0,
        set_by="default", updated_at=utcnow()).on_conflict_do_nothing(
        index_elements=[Q.scope_kind, Q.scope_id]))


async def _read(session, scope_kind: str, scope_id: str) -> AppDataQuota | None:
    return (await session.execute(
        select(Q).where(Q.scope_kind == scope_kind, Q.scope_id == scope_id)
        .execution_options(populate_existing=True))).scalar_one_or_none()


async def _locked(session, scope_kind: str, scope_id: str) -> AppDataQuota:
    """The row, created if missing, under a write lock held to the end of the
    transaction: a no-op UPDATE is a row lock on Postgres and the write lock
    on SQLite, so the read that follows can't go stale before the charge."""
    await _ensure(session, scope_kind, scope_id)
    await session.execute(update(Q).where(Q.scope_kind == scope_kind,
                                          Q.scope_id == scope_id)
                          .values(used_records=Q.used_records))
    return await _read(session, scope_kind, scope_id)


def _limit(row: AppDataQuota | None, scope_kind: str, name: str,
           settings: Settings) -> int:
    value = getattr(row, name, None) if row is not None else None
    return value if value is not None else defaults(scope_kind, settings)[name]


def _window(count: int, window: int, hour: int) -> int:
    return count if window == hour else 0


# --- storage and writes ------------------------------------------------------------------------

async def settle(session, *, now: datetime | None = None) -> None:
    """Charge what the open transaction noted, or refuse it. Call it after the
    writes and before the commit; it doesn't commit."""
    tally = session.info.pop(_USAGE_KEY, None)
    if not tally:
        return
    now = now or utcnow()
    settings = _settings()
    for (app_id, owner), usage in sorted(tally.items()):
        if usage.records == usage.bytes == usage.writes == 0:
            continue
        rows = [("app", app_id, await _locked(session, "app", app_id)),
                ("owner", owner, await _locked(session, "owner", owner))]
        hour = _hour(now)
        for scope_kind, scope_id, row in rows:
            _check_storage(scope_kind, scope_id, row, usage, hour, now, settings)
        for scope_kind, scope_id, row in rows:
            await session.execute(update(Q).where(
                Q.scope_kind == scope_kind, Q.scope_id == scope_id).values(
                used_records=max(0, row.used_records + usage.records),
                used_bytes=max(0, row.used_bytes + usage.bytes),
                write_window=hour,
                writes_in_window=_window(row.writes_in_window, row.write_window, hour)
                + max(0, usage.writes),
                updated_at=now))


def _check_storage(scope_kind: str, scope_id: str, row: AppDataQuota, usage: _Usage,
                   hour: int, now: datetime, settings: Settings) -> None:
    """Only a growing dimension can breach: a delete always goes through, even
    for a scope already over a limit Kyle has lowered."""
    max_records = _limit(row, scope_kind, "max_records", settings)
    if usage.records > 0 and row.used_records + usage.records > max_records:
        raise _breach("AD-QUOTA-RECORDS", 413, scope_kind, scope_id, "max_records",
                      max_records, row.used_records, usage.records)
    max_bytes = _limit(row, scope_kind, "max_bytes", settings)
    if usage.bytes > 0 and row.used_bytes + usage.bytes > max_bytes:
        raise _breach("AD-QUOTA-BYTES", 413, scope_kind, scope_id, "max_bytes", max_bytes,
                      row.used_bytes, usage.bytes)
    per_hour = _limit(row, scope_kind, "writes_per_hour", settings)
    written = _window(row.writes_in_window, row.write_window, hour)
    if usage.writes > 0 and written + usage.writes > per_hour:
        raise _breach("AD-QUOTA-WRITES", 429, scope_kind, scope_id, "writes_per_hour",
                      per_hour, written, usage.writes, now)


async def precheck(session, ctx, *, records: int, bytes: int,
                   now: datetime | None = None) -> None:
    """A fail-fast check before a batch or a commit replays its records: refuse
    when the App or its owner has no room left at all, rather than after
    writing thousands of rows. `records` and `bytes` are what the write may
    add at most; an upsert can add none, so only an exhausted limit refuses
    here, and `settle` makes the exact charge."""
    now = now or utcnow()
    settings = _settings()
    hour = _hour(now)
    for scope_kind, scope_id in (("app", ctx.app_id), ("owner", ctx.owner)):
        row = await _read(session, scope_kind, scope_id)
        used_records = row.used_records if row is not None else 0
        used_bytes = row.used_bytes if row is not None else 0
        written = _window(row.writes_in_window, row.write_window, hour) if row else 0
        checks = (
            ("AD-QUOTA-RECORDS", 413, "max_records", used_records, records),
            ("AD-QUOTA-BYTES", 413, "max_bytes", used_bytes, bytes),
            ("AD-QUOTA-WRITES", 429, "writes_per_hour", written, records),
        )
        for code, status, name, used, wanted in checks:
            maximum = _limit(row, scope_kind, name, settings)
            if wanted > 0 and used >= maximum:
                raise _breach(code, status, scope_kind, scope_id, name, maximum, used,
                              wanted, now)


async def transfer_owner(session, app_id: str, old_owner: str, new_owner: str) -> None:
    """Move an App's storage from one owner to another (an ownership transfer,
    or an agent's Apps passing to Kyle when it's deleted). The new owner takes
    it unchecked, since refusing would leave the App with no owner; past its
    limits, its next write is what refuses. Doesn't commit."""
    app = await _locked(session, "app", app_id)
    for owner, sign in sorted(((old_owner, -1), (new_owner, 1))):
        row = await _locked(session, "owner", owner)
        await session.execute(update(Q).where(Q.scope_kind == "owner",
                                              Q.scope_id == owner).values(
            used_records=max(0, row.used_records + sign * app.used_records),
            used_bytes=max(0, row.used_bytes + sign * app.used_bytes)))


# --- counted things: Apps, drafts, proposals -------------------------------------------------

def _owner_filter(owner: str):
    if owner == "kyle":
        return AppDataApp.owner_kind == "kyle"
    return (AppDataApp.owner_kind == "agent") & (AppDataApp.owner_id == owner.split(":", 1)[1])


async def check_new_app(session, owner: str) -> None:
    """Call in the transaction that creates an App, before inserting it. Holds
    the owner's quota row, so two creates can't both take the last slot.
    Retired Apps count: their names and records are still theirs."""
    row = await _locked(session, "owner", owner)
    maximum = _limit(row, "owner", "max_apps", _settings())
    count = (await session.execute(select(func.count()).select_from(AppDataApp)
                                   .where(_owner_filter(owner)))).scalar_one()
    if count >= maximum:
        raise _breach("AD-QUOTA-APPS", 413, "owner", owner, "max_apps", maximum, count, 1)


async def check_new_draft(session, app_id: str) -> None:
    """Call in the transaction that opens a new draft definition (not one that
    edits an open draft in place), before inserting it."""
    row = await _locked(session, "app", app_id)
    maximum = _limit(row, "app", "max_open_drafts", _settings())
    count = (await session.execute(select(func.count()).select_from(AppDataDefinition)
                                   .where(AppDataDefinition.app_id == app_id,
                                          AppDataDefinition.state == "draft"))).scalar_one()
    if count >= maximum:
        raise _breach("AD-QUOTA-DRAFTS", 413, "app", app_id, "max_open_drafts", maximum,
                      count, 1)


async def check_new_proposal(session, app_id: str, owner: str, *, open_for_app: int,
                             open_for_owner: int) -> None:
    """Call before opening a proposal, with the open counts the lifecycle
    reads from its own table, in the same transaction."""
    settings = _settings()
    for scope_kind, scope_id, count in (("app", app_id, open_for_app),
                                        ("owner", owner, open_for_owner)):
        row = await _locked(session, scope_kind, scope_id)
        maximum = _limit(row, scope_kind, "max_open_proposals", settings)
        if count >= maximum:
            raise _breach("AD-QUOTA-PROPOSALS", 413, scope_kind, scope_id,
                          "max_open_proposals", maximum, count, 1)


async def prune_build_ops(session, *, now: datetime | None = None) -> int:
    """Drop builder receipts past their retention (90 days by default): a
    retry that late is a new call. Commits; returns how many went."""
    now = now or utcnow()
    cutoff = now - timedelta(days=_settings().app_data_build_ops_retention_days)
    try:
        result = await session.execute(delete(AppDataBuildOp)
                                        .where(AppDataBuildOp.created_at < cutoff))
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return result.rowcount or 0


# --- Kyle's setter and the readout --------------------------------------------------------------

async def describe(session, scope_kind: str, scope_id: str, *,
                   now: datetime | None = None) -> dict:
    """A scope's effective limits, which of them Kyle set, and its use."""
    if scope_kind not in LIMITS:
        raise RecordError("AD-QUOTA-INVALID", "scope must be app or owner", 422)
    now = now or utcnow()
    settings = _settings()
    row = await _read(session, scope_kind, scope_id)
    hour = _hour(now)
    used = {"records": 0, "bytes": 0, "writes_this_hour": 0, "scan_rows_this_hour": 0}
    if row is not None:
        used = {"records": row.used_records, "bytes": row.used_bytes,
                "writes_this_hour": _window(row.writes_in_window, row.write_window, hour),
                "scan_rows_this_hour": _window(row.scan_rows_in_window, row.scan_window,
                                               hour)}
    return {"scope": scope_kind, "scope_id": scope_id,
            "limits": {name: _limit(row, scope_kind, name, settings)
                       for name in LIMITS[scope_kind]},
            "set": {name: getattr(row, name) for name in LIMITS[scope_kind]
                    if row is not None and getattr(row, name) is not None},
            "set_by": row.set_by if row is not None else "default",
            "used": used}


async def set_quota(session, scope_kind: str, scope_id: str, limits: dict, *,
                    set_by: str, is_kyle: bool) -> dict:
    """Kyle sets limits for one App or owner. `is_kyle` is the caller's proof,
    checked by the route (a Kyle session); anything else is refused. A value
    of None returns that limit to the default. A limit below current use is
    allowed: writes then refuse until deletes bring use back under it."""
    if not is_kyle:
        raise RecordError("AD-QUOTA-FORBIDDEN", "only Kyle sets App data quotas", 403)
    if scope_kind not in LIMITS:
        raise RecordError("AD-QUOTA-INVALID", "scope must be app or owner", 422)
    if not isinstance(limits, dict) or not limits:
        raise RecordError("AD-QUOTA-INVALID", "name at least one limit", 422)
    unknown = sorted(set(limits) - set(LIMITS[scope_kind]))
    if unknown:
        raise RecordError("AD-QUOTA-INVALID", f"{scope_kind} quotas have no "
                          f"{', '.join(unknown)}", 422,
                          {"unknown": unknown, "limits": list(LIMITS[scope_kind])})
    for name, value in limits.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)
                                  or value < 0):
            raise RecordError("AD-QUOTA-INVALID", f"{name} must be a whole number of at "
                              "least 0, or null for the default", 422, {"limit": name})
    try:
        await _locked(session, scope_kind, scope_id)
        await session.execute(update(Q).where(Q.scope_kind == scope_kind,
                                              Q.scope_id == scope_id)
                              .values(**limits, set_by=set_by, updated_at=utcnow()))
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return await describe(session, scope_kind, scope_id)


# --- scan budgets --------------------------------------------------------------------------------

class ScanBudget:
    """One scan's allowance: `charge` every row read, and it raises once the
    scan passes the most it may read or its deadline."""

    def __init__(self, *, lease_id: str, app_id: str, owner: str, cap: int,
                 over: RecordError, max_seconds: float, now: datetime):
        self.lease_id, self.app_id, self.owner = lease_id, app_id, owner
        self.cap = cap
        self.scanned = 0
        self.max_seconds = max_seconds
        self.now = now
        self._over = over
        self._deadline = time.monotonic() + max_seconds

    def charge(self, rows: int = 1) -> None:
        self.scanned += rows
        if self.scanned > self.cap:
            raise self._over
        self.check_time()

    def check_time(self) -> None:
        if time.monotonic() > self._deadline:
            raise self.time_error()

    def time_error(self) -> RecordError:
        return _breach("AD-QUOTA-SCAN-TIME", 413, "execution", self.lease_id,
                       "scan_max_seconds", int(self.max_seconds), int(self.max_seconds),
                       what="; narrow the scan's filters")

    async def rows(self, source: AsyncIterable) -> AsyncIterator:
        """Pass `source` through, charging each item."""
        async for item in source:
            self.charge()
            yield item


async def _open_lease(session, ctx, settings: Settings, now: datetime) -> ScanBudget:
    try:
        app_row = await _locked(session, "app", ctx.app_id)
        owner_row = await _locked(session, "owner", ctx.owner)
        await session.execute(delete(L).where(L.expires_at <= now,
                                              (L.app_id == ctx.app_id)
                                              | (L.owner == ctx.owner)))
        running, app_reserved = (await session.execute(
            select(func.count(), func.coalesce(func.sum(L.reserved_rows), 0))
            .where(L.app_id == ctx.app_id))).one()
        owner_reserved = (await session.execute(
            select(func.coalesce(func.sum(L.reserved_rows), 0))
            .where(L.owner == ctx.owner))).scalar_one()
        concurrent = _limit(app_row, "app", "max_concurrent_scans", settings)
        if running >= concurrent:
            raise _breach("AD-QUOTA-SCAN-CONCURRENCY", 429, "app", ctx.app_id,
                          "max_concurrent_scans", concurrent, running, 1, now)
        hour = _hour(now)
        cap = settings.app_data_scan_max_rows
        over = _breach("AD-QUOTA-SCAN-EXECUTION", 413, "execution", ctx.app_id,
                       "scan_max_rows", cap, cap, what="in one scan; narrow its filters")
        for scope_kind, scope_id, row, reserved in (
                ("app", ctx.app_id, app_row, app_reserved),
                ("owner", ctx.owner, owner_row, owner_reserved)):
            per_hour = _limit(row, scope_kind, "scan_rows_per_hour", settings)
            spent = _window(row.scan_rows_in_window, row.scan_window, hour) + reserved
            left = per_hour - spent
            if left <= 0:
                raise _breach("AD-QUOTA-SCAN-ROWS", 429, scope_kind, scope_id,
                              "scan_rows_per_hour", per_hour, spent, now=now)
            if left < cap:
                cap = left
                over = _breach("AD-QUOTA-SCAN-ROWS", 429, scope_kind, scope_id,
                               "scan_rows_per_hour", per_hour, per_hour, now=now)
        lease = L(id=uuid.uuid4().hex, app_id=ctx.app_id, owner=ctx.owner,
                  reserved_rows=cap, started_at=now,
                  expires_at=now + timedelta(seconds=settings.app_data_scan_max_seconds)
                  + LEASE_GRACE)
        session.add(lease)
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
    return ScanBudget(lease_id=lease.id, app_id=ctx.app_id, owner=ctx.owner, cap=cap,
                      over=over, max_seconds=settings.app_data_scan_max_seconds, now=now)


async def _close_lease(session, budget: ScanBudget) -> None:
    """Free the slot and charge the rows actually read (never more than the
    reservation) to the App's and the owner's hour."""
    await session.rollback()
    spent = min(budget.scanned, budget.cap)
    hour = _hour(budget.now)
    try:
        for scope_kind, scope_id in (("app", budget.app_id), ("owner", budget.owner)):
            row = await _locked(session, scope_kind, scope_id)
            await session.execute(update(Q).where(
                Q.scope_kind == scope_kind, Q.scope_id == scope_id).values(
                scan_window=hour,
                scan_rows_in_window=_window(row.scan_rows_in_window, row.scan_window, hour)
                + spent))
        await session.execute(delete(L).where(L.id == budget.lease_id))
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


@asynccontextmanager
async def scan_budget(session, ctx, *, now: datetime | None = None) -> AsyncIterator[ScanBudget]:
    """Run one scan of `ctx` under its budgets:

        async with scan_budget(session, ctx) as budget:
            async for row in scan_view(session, ctx, caller, view, budget=budget): ...

    Entering takes a slot and reserves rows, or refuses (429); the body is cut
    off at the time limit; leaving charges what was read, whether the scan
    finished or not. The session is the scan's own: entering and leaving
    commit it."""
    settings = _settings()
    now = now or utcnow()
    budget = await _open_lease(session, ctx, settings, now)
    try:
        async with asyncio.timeout(settings.app_data_scan_max_seconds):
            yield budget
    except TimeoutError:
        raise budget.time_error() from None
    finally:
        await _close_lease(session, budget)
