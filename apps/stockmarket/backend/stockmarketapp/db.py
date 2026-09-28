"""The price archive: symbols, daily bars, watchlists and briefs, in the app's
own schema (app_stockmarket).

The app connects with its provisioned role (secret app-stockmarket-db → env)
and is confined to its schema by grant. Models are schema-less here; the
engine maps them into app_stockmarket via schema_translate_map, which also
lets tests run on sqlite untranslated. Same arrangement as the news app.

One table has a second writer: `bars` is filled by the `prices` tool, which
holds the same DB secret and upserts by (symbol, day). The app owns the DDL
for it regardless — the tool creates nothing, so there is exactly one
definition of the schema and it lives here.
"""
import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, DateTime, Float, Index, Integer,
                        LargeBinary, String, Text, UniqueConstraint)
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Symbol kinds that exist for the backtest engine's own use and must never
# reach a person-facing surface: `research` symbols are loaded only so a
# spec can reference them, and `fx` (CAD=X) is a conversion series, not
# something anyone charts or watches.
HIDDEN_KINDS: tuple[str, ...] = ("research", "fx")

# The three indexes the brief covers, tracked for everyone and not removable
# from any one person's watchlist.
INDEXES: list[tuple[str, str]] = [
    ("QQQ", "Nasdaq 100"),
    ("SPY", "S&P 500"),
    ("XIU.TO", "S&P/TSX 60"),
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Symbol(Base):
    """A tracked ticker. `status` is the loading state the UI renders and the
    `prices` tool maintains: `pending` means watchlisted but never loaded (the
    tool backfills it in full on its next pass), `ok` means it has bars,
    `invalid` means Yahoo had nothing under that ticker."""
    __tablename__ = "symbols"
    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(8), default="watch")   # index | watch | research | fx
    status: Mapped[str] = mapped_column(String(8), default="pending", index=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    # Yahoo's history_metadata currency (USD, CAD, …); NULL when unknown —
    # the `prices` tool never guesses it.
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Bar(Base):
    """One daily OHLCV bar. Written by the `prices` tool, never by the app —
    the app has no third-party egress and could not fetch these if it wanted
    to. `day` is an ISO date string, matching the news app's browse-axis
    convention and keeping sqlite tests honest.

    `close` stays the display series (fully adjusted, for the chart).
    `close_split_adj` is split-adjusted but NOT dividend-adjusted (Yahoo's
    `Close` under `auto_adjust=False`) — the basis the backtest engine
    reconstructs total return from. `adj_close` is split+dividend adjusted
    (Yahoo's `Adj Close`), used only as the engine's cross-check. `dividend`
    and `split_ratio` are the raw corporate-action events for that day
    (NULL/0 on an ordinary day)."""
    __tablename__ = "bars"
    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float)
    close_split_adj: Mapped[float | None] = mapped_column(Float, nullable=True)
    adj_close: Mapped[float | None] = mapped_column(Float, nullable=True)
    dividend: Mapped[float | None] = mapped_column(Float, nullable=True)
    split_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Widened from Integer: some symbols' raw share volume overflows a 32-bit
    # signed int (index/ETF volumes run well past 2^31 over a 10y fetch).
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class Watch(Base):
    """One person's interest in one ticker. Watchlists are per-user; the three
    indexes are pinned for everyone and never appear here."""
    __tablename__ = "watchlist"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user: Mapped[str] = mapped_column(String(128), index=True)
    symbol: Mapped[str] = mapped_column(String(12), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (UniqueConstraint("user", "symbol", name="uq_watch_user_symbol"),)


class Brief(Base):
    """The weekday market brief for one session, as ingested from the agent.
    Keyed by the session it describes, so a re-run overwrites rather than
    duplicating — the agent is not the authority on how many briefs exist."""
    __tablename__ = "briefs"
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    body: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[list] = mapped_column(JSON, default=list)
    indexes: Mapped[list] = mapped_column(JSON, default=list)
    movers: Mapped[list] = mapped_column(JSON, default=list)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestExperiment(Base):
    """One backtest run's identity and narrative. `id` is the first 32 hex
    digits of sha256(spec, dataset_sha, engine_version) — deterministic, so a
    `rerun` on the same pinned dataset reproduces it exactly. `spec` is the
    validated, defaulted spec as the engine actually ran it; `assumed`,
    `caveats` and `exclusions` are what `describe(spec)` and the engine
    produced, stored verbatim so the experiment page never has to recompute
    them from the raw spec."""
    __tablename__ = "backtest_experiments"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    spec: Mapped[dict] = mapped_column(JSON, default=dict)
    description: Mapped[str] = mapped_column(Text, default="")
    assumed: Mapped[list] = mapped_column(JSON, default=list)
    caveats: Mapped[list] = mapped_column(JSON, default=list)
    exclusions: Mapped[list] = mapped_column(JSON, default=list)
    dataset_sha: Mapped[str] = mapped_column(String(64))
    engine_version: Mapped[str] = mapped_column(String(20), default="")
    caller: Mapped[str] = mapped_column(String(128), default="")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestDataset(Base):
    """The exact rows a run priced against, content-addressed by sha256 of
    the canonical (sorted, Decimal-as-string) JSON the tool built. Shared
    across experiments that happen to price the same symbols over the same
    span — the tool inserts one only if the sha is new. `rows_gz` is the
    gzip of that canonical JSON, so a `rerun` needs no re-fetch and no
    re-read of `bars`."""
    __tablename__ = "backtest_datasets"
    sha: Mapped[str] = mapped_column(String(64), primary_key=True)
    rows_gz: Mapped[bytes] = mapped_column(LargeBinary)
    symbols: Mapped[list] = mapped_column(JSON, default=list)
    day_from: Mapped[str] = mapped_column(String(10))
    day_to: Mapped[str] = mapped_column(String(10))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BacktestResult(Base):
    """One strategy's headline metrics within an experiment. An experiment
    with N strategies (a compare spec) has N rows here, one per
    `strategy_id`."""
    __tablename__ = "backtest_results"
    experiment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)


