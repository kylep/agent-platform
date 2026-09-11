import uuid
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from sqlalchemy import (JSON, DateTime, Index, Integer, LargeBinary, String, Text,
                        UniqueConstraint, select, text)
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
    # The relay message that caused this run (docs/design/19). The reply is
    # threaded under it and takes its hop + 1, so a chain of agents answering
    # each other is walkable — and boundable — from either end.
    trigger_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    """A CHANNEL (docs/design/19): a durable, multi-turn room whose messages are
    relay_messages and whose turns are Runs (Run.conversation_id). A `dm` is the
    original shape — one human, one agent in `agent` — and every pre-relay row
    is one; `channel`/`group` rooms hold many participants and leave `agent`
    null. connector/external_ref/claude_session_id/session_blob are the legacy
    single-agent fields, superseded by relay_bindings and relay_sessions and
    kept because the design-07/14 code paths still read them."""
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    connector: Mapped[str] = mapped_column(String(32))          # web | discord | slack
    external_ref: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    agent: Mapped[str | None] = mapped_column(String(128), nullable=True)
    kind: Mapped[str] = mapped_column(String(16), default="dm")   # dm | channel | group
    # Slug, channels only (`general`), unique among them — enforced by the
    # partial index _ensure_relay_ddl creates, since dms leave it null.
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    topic: Mapped[str] = mapped_column(String(256), default="")
    # An open channel has no participant rows: every enabled agent and every
    # human is a member of it by definition.
    open: Mapped[bool] = mapped_column(default=False)
    # The canonical identity of a DM: its two participant strings, sorted,
    # joined by "|". A DM is not created, it is RESOLVED — asking for the same
    # pair twice must return one room — and a lookup-then-insert forks under
    # concurrency, so the uniqueness lives in an index (_ensure_relay_ddl) and
    # this column is what that index is on. Null on channels and groups, and on
    # a legacy DM whose pair was already claimed by an older row.
    dm_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
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

