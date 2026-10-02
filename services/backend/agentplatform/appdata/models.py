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

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, Index, Integer, String,
                        Text, UniqueConstraint)
from sqlalchemy import text  # the partial indexes' predicates
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
    # One line for the Apps list; the builder writes it at create.
    description: Mapped[str] = mapped_column(Text, default="")
    # IANA name; views bucket days and weeks in it.
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    # active | retired
    status: Mapped[str] = mapped_column(String(16), default="active")
    # Build notes: the maintainer's runbook. The revision is the
    # compare-and-swap token for concurrent edits.
    notes: Mapped[str] = mapped_column(Text, default="")
    notes_revision: Mapped[int] = mapped_column(Integer, default=0)
    notes_updated_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    notes_updated_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
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
    a draft is edited in place under `revision`, then published.

    A published version number is the App's `approved_version` at the publish
    that wrote it, and a definition gets a row only when that publish changed
    it, so the approved state at version N is the newest row at or below N for
    each name (`appdata/lifecycle.py`). A draft holds version 0 until then:
    there is at most one per name."""
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
    # The approved version a draft was written against; validate reports the
    # draft stale when its definition was published again since.
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # A removal: as a draft, "drop this definition at publish"; published, the
    # tombstone that ends the name's run in the approved state.
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Who wrote this revision and why: a participant string, the run, a reason.
    author: Mapped[str] = mapped_column(String(160))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    # A version published by approving a proposal: who approved it (`kyle`)
    # and which proposal. The proposer stays the `author`.
    approved_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
        # Partial on Postgres: a record that fills no ix_text2 / ix_num1 is in
        # neither index, and the engine's results and bars queries always
        # constrain the column, so the planner can prove the predicate.
        Index("ix_app_data_records_t1_t2_time", "app_id", "collection", "ix_text1",
              "ix_text2", "ix_time1", postgresql_where=text("ix_text2 IS NOT NULL")),
        Index("ix_app_data_records_t1_num", "app_id", "collection", "ix_text1", "ix_num1",
              postgresql_where=text("ix_num1 IS NOT NULL")),
        Index("ix_app_data_records_time", "app_id", "collection", "ix_time1"),
        # Default ordering (newest first) and retention's age and count cuts.
        Index("ix_app_data_records_created", "app_id", "collection", "created_at"),
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
    # What `doc` costs against the byte quota (appdata/quotas.doc_bytes),
    # stored so a delete releases exactly what the write charged.
    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)


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
    # An upsert's key: the fields of the `unique` rule it matches on, when the
    # collection declares more than one.
    key: Mapped[list | None] = mapped_column(_JSON, nullable=True)
    doc: Mapped[dict] = mapped_column(_JSON)


class AppDataQuota(Base):
    """A platform-owned limit, set by Kyle, per App or per owner, with the
    usage it is checked against. A breach fails closed (appdata/quotas.py).

    A limit left None means the platform default from config; the row still
    exists, because it holds the usage. The hourly counters are fixed
    windows: `*_window` is the hour (epoch seconds // 3600) the count belongs
    to, and a write in a later hour starts the count again."""
    __tablename__ = "app_data_quotas"
    # app | owner. scope_id is the App id or the owner's participant string.
    scope_kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    max_records: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    writes_per_hour: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    scan_rows_per_hour: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_concurrent_scans: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Owner rows: Apps (retired ones included). App rows: open drafts.
    max_apps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_open_drafts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_open_proposals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_records: Mapped[int] = mapped_column(BigInteger, default=0)
    used_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    write_window: Mapped[int] = mapped_column(BigInteger, default=0)
    writes_in_window: Mapped[int] = mapped_column(BigInteger, default=0)
    scan_window: Mapped[int] = mapped_column(BigInteger, default=0)
    scan_rows_in_window: Mapped[int] = mapped_column(BigInteger, default=0)
    set_by: Mapped[str] = mapped_column(String(160))
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)


class AppDataScanLease(Base):
    """One running scan: a concurrency slot and the rows it reserved from the
    hourly scan budgets. A scan is bounded in time, so a lease left behind by
    a crashed worker lapses at `expires_at` instead of holding a slot."""
    __tablename__ = "app_data_scan_leases"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(String(32), index=True)
    owner: Mapped[str] = mapped_column(String(160), index=True)
    reserved_rows: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(_TS, index=True)


class AppDataWriteCounter(Base):
    """Bumped by every committed write to a collection. A cached view result
    is keyed on its sources' counters, so any write retires it."""
    __tablename__ = "app_data_write_counters"
    app_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    counter: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)


class AppDataViewCache(Base):
    """Bounded, authority-versioned tool-view result cache."""
    __tablename__ = "app_data_view_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(32), index=True)
    view: Mapped[str] = mapped_column(String(64))
    principal: Mapped[str] = mapped_column(String(128))
    result: Mapped[dict] = mapped_column(_JSON)
    bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    accessed_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, index=True)


