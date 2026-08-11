"""The activity log + weekly briefs, in the app's own schema (app_running).

Same arrangement as the stockmarket app: the app connects with its provisioned
role (secret app-running-db → env) and is confined to its schema by grant.
Models are schema-less; the engine maps them into app_running via
schema_translate_map, which also lets tests run on sqlite untranslated.

Unlike stockmarket, NO tool writes here — the app has no third-party egress and
the `strava` tool holds only its own OAuth cache, never these creds. Activities
arrive over Kafka (the `running` agent pulls them through the read-only strava
tool and emits them; this app is the only writer). One activity per Strava id,
so a re-sent activity corrects rather than duplicates.
"""
import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, BigInteger, Boolean, DateTime, Float, Integer,
                        String, Text)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Foot sports the running stats treat as "a run". Everything is stored, but
# pace/PR math only makes sense for these; rides et al. still show on the
# distance heatmap via their own type.
RUN_TYPES = {"Run", "TrailRun", "VirtualRun"}
FOOT_TYPES = RUN_TYPES | {"Walk", "Hike"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Activity(Base):
    """One Strava activity, keyed by its Strava id so a re-send overwrites. `day`
    is the local start date (YYYY-MM-DD) — the browse axis for the heatmap and
    weekly rollups, matching the stockmarket bars convention."""
    __tablename__ = "activities"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    day: Mapped[str] = mapped_column(String(10), index=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    type: Mapped[str] = mapped_column(String(32), default="Run", index=True)
    distance_m: Mapped[int] = mapped_column(Integer, default=0)
    moving_time_s: Mapped[int] = mapped_column(Integer, default=0)
    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_hr: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Brief(Base):
    """The weekly coach's note, keyed by the Monday of its ISO week. The app —
    not the agent — assigns the week (from its own clock at ingest), so a brief
    re-sent later the same week updates the text in place and never re-posts.
    `posted` guards the once-per-week Discord post + report write."""
    __tablename__ = "briefs"
    week_start: Mapped[str] = mapped_column(String(10), primary_key=True)
    body: Mapped[str] = mapped_column(Text, default="")
    highlights: Mapped[list] = mapped_column(JSON, default=list)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    distance_m: Mapped[int] = mapped_column(Integer, default=0)
    runs: Mapped[int] = mapped_column(Integer, default=0)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def make_engine(url: str | None = None):
    url = url or os.environ["APP_DB_URL"]
    kwargs = {}
    if url.startswith("postgresql"):
        kwargs["execution_options"] = {"schema_translate_map": {None: "app_running"}}
    return create_async_engine(url, **kwargs)


def make_session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_db(engine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
