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

from sqlalchemy import (JSON, DateTime, Float, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

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
    kind: Mapped[str] = mapped_column(String(8), default="watch")   # index | watch
    status: Mapped[str] = mapped_column(String(8), default="pending", index=True)
    error: Mapped[str] = mapped_column(String(500), default="")
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Bar(Base):
    """One daily OHLCV bar. Written by the `prices` tool, never by the app —
    the app has no third-party egress and could not fetch these if it wanted
    to. `day` is an ISO date string, matching the news app's browse-axis
    convention and keeping sqlite tests honest."""
    __tablename__ = "bars"
    symbol: Mapped[str] = mapped_column(String(12), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)
    open: Mapped[float | None] = mapped_column(Float, nullable=True)
    high: Mapped[float | None] = mapped_column(Float, nullable=True)
    low: Mapped[float | None] = mapped_column(Float, nullable=True)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[int | None] = mapped_column(Integer, nullable=True)


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


def make_engine(url: str | None = None):
    url = url or os.environ["APP_DB_URL"]
    kwargs = {}
    if url.startswith("postgresql"):
        kwargs["execution_options"] = {"schema_translate_map": {None: "app_stockmarket"}}
    return create_async_engine(url, **kwargs)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
