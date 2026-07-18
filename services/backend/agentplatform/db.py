import uuid
from datetime import datetime, timezone
from enum import StrEnum
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class RunState(StrEnum):
    QUEUED = "queued"; DISPATCHED = "dispatched"; RUNNING = "running"
    SUCCEEDED = "succeeded"; FAILED = "failed"; TIMED_OUT = "timed_out"
    KILLED = "killed"; REJECTED = "rejected"; DLQ = "dlq"

ACTIVE_STATES = (RunState.QUEUED, RunState.DISPATCHED, RunState.RUNNING)

class Base(DeclarativeBase): pass

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    agent: Mapped[str] = mapped_column(String(128))
    trigger: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default=RunState.QUEUED)
    prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)

class TranscriptEvent(Base):
    __tablename__ = "run_transcript_events"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)

class Principal(Base):
    __tablename__ = "principals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)

class SecretMeta(Base):
    __tablename__ = "secrets_meta"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="missing")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

def make_engine(db_url: str) -> AsyncEngine:
    return create_async_engine(db_url)

def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)

async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