class BacktestSeries(Base):
    """One strategy's daily portfolio value, for the value-vs-contributed
    and drawdown charts. `contributed` is the cumulative cash put in as of
    that day (book cost), so the chart can show growth against money in
    without a second query."""
    __tablename__ = "backtest_series"
    experiment_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    strategy_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    contributed: Mapped[float] = mapped_column(Float, default=0.0)


class BacktestEvent(Base):
    """The decision log: one row per buy/sell/dividend/exclusion/fallback the
    engine recorded, for the pick timeline and the events endpoint. `symbol`
    is nullable — an all-cash day or a universe-empty fallback has none."""
    __tablename__ = "backtest_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(String(32))
    strategy_id: Mapped[str] = mapped_column(String(64))
    day: Mapped[str] = mapped_column(String(10))
    kind: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str | None] = mapped_column(String(12), nullable=True)
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (Index("ix_backtest_events_experiment_id", "experiment_id"),)


def make_engine(url: str | None = None):
    url = url or os.environ["APP_DB_URL"]
    kwargs = {}
    if url.startswith("postgresql"):
        kwargs["execution_options"] = {"schema_translate_map": {None: "app_stockmarket"}}
    return create_async_engine(url, **kwargs)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


# Columns added to a table that already existed before this change.
# `create_all` only creates missing *tables*, never adds columns to a live
# one, so an existing prod `bars`/`symbols` needs an explicit, additive
# migration. Postgres-only: on sqlite (tests, and the schema-translation
# arrangement doesn't apply there) `create_all` already produces the new
# shape on a fresh DB, and sqlite's ALTER TABLE can't do the BIGINT widening
# anyway.
_NEW_BAR_COLUMNS: tuple[str, ...] = (
    "close_split_adj", "adj_close", "dividend", "split_ratio")


def _postgres_column_migrations() -> list[str]:
    """The exact ALTER TABLE statements to run on postgres, compiled from the
    model's own column types (one definition of the schema, per the module
    docstring) against the postgresql dialect — so a type drifting out of
    sync with this list is caught by compiling it, not by hand-copying a
    string. `ADD COLUMN IF NOT EXISTS` and re-asserting the same column type
    are both no-ops on a column that is already there, so this list is safe
    to run on every boot and safe to run twice."""
    dialect = postgresql.dialect()
    stmts = [
        f"ALTER TABLE app_stockmarket.bars ADD COLUMN IF NOT EXISTS "
        f"{name} {Bar.__table__.c[name].type.compile(dialect=dialect)}"
        for name in _NEW_BAR_COLUMNS
    ]
    stmts.append(
        "ALTER TABLE app_stockmarket.bars ALTER COLUMN volume TYPE "
        f"{Bar.__table__.c.volume.type.compile(dialect=dialect)}")
    stmts.append(
        "ALTER TABLE app_stockmarket.symbols ADD COLUMN IF NOT EXISTS "
        f"currency {Symbol.__table__.c.currency.type.compile(dialect=dialect)}")
    return stmts


def _migrate_postgres(conn) -> None:
    if conn.dialect.name != "postgresql":
        return
    for stmt in _postgres_column_migrations():
        conn.exec_driver_sql(stmt)


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_postgres)


async def seed_indexes(sf) -> None:
    """Make sure the three indexes are tracked. Additive and idempotent: it
    never resets an index's status, so a restart doesn't order a pointless
    five-year re-backfill of data that is already loaded."""
    from sqlalchemy import select
    async with sf() as s:
        known = set((await s.execute(select(Symbol.symbol))).scalars())
        for symbol, label in INDEXES:
            if symbol not in known:
                s.add(Symbol(symbol=symbol, label=label, kind="index",
                             status="pending"))
        await s.commit()
