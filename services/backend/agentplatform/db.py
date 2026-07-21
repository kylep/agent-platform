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
    # Run-chain provenance for agent-invokes-agent. parent_run_id is the run
    # whose API token requested this one (null for human/schedule/webhook
    # triggers); depth is the chain length, used as a loop guard.
    parent_run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    # When this run is a turn in a conversation, the owning conversation id and
    # the raw user message for that turn (prompt holds the built context prompt).
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    user_message: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    # Post-hoc metadata, set by the run-summarizer system agent (or an admin).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Final assistant reply text, captured by the recorder from the terminal
    # `result` frame — used to build conversation history.
    result: Mapped[str | None] = mapped_column(Text, nullable=True)

class RunModelUsage(Base):
    """Per-(run, model) token usage, captured by the recorder from the run's
    terminal `modelUsage` frame. A run can use several models (main + subagents),
    so this is the grain for a by-model token breakdown."""
    __tablename__ = "run_model_usage"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    model: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)

class TranscriptEvent(Base):
    __tablename__ = "run_transcript_events"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)

class Conversation(Base):
    """A durable, multi-turn thread with an agent. Each turn is a Run
    (Run.conversation_id). Sourced from a connector (web/discord/slack); an
    external_ref binds it to the external channel (e.g. a Discord thread id)."""
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    connector: Mapped[str] = mapped_column(String(32))          # web | discord | slack
    external_ref: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    agent: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(256), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | closed
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class Memory(Base):
    __tablename__ = "memories"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    # Namespace: memories are private to one agent. All access is scoped to the
    # caller's agent (an agent can only see/write its own namespace).
    agent: Mapped[str] = mapped_column(String(128), index=True)
    # Optional short label; a save reusing a key overwrites (idempotent remember).
    key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class SecretAccess(Base):
    """Audit trail: which k8s secrets a run's pod was granted at launch (the
    base claude credential + the union of its manifest/skill secrets). One row
    per (run, secret)."""
    __tablename__ = "secret_access"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    run_id: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(128))
    secret: Mapped[str] = mapped_column(String(128))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

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

class Schedule(Base):
    __tablename__ = "schedules"
    agent: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_fire: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_fire: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class ApiKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    # Optional agent scope: keys minted for a specific agent (agent-invokes-
    # agent) carry the agent name; operator/human keys leave it null.
    agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Optional run scope: a per-run token minted for one run (the caller in an
    # agent-invokes-agent chain). Its run's depth authoritatively bounds the
    # chain, and the key is revoked when that run terminates.
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Only the hash and a display prefix are stored; the token is shown once.
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    prefix: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

def make_engine(db_url: str) -> AsyncEngine:
    return create_async_engine(db_url)

def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)

def _ensure_columns(conn) -> None:
    """Minimal additive migration: create_all makes missing *tables* but never
    adds *columns* to an existing one. Add any model columns missing from a
    live table (portable ADD COLUMN — no `IF NOT EXISTS`, which sqlite lacks)."""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(conn)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in existing:
                ddl = col.type.compile(dialect=conn.dialect)
                conn.exec_driver_sql(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl}')


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)