class RelayParticipant(Base):
    """Explicit membership of a dm or group channel. Open channels carry no
    rows at all — membership there is implicit — so absence of rows is a real
    answer, not missing data."""
    __tablename__ = "relay_participants"
    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    # 'agent:<name>' | 'user:<principal>' | 'discord:<snowflake>'. A string, not
    # a foreign key: the same identity seam as Run.initiated_by (design 13), and
    # a participant may live outside this platform entirely.
    participant: Mapped[str] = mapped_column(String(128), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="member")   # member | owner
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RelayMessage(Base):
    """One message in a channel: the unit of history, of the `relay.messages`
    event, and of the loop guard. `hop` is what bounds agent-to-agent chatter —
    a human or system message is 0 and a run triggered by a message at h posts
    its reply at h+1 — and trigger_message_id is the message that caused it,
    so a chain is walkable in both directions."""
    __tablename__ = "relay_messages"
    __table_args__ = (Index("ix_relay_messages_channel_created", "channel_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    channel_id: Mapped[str] = mapped_column(String(32))
    author: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="text")   # text | system | event
    body: Mapped[str] = mapped_column(Text, default="")
    # Structured payload for kind=event (a run card, an alert) — the UI renders
    # it instead of the body, which stays the plain-text fallback.
    card: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    reply_to: Mapped[str | None] = mapped_column(String(32), nullable=True)
    thread_root: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    # The run that authored this message (agents only) and the message that
    # triggered that run.
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    trigger_message_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hop: Mapped[int] = mapped_column(Integer, default=0)
    # Agent names the router resolved from the body's @mentions, not the raw
    # tokens: routing reads this, never the text.
    mentions: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft delete: a deleted message keeps its id so replies and threads that
    # point at it still resolve.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RelayReaction(Base):
    __tablename__ = "relay_reactions"
    message_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    participant: Mapped[str] = mapped_column(String(128), primary_key=True)
    emoji: Mapped[str] = mapped_column(String(32), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RelaySession(Base):
    """The design-14 resume blob, generalised to (channel, agent): a channel can
    hold several agents and each keeps its own CLI session. Stored opaquely,
    exactly as Conversation.claude_session_id/session_blob were."""
    __tablename__ = "relay_sessions"
    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent: Mapped[str] = mapped_column(String(128), primary_key=True)
    claude_session_id: Mapped[str] = mapped_column(String(64), default="")
    session_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class RelayBinding(Base):
    """Ties a channel to a room on an external network. The unique
    (connector, external_ref) is what makes inbound routing unambiguous: one
    Discord thread can only ever resolve to one channel."""
    __tablename__ = "relay_bindings"
    __table_args__ = (UniqueConstraint("connector", "external_ref",
                                       name="uq_relay_bindings_external"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    connector: Mapped[str] = mapped_column(String(32))    # discord | slack | telegram
    external_ref: Mapped[str] = mapped_column(String(256))
    config: Mapped[dict] = mapped_column(JSON, default=dict)


class RelayWake(Base):
    """The coalesced "someone mentioned you while you were busy" marker: at most
    one per (channel, agent), pointing at the oldest message the agent has not
    seen, so a flurry of mentions during a run becomes one follow-up."""
    __tablename__ = "relay_wakes"
    channel_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    agent: Mapped[str] = mapped_column(String(128), primary_key=True)
    since_message_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RelayInvocation(Base):
    """Every routing decision, including every suppression — the record that
    answers "why did nothing happen when I mentioned it?". Mirrored from the
    `relay.invocations` topic."""
    __tablename__ = "relay_invocations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    channel_id: Mapped[str] = mapped_column(String(32), index=True)
    message_id: Mapped[str] = mapped_column(String(32), index=True)
    agent: Mapped[str] = mapped_column(String(128), index=True)
    decision: Mapped[str] = mapped_column(String(16))     # invoked | suppressed
    reason: Mapped[str] = mapped_column(String(64), default="")
    run_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    hop: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SchemaMark(Base):
    """One-time data migrations record themselves here. create_all and
    _ensure_columns are naturally idempotent; a BACKFILL is not — it has to know
    it already ran, or it re-scans every source table on every boot forever and
    (worse) re-derives rows the live code has since become the owner of."""
    __tablename__ = "schema_marks"
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


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
    # The agent's face in Relay (docs/design/19): one emoji, optional — unset
    # means the UI derives a stable one from the name.
    icon: Mapped[str | None] = mapped_column(String(16), nullable=True)
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
    # admin | tool:agents_edit | tool:agents_grant | import | rollback, and
    # `delete:<one of those>` for the tombstone a deletion files — the snapshot
    # is the definition as it stood, so the log alone can say who removed an
    # agent and recreate it.
    changed_via: Mapped[str] = mapped_column(String(32), default="admin")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class WebhookSecret(Base):
    """The shared secret guarding one declared webhook path (docs/design/16).

    Deliberately NOT a field of the definition. `agent_versions` snapshots the
    whole definition on every write, so a secret stored there — even hashed —
    would be sprayed across the change log and a rollback would silently
    resurrect a rotated one. The definition carries only the MODE
    (`entrypoints.webhooks[].auth`), which is exactly what should be
    versioned and rollbackable; the value lives here, write-only, and is
    deleted with its agent.

    Only a salted digest is stored, with a per-row random salt: the platform
    never needs to read a webhook secret back, only to recognize one.
    """
    __tablename__ = "webhook_secrets"
    # (agent, path) is the identity — the same pair the ingress resolves before
    # comparing, so one agent's secret can never authenticate another's path.
    agent: Mapped[str] = mapped_column(String(128), primary_key=True)
    path: Mapped[str] = mapped_column(String(256), primary_key=True)
    salt: Mapped[str] = mapped_column(String(64))
    secret_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


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
    its own cron + prompt), unlike an agent's own declared entrypoint crons.
    Created and managed from the UI; the scheduler fires it when due."""
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


# (name, topic) of the rooms the platform ships with — open to every agent and
# every human, seeded once and thereafter editable like any other channel.
RELAY_BACKFILL_MARK = "relay-backfill-v1"
RELAY_DM_KEY_MARK = "relay-dm-keys-v1"


def dm_key_of(participants) -> str:
    """The canonical `Conversation.dm_key` for a pair of participant strings.
    Order-free by construction: the DM between a and b is the DM between b
    and a."""
    return "|".join(sorted(participants))

RELAY_SEED_CHANNELS = (
    ("general", "everyone"),
    ("ops", "alerts and operations"),
    ("standup", "what did you do today?"),
)


def _ensure_relay_ddl(conn) -> None:
    """Schema the model declarations cannot express portably: a channel's name
    is unique only among channels (dms leave it null, and several nulls are not
    a conflict), postgres wants a full-text index sqlite has no equivalent for,
    and a live conversations.agent is still NOT NULL from when every
    conversation had exactly one agent."""
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_channel_name "
                      "ON conversations (name) WHERE kind = 'channel'"))
    # Get-or-create made atomic: two requests racing for the same pair both
    # insert, and the loser is told so by this index instead of forking the DM.
    conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_conversations_dm_key "
                      "ON conversations (dm_key) "
                      "WHERE kind = 'dm' AND dm_key IS NOT NULL"))
    if conn.dialect.name == "postgresql":
        # DROP NOT NULL takes an ACCESS EXCLUSIVE lock even when the column is
        # already nullable, and this runs on every boot of every service — so
        # ask first, the way _ensure_memory_key_index does.
        if conn.execute(text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'conversations' AND column_name = 'agent'"
        )).scalar() == "NO":
            conn.exec_driver_sql("ALTER TABLE conversations ALTER COLUMN agent DROP NOT NULL")
        conn.exec_driver_sql("CREATE INDEX IF NOT EXISTS ix_relay_messages_body_fts "
                             "ON relay_messages USING GIN (to_tsvector('english', body))")


def _relay_human_of(conv, run) -> str:
    """The human behind ONE historical turn. A connector turn records
    `connector:<name>:<user>` in requested_by, and that external user IS the
    participant identity on a bridged channel — there is no platform principal
    behind it. A web turn carries the real principal in initiated_by. Derived
    per turn, not per conversation: a bridged channel is a room, and two people
    posting in it must not collapse into whoever spoke first."""
    requested = (run.requested_by if run is not None else "") or ""
    if conv.connector != "web" and requested.startswith("connector:"):
        network, _, user = requested[len("connector:"):].partition(":")
        if network and user:
            return f"{network}:{user}"
    initiated = (run.initiated_by if run is not None else None) or "admin"
    return f"user:{initiated}"


def _ensure_relay_backfill(conn) -> None:
    """Migrate the pre-relay world into the relay tables (docs/design/19): every
    conversation is already a dm channel, so give it its participants, replay its
    turns as messages, and move its resume blob and connector ref into the tables
    that now own them. A ONE-TIME migration, gated on its schema mark — after it,
    live code (not this function) owns every new message, so a second pass would
    be re-deriving rows it no longer has authority over. The seeded channels sit
    outside the mark: three lookups by name, and a deleted #general should not
    come back only to the next fresh database."""
    from sqlalchemy import inspect as sa_inspect
    if not sa_inspect(conn).has_table("conversations"):
        return
    conv_t, run_t = Conversation.__table__, Run.__table__
    msg_t, part_t = RelayMessage.__table__, RelayParticipant.__table__
    sess_t, bind_t = RelaySession.__table__, RelayBinding.__table__
    for name, topic in RELAY_SEED_CHANNELS:
        if conn.execute(select(conv_t.c.id).where(conv_t.c.kind == "channel",
                                                  conv_t.c.name == name)).first():
            continue
        conn.execute(conv_t.insert().values(
            id=uuid.uuid4().hex, connector="web", external_ref=None, agent=None,
            kind="channel", name=name, topic=topic, open=True, archived_at=None,
            title=f"#{name}", status="active", claude_session_id="", session_blob=None,
            created_at=utcnow(), updated_at=utcnow()))

    mark_t = SchemaMark.__table__
    if conn.execute(select(mark_t.c.name)
                    .where(mark_t.c.name == RELAY_BACKFILL_MARK)).first():
        return
    # _ensure_columns adds a column but cannot give existing rows its default.
    conn.execute(text("UPDATE conversations SET kind = 'dm' WHERE kind IS NULL"))
    conn.execute(text("UPDATE conversations SET topic = '' WHERE topic IS NULL"))
    conn.execute(conv_t.update().where(conv_t.c.open.is_(None)).values(open=False))

    have_messages = {r[0] for r in conn.execute(select(msg_t.c.channel_id).distinct())}
    have_parts = {(r[0], r[1]) for r in
                  conn.execute(select(part_t.c.channel_id, part_t.c.participant))}
    have_sessions = {(r[0], r[1]) for r in
                     conn.execute(select(sess_t.c.channel_id, sess_t.c.agent))}
    have_bindings = {(r[0], r[1]) for r in
                     conn.execute(select(bind_t.c.connector, bind_t.c.external_ref))}
    # Oldest first: nothing stops two live conversations from sharing an
    # external_ref (the ingestor's lookup-then-insert can race), but only one may
    # hold the binding — the original, which is the one the bridge was really
    # talking to. Claiming it here rather than letting the unique constraint
    # decide keeps a boot from dying on data that is merely untidy.
    for conv in conn.execute(select(conv_t).where(conv_t.c.kind == "dm")
                             .order_by(conv_t.c.created_at, conv_t.c.id)).fetchall():
        runs = conn.execute(select(run_t).where(run_t.c.conversation_id == conv.id)
                            .order_by(run_t.c.created_at, run_t.c.id)).fetchall()
        agent_part = f"agent:{conv.agent}" if conv.agent else None
        if conv.agent and (conv.claude_session_id or conv.session_blob) \
                and (conv.id, conv.agent) not in have_sessions:
            conn.execute(sess_t.insert().values(
                channel_id=conv.id, agent=conv.agent, updated_at=utcnow(),
                claude_session_id=conv.claude_session_id or "",
                session_blob=conv.session_blob))
            have_sessions.add((conv.id, conv.agent))
        if conv.connector != "web" and conv.external_ref \
                and (conv.connector, conv.external_ref) not in have_bindings:
            conn.execute(bind_t.insert().values(
                id=uuid.uuid4().hex, channel_id=conv.id, connector=conv.connector,
                external_ref=conv.external_ref, config={}))
            have_bindings.add((conv.connector, conv.external_ref))
        humans = [_relay_human_of(conv, run) for run in runs] or [_relay_human_of(conv, None)]
        for participant in dict.fromkeys(humans + [agent_part]):
            if participant and (conv.id, participant) not in have_parts:
                conn.execute(part_t.insert().values(
                    channel_id=conv.id, participant=participant, role="member",
                    joined_at=conv.created_at or utcnow()))
                have_parts.add((conv.id, participant))
        if conv.id in have_messages:
            continue
        for run, human in zip(runs, humans):
            asked = run.created_at or conv.created_at or utcnow()
            if run.user_message:
                conn.execute(msg_t.insert().values(_relay_message(
                    conv.id, human, run.user_message, asked)))
            if run.result is not None:
                # Messages are read in created_at order, so a reply that landed
                # in the same instant as its prompt must still sort after it.
                answered = run.finished_at or asked
                if answered <= asked:
                    answered = asked + timedelta(microseconds=1)
                conn.execute(msg_t.insert().values(_relay_message(
                    conv.id, agent_part or human, run.result, answered, run_id=run.id)))
    conn.execute(mark_t.insert().values(name=RELAY_BACKFILL_MARK, applied_at=utcnow()))


def _ensure_dm_keys(conn) -> None:
    """Give every existing two-party DM its canonical key. Runs after the relay
    backfill, which is what put the participant rows there in the first place.
    One-time and marked, like that backfill: from here on live code sets the
    key on every DM it creates, and a second pass would be re-deriving rows it
    no longer owns."""
    from sqlalchemy import inspect as sa_inspect
    if not sa_inspect(conn).has_table("conversations"):
        return
    mark_t = SchemaMark.__table__
    if conn.execute(select(mark_t.c.name)
                    .where(mark_t.c.name == RELAY_DM_KEY_MARK)).first():
        return
    conv_t, part_t = Conversation.__table__, RelayParticipant.__table__
    members: dict[str, list[str]] = {}
    for cid, participant in conn.execute(select(part_t.c.channel_id,
                                                part_t.c.participant)):
        members.setdefault(cid, []).append(participant)
    taken = {r[0] for r in conn.execute(
        select(conv_t.c.dm_key).where(conv_t.c.dm_key.isnot(None)))}
    # Oldest first, for the same reason the binding backfill claims in that
    # order: where two rows hold the same pair, the original is the real DM and
    # the duplicate stays keyless rather than failing the boot.
    for conv in conn.execute(select(conv_t.c.id).where(
            conv_t.c.kind == "dm", conv_t.c.dm_key.is_(None))
            .order_by(conv_t.c.created_at, conv_t.c.id)).fetchall():
        pair = members.get(conv.id, [])
        if len(pair) != 2:
            continue
        key = dm_key_of(pair)
        if key in taken:
            continue
        conn.execute(conv_t.update().where(conv_t.c.id == conv.id).values(dm_key=key))
        taken.add(key)
    conn.execute(mark_t.insert().values(name=RELAY_DM_KEY_MARK, applied_at=utcnow()))


def _relay_message(channel_id, author, body, created_at, run_id=None) -> dict:
    """A replayed historical message: plain text at hop 0, with none of the
    threading a live message would carry."""
    return dict(id=uuid.uuid4().hex, channel_id=channel_id, author=author, kind="text",
                body=body, card=None, reply_to=None, thread_root=None, run_id=run_id,
                trigger_message_id=None, hop=0, mentions=[], created_at=created_at,
                edited_at=None, deleted_at=None)


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
        await conn.run_sync(_ensure_relay_ddl)
        await conn.run_sync(_ensure_relay_backfill)
        await conn.run_sync(_ensure_dm_keys)
