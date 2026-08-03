"""The news archive: topics and items in the app's own schema (app_news).

The app connects with its provisioned role (secret app-news-db → env) and is
confined to its schema by grant. Models are schema-less here; the engine maps
them into app_news via schema_translate_map, which also lets tests run on
sqlite untranslated."""
import os
import uuid
from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, ForeignKey, Integer, String, Text)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Topic(Base):
    """A tag lane (ai, business, …). Sections in the gatherer's digest map
    here; unknown sections auto-create a topic rather than losing the tag."""
    __tablename__ = "topics"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True)
    label: Mapped[str] = mapped_column(String(128))
    # Design-token chart color index (1-8) — the UI renders topic accents with
    # var(--ds-chart-N), never raw color values.
    color: Mapped[int] = mapped_column(Integer, default=1)


class Item(Base):
    """One news story. `day` (ISO date, the gathering day) and `topic_id` are
    the browse axes; `dedup_hash` (canonicalized URL) is the platform-wide
    dedup authority — the successor of the old shared_news table."""
    __tablename__ = "items"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(512), default="")
    source: Mapped[str] = mapped_column(String(128), default="")
    summary: Mapped[str] = mapped_column(Text, default="")
    topic_id: Mapped[int] = mapped_column(ForeignKey("topics.id"), index=True)
    day: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dedup_hash: Mapped[str] = mapped_column(String(512), unique=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(url: str | None = None):
    url = url or os.environ["APP_DB_URL"]
    kwargs = {}
    if url.startswith("postgresql"):
        kwargs["execution_options"] = {"schema_translate_map": {None: "app_news"}}
    return create_async_engine(url, **kwargs)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