class AppDataArtifact(Base):
    """An App-owned artifact (design 39, "Collections" → "Artifact fields").

    The artifact row and its bytes stay in `artifacts` / `artifact_blobs`;
    this row is what takes it out of the plain artifact surface. Every
    artifact route, the list and the event feed look here first, and an
    artifact named here is authorized through its owning field's read access
    and nothing else. The owning field is the first one that referenced it
    (or the one an upload named); it never changes. `size` is the bytes the
    App is charged, copied so the App's usage is one sum over this table."""
    __tablename__ = "app_data_artifacts"
    artifact_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(32), index=True)
    collection: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)


class AppDataArtifactRef(Base):
    """One record field holding an App-owned artifact. The artifact is
    deleted when its last row here goes; an upload with none yet is swept
    after a day."""
    __tablename__ = "app_data_artifact_refs"
    __table_args__ = (Index("ix_app_data_artifact_refs_record", "app_id", "collection",
                            "record_id"),)
    artifact_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    app_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    collection: Mapped[str] = mapped_column(String(64), primary_key=True)
    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    field: Mapped[str] = mapped_column(String(64), primary_key=True)


class AppDataToolCall(Base):
    """A minted tool-call credential (design 39, "Tool-call credentials"),
    by jti. The API refuses the credential once `revoked_at` is set, which the
    broker does when the call returns, so a copy outlives nothing."""
    __tablename__ = "app_data_tool_calls"
    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    # The call (or, for Kyle's page actions in Release 2, the intent) it is for.
    call_id: Mapped[str] = mapped_column(String(64), unique=True)
    # tool_call | page_intent
    kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(_TS, index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    # Nullable for credentials minted before the view-execution rollout.
    scan_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_fields: Mapped[list | None] = mapped_column(_JSON, nullable=True)


class AppDataProposal(Base):
    """A change only Kyle's approval publishes (design 39, "Proposals"): a
    widening bundle, a wider rollback, or an ownership transfer. What Kyle
    reviews is frozen here, content-addressed by `digest`, with the approved
    version and authority generation it was computed against; approval
    publishes it only if neither has moved and the delta is the same."""
    __tablename__ = "app_data_proposals"
    __table_args__ = (Index("ix_app_data_proposals_app_state", "app_id", "state"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(String(32))
    # bundle | rollback | transfer
    kind: Mapped[str] = mapped_column(String(16))
    # The frozen change: {changes: [...]}, plus rollback_to or transfer_to.
    bundle: Mapped[dict] = mapped_column(_JSON)
    # sha256 over the canonical bundle, base and delta: what Kyle approves.
    digest: Mapped[str] = mapped_column(String(64))
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    authority_generation: Mapped[int] = mapped_column(Integer)
    # The authority delta in plain words (A.describe): added, removed, widening.
    delta: Mapped[dict] = mapped_column(_JSON)
    # What validate reported at propose: data dropping, reindexing.
    validation: Mapped[dict] = mapped_column(_JSON)
    # open | published | declined | stale | withdrawn; only open ever changes.
    state: Mapped[str] = mapped_column(String(16), default="open")
    # The participant string that proposed it, and the run.
    proposer: Mapped[str] = mapped_column(String(160))
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[str | None] = mapped_column(String(160), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(_TS, nullable=True)
    # Why it closed: Kyle's decline reason, or what made it stale.
    outcome: Mapped[dict | None] = mapped_column(_JSON, nullable=True)
    # One Relay card for the proposal's lifetime; a state change edits it.
    relay_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(_TS, default=utcnow, onupdate=utcnow)


class AppDataPageIntent(Base):
    """Kyle's five-minute confirmation of one published page template."""
    __tablename__ = "app_data_page_intents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    app_id: Mapped[str] = mapped_column(String(32), index=True)
    page: Mapped[str] = mapped_column(String(64))
    page_version: Mapped[int] = mapped_column(Integer)
    approved_version: Mapped[int] = mapped_column(Integer)
    authority_generation: Mapped[int] = mapped_column(Integer)
    template: Mapped[str] = mapped_column(String(64))
    verb: Mapped[str] = mapped_column(String(16))
    collection: Mapped[str] = mapped_column(String(64))
    record_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    record_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    values: Mapped[dict] = mapped_column(_JSON, default=dict)
    payload_digest: Mapped[str] = mapped_column(String(64))
    plan_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    confirmation: Mapped[dict] = mapped_column(_JSON)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(_TS)


class AppDataPageReceipt(Base):
    """One committed write per intent; retry returns this exact outcome."""
    __tablename__ = "app_data_page_receipts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    intent_id: Mapped[str] = mapped_column(String(32), unique=True)
    app_id: Mapped[str] = mapped_column(String(32), index=True)
    principal: Mapped[str] = mapped_column(String(160))
    result: Mapped[dict] = mapped_column(_JSON)
    created_at: Mapped[datetime] = mapped_column(_TS, default=utcnow)
