"""The `app_data` store's tables (docs/design/39, "Apps as state", "Storage").

An App is nothing but rows here: its identity, its versioned definitions
(collections, views, pages), its records and their history, and the
bookkeeping around writing them — idempotent build ops, batch-job staging
sets, quotas, and the per-collection write counters that key the view cache.
There is no per-App schema, role or secret, so `pg_dump` of the platform
database is the whole of an App's backup.

Records keep every field in one `doc` (JSONB on Postgres, so the single GIN
`jsonb_path_ops` index can serve containment filters) and copy up to four
declared *indexed fields* into typed side columns. The composite indexes over
those columns are fixed: a collection picks which of its fields fill the
slots, it never gets an index of its own, so the index set — and the write
cost of every record — stays the same however many Apps exist.

The tables are prefixed `app_data_` in the platform schema and created by
`init_db`'s `create_all`. Like the rest of the platform there are no foreign
keys or CHECK constraints: create_all never alters an existing table, so a
CHECK on today's kinds (collection/view/page) would need a hand migration the
day a fourth arrives. The vocabularies are noted on each column and enforced
by the code that writes them. Unique constraints are the exception, because
they are the identities: an App name is never reused (retiring keeps the row),
a definition version is written once, and a record id names one record per
collection.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (JSON, BigInteger, DateTime, Float, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentplatform.db import Base, utcnow

# jsonb on Postgres (containment, the GIN index), plain JSON text on SQLite.
_JSON = JSON().with_variant(JSONB(), "postgresql")
_TS = DateTime(timezone=True)


def _uuid() -> str:
    return uuid.uuid4().hex


class AppDataApp(Base):
    """An App's identity: an immutable id, a never-reused name, an owner, and
    the pointers the authority model compares against."""
    __tablename__ = "app_data_apps"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Unique across retired Apps too: a name once used keeps meaning that App.
    name: Mapped[str] = mapped_column(String(64), unique=True)
    # agent | kyle. owner_id is the agent's name, or "kyle".
    owner_kind: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[str] = mapped_column(String(128), index=True)
    # IANA name; views bucket days and weeks in it.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # active | retired
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Build notes: the maintainer's runbook. The revision is the
    # compare-and-swap token for concurrent edits.
    notes: Mapped[str] = mapped_column(Text, default="")
    notes_revision: Mapped[int] = mapped_column(Integer, default=0)
    # The definition-set version Kyle (or self-publish) last approved; None
    # until the first publish.
    approved_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Bumped whenever an authority fact changes; part of every cache key, so
    # an access change invalidates cached view results at once.
    authority_generation: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)
    retired_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class AppDataDefinition(Base):
    """One version of one collection, view or page. A version is written once;
    a draft is edited in place under `revision`, then published."""
    __tablename__ = "app_data_definitions"
    __table_args__ = (UniqueConstraint("app_id", "kind", "name", "version",
                                       name="uq_app_data_definitions_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(String(32))
    # collection | view | page
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(_JSON)
    # draft | published
    state: Mapped[str] = mapped_column(String(16), default="draft")
    # Compare-and-swap token for draft edits (`stale_base`).
    revision: Mapped[int] = mapped_column(Integer, default=1)
    # Who wrote this revision and why: a participant string, the run, a reason.
    author: Mapped[str] = mapped_column(String(160))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)


class AppDataRecord(Base):
    """A record's current state. The system fields are columns; every declared
    field is in `doc`, and up to four indexed fields are copied into the side
    columns the fixed indexes cover."""
    __tablename__ = "app_data_records"
    __table_args__ = (
        # The design's fixed set: bars (symbol, day), results (run, ref) and
        # backtest series (experiment, strategy, day).
        Index("ix_app_data_records_t1_time", "app_id", "collection", "ix_text1", "ix_time1"),
        Index("ix_app_data_records_t1_t2_time", "app_id", "collection", "ix_text1",
              "ix_text2", "ix_time1"),
        Index("ix_app_data_records_t1_num", "app_id", "collection", "ix_text1", "ix_num1"),
        Index("ix_app_data_records_time", "app_id", "collection", "ix_time1"),
        # SQLite has no index for a JSON blob, so this one is Postgres's alone.
        Index("ix_app_data_records_doc", "doc", postgresql_using="gin",
              postgresql_ops={"doc": "jsonb_path_ops"}).ddl_if(dialect="postgresql"),
    )
    # The primary key is the record's identity: one id per collection.
    app_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    # The `version` system field; history rows below hold the earlier ones.
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    # The principal that wrote it (agent:<name>, kyle) and, for a write made
    # through a tool-call credential, the tool it came through (tool:<name>).
    author: Mapped[str] = mapped_column(String(160))
    via: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # The collection definition version the record was validated against.
    collection_version: Mapped[int] = mapped_column(Integer)
    ix_text1: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ix_text2: Mapped[str | None] = mapped_column(String(256), nullable=True)
    ix_num1: Mapped[float | None] = mapped_column(Float, nullable=True)
    ix_time1: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    doc: Mapped[dict] = mapped_column(_JSON)


class AppDataRecordVersion(Base):
    """A record as it stood at one earlier version: what `history` reads."""
    __tablename__ = "app_data_record_versions"
    app_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The system fields as they were at this version.
    author: Mapped[str] = mapped_column(String(160))
    via: Mapped[str | None] = mapped_column(String(160), nullable=True)
    collection_version: Mapped[int] = mapped_column(Integer)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    doc: Mapped[dict] = mapped_column(_JSON)


class AppDataBuildOp(Base):
    """An idempotent builder call. A retried `request_id` returns the stored
    receipt; the same id with a different `args_hash` is refused rather than
    silently answered with another call's result."""
    __tablename__ = "app_data_build_ops"
    __table_args__ = (UniqueConstraint("principal", "request_id",
                                       name="uq_app_data_build_ops_request"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # Scoped to the caller, not the App: `create` has no App until it runs.
    principal: Mapped[str] = mapped_column(String(160))
    request_id: Mapped[str] = mapped_column(String(128))
    app_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # create | draft | notes | publish | rollback | retire | ...
    op: Mapped[str] = mapped_column(String(32))
    args_hash: Mapped[str] = mapped_column(String(64))
    receipt: Mapped[dict] = mapped_column(_JSON, default=dict)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class AppDataStagingSet(Base):
    """A batch job's staging set: bound to the creator that opened it and the
    collection versions it was opened against, which commit re-checks."""
    __tablename__ = "app_data_staging_sets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(String(32), index=True)
    # The creator binding: principal, and the tool and call when it came
    # through a tool-call credential.
    creator: Mapped[str] = mapped_column(String(160))
    tool: Mapped[str | None] = mapped_column(String(128), nullable=True)
    call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # {collection: definition version} at open.
    collection_versions: Mapped[dict] = mapped_column(_JSON, default=dict)
    # open | committed | aborted | expired
    state: Mapped[str] = mapped_column(String(16), default="open")
    # Running totals against the per-set limits and the App's quota.
    record_count: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    # 24 hours after open.
    expires_at: Mapped[datetime] = mapped_column(_TS, index=True)
    committed_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)


class AppDataStagedRecord(Base):
    """A record waiting in a staging set. Invisible to every read: it lives in
    its own table, so no query over `app_data_records` can see it."""
    __tablename__ = "app_data_staged_records"
    set_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Arrival order, which commit replays.
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    collection: Mapped[str] = mapped_column(String(64))
    # None lets the engine assign one at commit.
    record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # insert | upsert | skip_existing
    mode: Mapped[str] = mapped_column(String(16))
    doc: Mapped[dict] = mapped_column(_JSON)


class AppDataQuota(Base):
    """A platform-owned limit, set by Kyle, per App or per owner, with the
    usage it is checked against. A breach fails closed."""
    __tablename__ = "app_data_quotas"
    # app | owner. scope_id is the App id or the owner's participant string.
    scope_kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    max_records: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scan_rows_per_hour: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_concurrent_scans: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_records: Mapped[int] = mapped_column(BigInteger, default=0)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    set_by: Mapped[str] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)


class AppDataWriteCounter(Base):
    """Bumped by every committed write to a collection. A cached view result
    is keyed on its sources' counters, so any write retires it."""
    __tablename__ = "app_data_write_counters"
    app_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    counter: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)
