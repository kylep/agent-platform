import uuid
from datetime import datetime, timezone
from enum import StrEnum
from sqlalchemy import JSON, DateTime, Index, Integer, LargeBinary, String, Text, UniqueConstraint, text
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
    # docs/design/13 D: the PRINCIPAL at the root of the chain — who this work
    # is ultimately being done for. requested_by is the immediate requester
    # (an agent, the scheduler, a job); initiated_by survives chaining: an
    # agent-invoked child inherits its parent's. Single-operator today, so
    # this is almost always "admin" — but the claim, the column, and the
    # audit trail are real from day one (family access is additive later).
    initiated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    # Prompt-cache tokens (docs/design/14): read = served from Anthropic's
    # prefix cache at ~10% price; creation = written to it this run. Previously
    # dropped by the recorder; now captured so cache health is observable.
    tokens_cache_read: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_creation: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)
    # Post-hoc metadata, set by the run-summarizer system agent (or an admin).
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    # Final assistant reply text, captured by the recorder from the terminal
    # `result` frame — used to build conversation history.
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Tool calls the CLI blocked during the run (from the result frame's
    # `permission_denials`). A non-empty list on a least-privilege agent is a
    # signal the agent tried something outside its allow-list.
    permission_denials: Mapped[list] = mapped_column(JSON, default=list)
    # Set when this run's conversation reply has been published. The reply text
    # (`result`) and the terminal state arrive on *different* Kafka topics, so
    # both consumers race to publish; this is the claim that makes it once.
    reply_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)

class ToolAudit(Base):
    """Append-only audit of custom-tool calls at the broker chokepoint
    (docs/design/13 E). args_digest is a sha256 of the canonical arguments —
    never the raw args, which may embed sensitive content. `decision` is
    allow | deny:<reason> | error:<kind>."""
    __tablename__ = "tool_audit"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    initiated_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tool: Mapped[str] = mapped_column(String(64), index=True)
    args_digest: Mapped[str] = mapped_column(String(64), default="")
    decision: Mapped[str] = mapped_column(String(64))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    result_bytes: Mapped[int] = mapped_column(Integer, default=0)


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
    tokens_cache_read: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_creation: Mapped[int] = mapped_column(Integer, default=0)

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
    # Claude CLI session resume (docs/design/14): the id + raw bytes of the
    # CLI's session .jsonl, stored OPAQUELY — never parsed or generated here.
    # Restored into the run pod so `claude --resume` continues the real session
    # (full fidelity + prompt-cache hits); empty/null = text-replay fallback.
    claude_session_id: Mapped[str] = mapped_column(String(64), default="")
    session_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

class AgentDef(Base):
    """An agent's IDENTITY (docs/design/15): the row that replaced
    `agents/<name>/{agent.md,manifest.yaml,entrypoints.yaml}`. Capability stays
    code (tools, skills, secret declarations, apps); what an agent *is* — its
    prompt, its config, its grants, its triggers — is this row, editable
    through the admin API/UI and the `agents_edit`/`agents_grant` tools. Every
    write appends an AgentVersion, which is the review surface git used to be.
    Grants are lists of names, not foreign keys: the referent lives in the repo
    (a skill dir, a secret dir, a tool dir), so validation-on-write against the
    code registries is the integrity check — see agentdefs.validate_def."""
    __tablename__ = "agent_defs"
    # The agent slug is the identity everywhere else (runs.agent, memories.agent,
    # `claude --agent <name>`), so it is the primary key rather than a surrogate.
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    # The former agent.md BODY: the agent's context/personality. The runner
    # materializes ~/.claude/agents/<name>.md from this.
    prompt: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str] = mapped_column(String(512), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    # Platform role the agent's tokens are minted at (see api.auth.ROLES);
    # `coder` is additionally what makes a run self-edit-capable.
    role: Mapped[str] = mapped_column(String(32), default="operator")
    # System agents are platform-internal (run-summarizer, health-monitor):
    # they get API access injected and are protected from deletion in the UI.
    system: Mapped[bool] = mapped_column(default=False)
    # Grants an operator-scoped per-run token so the agent can invoke agents.
    can_invoke: Mapped[bool] = mapped_column(default=False)
    concurrency: Mapped[int] = mapped_column(Integer, default=1)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=1800)
    # When set, the recorder publishes each successful run's result to this
    # Kafka topic — how an agent's output feeds an app (docs/design/11).
    result_topic: Mapped[str] = mapped_column(String(256), default="")
    # Per-agent transcript retention override (days). NULL = platform default;
    # <= 0 = keep forever.
    transcript_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Grants. harness_tools = Claude Code's own tools (WebFetch, Glob, …); the
    # sensitive set stays denied in the runner for non-self-edit runs no matter
    # what is stored here. platform_tools = mcp__platform__* names, which is
    # also what the design-12 role ladder now derives from (not frontmatter).
    harness_tools: Mapped[list] = mapped_column(JSON, default=list)
    platform_tools: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    secrets: Mapped[list] = mapped_column(JSON, default=list)
    # The former entrypoints.yaml: {"crons": [{"schedule", "prompt"}],
    # "webhooks": [{"path"}], "topics": [...], "timezone": ""}. One JSON blob
    # rather than child tables — it is read and written whole, always as part
    # of the definition, and the snapshot log wants it inline anyway.
    entrypoints: Mapped[dict] = mapped_column(JSON, default=dict)
    # Soft off-switch: a disabled agent keeps its definition and history but
    # takes no triggers. Deleting is the destructive option.
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class AgentVersion(Base):
    """Append-only change log for AgentDef (docs/design/15). Every write — API,
    UI, or tool — lands a FULL snapshot here, so an agent is restorable from
    its own history and every change is attributable. There is no
    pending/approval state: edits apply immediately and rollback is re-applying
    an old snapshot (which itself logs a new version)."""
    __tablename__ = "agent_versions"
    # next_version() is an unlocked read-then-write, so two concurrent writers
    # on the same agent (a UI save and an agents_edit tool call) would both
    # read max=4 and both insert 5 — two different snapshots wearing the same
    # version, and "roll back to 5" quietly stops meaning anything. The
    # constraint makes the loser fail loudly instead.
    __table_args__ = (UniqueConstraint("agent", "version",
                                       name="uq_agent_versions_agent_version"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    # Monotonic per agent, app-enforced (agentdefs.next_version). Not a DB
    # sequence: it is per-agent and must survive a delete/recreate of the def.
    version: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict] = mapped_column(JSON)
    # The VERIFIED principal (session name, api-key name, or the run's agent) —
    # never self-reported by the request payload.
    changed_by: Mapped[str] = mapped_column(String(128), default="")
    # admin | tool:agents_edit | tool:agents_grant | import | rollback
    changed_via: Mapped[str] = mapped_column(String(32), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class Memory(Base):
    __tablename__ = "memories"
    # "Overwrite on same key" is only real with a constraint behind it: two
    # concurrent saves of the same key would otherwise both insert and the
    # namespace silently holds duplicates. Partial unique (keyless memories are
    # append-only notes and may repeat). init_db backfills this on live DBs.
    # docs/design/12: the memory TOOL owns this data — on postgres the table
    # lives in the tool's provisioned schema (tool_memory). "memory_store" is a
    # sentinel translated per-dialect by make_engine (postgres → tool_memory,
    # sqlite → default), so the admin API/ORM and the sqlite test suite both
    # keep working while the storage is genuinely the tool's.
    __table_args__ = (
        Index("uq_memories_agent_key", "agent", "key", unique=True,
              postgresql_where=text("key IS NOT NULL"),
              sqlite_where=text("key IS NOT NULL")),
        {"schema": "memory_store"},
    )
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

class Report(Base):
    """A report instance: one dated HTML artifact of a git-declared report
    type (reports/<type>/report.yaml — see reportregistry). Identity is
    type/YYYY-MM-DD[/HH-MM]; a re-run of the same identity replaces the html
    (idempotent upsert). The html column stores the SANITIZED body fragment
    only — the viewer wraps it in the report-kit shell at render time.
    ISO date/time strings keep range queries lexicographic and sidestep
    NULL-in-unique-constraint semantics (time "" = a daily report)."""
    __tablename__ = "reports"
    __table_args__ = (UniqueConstraint("type", "date", "time", name="uq_reports_identity"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    type: Mapped[str] = mapped_column(String(128), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)   # YYYY-MM-DD
    time: Mapped[str] = mapped_column(String(5), default="")    # HH-MM or ""
    title: Mapped[str] = mapped_column(String(256), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)
    html: Mapped[str] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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

class ScheduledJob(Base):
    """A recurring task: run `agent` with `prompt` on a cron. Decouples the
    schedule from the agent (1:many — one agent can back many jobs, each with
    its own cron + prompt), unlike the manifest `schedule:` field. Created and
    managed from the UI; the scheduler fires it when due."""
    __tablename__ = "scheduled_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(128))
    agent: Mapped[str] = mapped_column(String(128), index=True)
    cron: Mapped[str] = mapped_column(String(128))
    # IANA zone the cron is read in; empty = UTC. Stored times stay UTC — this
    # only decides which UTC instant a wall-clock expression means, so a job
    # pinned to market open doesn't drift an hour across daylight saving.
    timezone: Mapped[str] = mapped_column(String(64), default="", server_default="")
    prompt: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    last_fire: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_fire: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

# (shared_news is gone: the news APP's items table is the dedup authority now
# — docs/design/11. The old table is backfilled into app_news.items at deploy
# and then dropped manually; create_all never drops.)

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

MEMORY_SCHEMA = "tool_memory"


def make_engine(db_url: str) -> AsyncEngine:
    engine = create_async_engine(db_url)
    # Resolve the Memory model's sentinel schema (see Memory.__table_args__).
    real = MEMORY_SCHEMA if engine.dialect.name == "postgresql" else None
    return engine.execution_options(schema_translate_map={"memory_store": real})


def _schema_of(conn, table) -> str | None:
    """The PHYSICAL schema of a model table on this connection (the inspector
    does not apply schema_translate_map, so raw-SQL helpers resolve it here)."""
    if table.schema == "memory_store":
        return MEMORY_SCHEMA if conn.dialect.name == "postgresql" else None
    return table.schema

def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)

def _ensure_columns(conn) -> None:
    """Minimal additive migration: create_all makes missing *tables* but never
    adds *columns* to an existing one. Add any model columns missing from a
    live table (portable ADD COLUMN — no `IF NOT EXISTS`, which sqlite lacks)."""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(conn)
    for table in Base.metadata.sorted_tables:
        schema = _schema_of(conn, table)
        if not insp.has_table(table.name, schema=schema):
            continue
        existing = {c["name"] for c in insp.get_columns(table.name, schema=schema)}
        for col in table.columns:
            if col.name not in existing:
                ddl = col.type.compile(dialect=conn.dialect)
                qualified = f'{schema}.{table.name}' if schema else table.name
                conn.exec_driver_sql(f'ALTER TABLE {qualified} ADD COLUMN {col.name} {ddl}')


def _ensure_memory_key_index(conn) -> None:
    """Backfill the (agent, key) partial unique index on a live DB: create_all
    never touches an existing table, so dedup first (keep the newest row per
    key — that's what "overwrite on save" always meant), then create the index.
    Idempotent via IF NOT EXISTS (supported by both sqlite and postgres)."""
    from sqlalchemy import inspect as sa_inspect
    insp = sa_inspect(conn)
    schema = _schema_of(conn, Memory.__table__)
    tbl = f"{schema}.memories" if schema else "memories"
    if not insp.has_table("memories", schema=schema):
        return
    if any(ix["name"] == "uq_memories_agent_key"
           for ix in insp.get_indexes("memories", schema=schema)):
        return
    dupes = conn.execute(text(
        f"SELECT agent, key FROM {tbl} WHERE key IS NOT NULL "
        "GROUP BY agent, key HAVING count(*) > 1")).fetchall()
    for agent, key in dupes:
        ids = [r[0] for r in conn.execute(text(
            f"SELECT id FROM {tbl} WHERE agent = :a AND key = :k "
            "ORDER BY updated_at DESC, id DESC"), {"a": agent, "k": key}).fetchall()]
        for stale in ids[1:]:
            conn.execute(text(f"DELETE FROM {tbl} WHERE id = :i"), {"i": stale})
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_memories_agent_key "
                      f"ON {tbl} (agent, key) WHERE key IS NOT NULL"))


async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            # The memory tool's schema must exist before create_all places the
            # memories table in it (the ToolProvisioner later grants the tool
            # role its privileges — creation order is API-first-safe).
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{MEMORY_SCHEMA}"'))
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_columns)
        await conn.run_sync(_ensure_memory_key_index)
