"""Response models for the REST API.

These exist so the OpenAPI spec carries response schemas, which is what lets the
SDK (`sdk/`) be *generated* from the spec with typed return values rather than
hand-maintained. FastAPI serializes each handler's return **through** its
`response_model`, so every field a handler returns MUST be declared here or it
is silently dropped from the response — the backend test-suite asserts on these
fields and is the guard against that.

Loose/dynamic payloads (raw transcript frames, upstream GitHub merge results)
are intentionally left unmodeled at the call site (return `dict`/`list`) — they
generate as untyped, which is honest for genuinely free-form data.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentplatform import agentdefs, webhooksecrets


# --- shared action results ---------------------------------------------------

class Ok(BaseModel):
    ok: bool = True


class OkId(Ok):
    id: str


class OkIdState(Ok):
    id: str
    state: str


class PruneResult(Ok):
    deleted: int


# --- agents (docs/design/15) --------------------------------------------------
# An agent definition is a row, and these are its shapes at the API edge. They
# mirror `agentdefs.DEF_FIELDS` field for field (a test pins that) instead of
# reusing `AgentDefModel`, because the edge needs two things the domain model
# must not have:
#
#   INPUTS reject unknown fields — a typo'd field has to 422, not silently
#   no-op on a full-replacement PUT;
#   OUTPUTS carry no validators — a QUARANTINED row (one that stopped
#   validating) must still render, because reading it is how you fix it.
#
# The nested entrypoint shapes are the exception: the input ones subclass the
# domain models so the cron/timezone validation is written once, while the
# output ones are plain mirrors for the same
# a-broken-row-must-still-be-readable reason.

class CronEntryIn(agentdefs.CronEntry):
    model_config = ConfigDict(extra="forbid")


class WebhookEntryIn(agentdefs.WebhookEntry):
    model_config = ConfigDict(extra="forbid")
    # DERIVED, and declared here only so it can be sent back. The editor works
    # read-modify-write: it GETs a definition (whose webhook entries carry
    # `secret_set`) and PUTs it whole, and `extra="forbid"` would 422 the
    # round trip. `AgentDefModel` — the domain model the stored value is built
    # from — does not have the field, so it is dropped on the way to the
    # column: what the UI echoes back can never become part of the definition.
    # Never an input in the real sense; the secret endpoints are the only way
    # to change what it reports.
    secret_set: bool = False


class EntrypointsIn(agentdefs.EntrypointsModel):
    model_config = ConfigDict(extra="forbid")
    crons: list[CronEntryIn] = []
    webhooks: list[WebhookEntryIn] = []


class AgentDefIn(BaseModel):
    """A complete agent definition on the wire. PUT replaces the whole
    definition, so an omitted field RESETS to the default shown here rather
    than keeping whatever the row had."""
    model_config = ConfigDict(extra="forbid")
    # Ignored on update: the path identifies the agent, so a payload can never
    # rename or retarget one.
    name: str = ""
    prompt: str = ""
    description: str = ""
    model: str = ""
    role: str = "operator"
    system: bool = False
    can_invoke: bool = False
    concurrency: int = 1
    timeout_seconds: int = 1800
    result_topic: str = ""
    transcript_retention_days: int | None = None
    harness_tools: list[str] = []
    platform_tools: list[str] = []
    skills: list[str] = []
    secrets: list[str] = []
    entrypoints: EntrypointsIn = EntrypointsIn()
    enabled: bool = True


class AgentCreateIn(AgentDefIn):
    """Create/import payload — same definition, but the name is the one thing
    that cannot be defaulted, plus the one knob that is about the write rather
    than about the agent."""
    name: str
    # The Relay default grant (docs/design/19), tri-state and deliberately NOT
    # part of the definition: None follows `settings.relay_default_grant`,
    # False is the explicit opt-out, True asks for the grant whatever the
    # setting says. A field rather than a sentinel value inside
    # `platform_tools`, because "do not give me the platform default" is not
    # something an empty list can say — an empty list is also what a caller
    # who simply wants no grants of their own sends. Absent from AgentDefIn:
    # a PUT already replaces `platform_tools` wholesale, so removing the grant
    # there is just sending the list without it.
    relay: bool | None = None


class AgentDefOut(BaseModel):
    """An agent definition as the API returns it."""
    name: str
    prompt: str = ""
    description: str = ""
    model: str = ""
    role: str = "operator"
    system: bool = False
    can_invoke: bool = False
    concurrency: int = 1
    timeout_seconds: int = 1800
    result_topic: str = ""
    transcript_retention_days: int | None = None
    harness_tools: list[str] = []
    platform_tools: list[str] = []
    skills: list[str] = []
    secrets: list[str] = []
    # The column verbatim, NOT a typed mirror. Tolerance on the way out is only
    # worth anything if it goes all the way down: a row whose entrypoints blob
    # has the wrong SHAPE (raw SQL, a foreign dump, a bad migration) would 500
    # the very GET you need to see the damage, and the editor's PUT — which
    # replaces the blob wholesale — is exactly the repair. The write side is
    # still strict: `AgentDefIn.entrypoints` is the validated shape.
    entrypoints: dict = {}
    enabled: bool = True


class AgentSummary(AgentDefOut):
    """A listing row: the stored definition plus the readiness only the
    platform can derive — deliberately not columns, because they are computed
    from secrets and validation, not declared."""
    quarantined: bool = False
    error: str | None = None
    # Blocked — unmet required secret dependency (docs/design/10). Recoverable
    # by fixing the SECRET; quarantined is recoverable by fixing the agent.
    blocked: bool = False
    blocked_reason: str | None = None
    # Pre-rendered cron summary for the listing (the entrypoints carry the raw
    # expressions).
    schedule: str = ""


class AgentVersionRow(BaseModel):
    """One entry of the append-only change log, without its snapshot."""
    version: int
    changed_by: str
    changed_via: str
    created_at: str | None


class AgentVersionDetail(AgentVersionRow):
    # The whole definition as it stood — what a rollback re-applies.
    snapshot: dict


class AgentImportResult(BaseModel):
    name: str
    status: str          # created | updated | unchanged


class WebhookSecretIn(BaseModel):
    """Setting/rotating one webhook's shared secret (docs/design/16). Its own
    endpoint rather than a definition field, because the value must never take
    the definition's path through `agent_versions`. Write-only: no response
    model carries it back, and nothing reads it out again."""
    model_config = ConfigDict(extra="forbid")
    # Bounded at the edge rather than in the handler so the limits reach the
    # OpenAPI spec, and through it the generated SDK — a caller shouldn't have
    # to POST a bad secret to discover the range.
    secret: str = Field(min_length=webhooksecrets.MIN_SECRET_LENGTH,
                        max_length=webhooksecrets.MAX_SECRET_LENGTH)


class WebhookSecretState(Ok):
    """What a secret write reports: the path it touched and whether one is now
    set. Never the secret."""
    agent: str
    path: str
    secret_set: bool


class ModelOption(BaseModel):
    id: str
    label: str


class AgentModels(BaseModel):
    models: list[ModelOption]


class PrRef(BaseModel):
    number: int
    url: str


class EditResult(BaseModel):
    tier: int
    branch: str | None
    sha: str | None
    changes: list[str]
    pr: PrRef | None


class EditDispatch(BaseModel):
    id: str
    state: str
    target_agent: str


# --- runs --------------------------------------------------------------------

class RunSummary(BaseModel):
    id: str
    agent: str
    state: str
    trigger: str
    created_at: str | None
    summary: str | None
    tags: list[str]


class RunDetail(RunSummary):
    prompt: str
    exit_code: int | None
    error: str | None
    tokens_in: int
    tokens_out: int
    tool_calls: int
    secrets_granted: list[str]
    permission_denials: list[dict]
    parent_run_id: str | None
    depth: int
    requested_by: str
    initiated_by: str | None = None
    started_at: str | None
    finished_at: str | None


class RunAccepted(BaseModel):
    id: str
    state: str


# --- api keys ----------------------------------------------------------------

class ApiKeyView(BaseModel):
    id: str
    name: str
    role: str
    agent: str | None
    prefix: str
    created_at: datetime | None
    revoked_at: datetime | None


class ApiKeyCreated(ApiKeyView):
    token: str


# --- apps --------------------------------------------------------------------

class AppView(BaseModel):
    name: str
    description: str
    icon: str
    ui: bool
    api: bool
    postgres: bool
    kafka_topics: list[str]
    redis: bool
    agent_key_role: str | None
    error: str | None
    # Deployment ap-app-<name> readiness; None = unknown (no k8s / not deployed)
    ready: bool | None
    ready_replicas: int


# --- audit -------------------------------------------------------------------

class SecretAccessView(BaseModel):
    id: str
    run_id: str
    agent: str
    secret: str
    granted_at: str | None


# --- conversations -----------------------------------------------------------

class Connector(BaseModel):
    name: str
    kind: str
    implemented: bool
    secrets: list[str]
    description: str


class ConversationView(BaseModel):
    id: str
    connector: str
    external_ref: str | None
    agent: str
    title: str
    status: str
    created_at: str | None
    updated_at: str | None


class ConversationTurn(BaseModel):
    run_id: str
    user_message: str | None
    result: str | None
    state: str
    sender: str = "unknown"
    created_at: str | None


class ConversationDetail(ConversationView):
    turns: list[ConversationTurn]


class MessageAccepted(BaseModel):
    run_id: str


# --- dlq ---------------------------------------------------------------------

class DlqEntry(BaseModel):
    id: str
    agent: str
    trigger: str
    error: str | None
    created_at: str | None
    finished_at: str | None


# --- health ------------------------------------------------------------------

class Backlog(BaseModel):
    queued: int
    active: int
    dlq: int


class KafkaHealth(BaseModel):
    reachable: bool
    topics: list[str]
    missing_topics: list[str]
    lag: int | None
    error: str | None
    backlog: Backlog


class ToolAuditView(BaseModel):
    id: str
    ts: str | None
    run_id: str | None
    agent: str
    initiated_by: str | None
    tool: str
    args_digest: str
    decision: str
    latency_ms: int
    result_bytes: int


class ToolMetrics(BaseModel):
    tool: str
    calls: int
    denials: int
    errors: int
    avg_latency_ms: float


# --- help --------------------------------------------------------------------

class HelpTopic(BaseModel):
    slug: str
    title: str


class HelpTopicDetail(HelpTopic):
    markdown: str     # the docs/building-blocks page, verbatim


class ToolHelp(BaseModel):
    name: str
    kind: str         # claude | platform
    description: str
    # Always denied by the runner for non-self-edit agents (trifecta break) —
    # checking it on a normal agent does nothing.
    sensitive: bool
    # Friendlier label for pickers/Help when the harness-fixed id is awkward
    # (e.g. TodoWrite → "Todo"). The id in `name` is what manifests declare.
    display_name: str | None = None


# --- integrations ------------------------------------------------------------

class Integration(BaseModel):
    name: str
    kind: str
    secrets: list[str]
    configured: bool
    status: str
    detail: str


# --- jobs --------------------------------------------------------------------

class JobView(BaseModel):
    id: str
    name: str
    # Exactly one of these is set: a job either runs an agent or posts into a
    # Relay channel (docs/design/19). `agent` stays first and keeps its name —
    # every existing caller reads it, and a relay job simply has none.
    agent: str | None
    relay_channel: str | None = None
    cron: str
    timezone: str = ""          # IANA zone the cron is read in; empty = UTC
    prompt: str
    enabled: bool
    last_fire: str | None
    next_fire: str | None


class JobRunAccepted(BaseModel):
    """What Run Now created: a run id for an agent job, a MESSAGE id for a relay
    job. One field because the caller's next move is the same either way — show
    the thing it just started — and `relay_channel` says which kind it is."""
    id: str
    agent: str | None
    relay_channel: str | None = None


# --- maintenance -------------------------------------------------------------

class Retention(BaseModel):
    default_days: int
    per_agent_days: dict[str, int]


# --- memory ------------------------------------------------------------------

class MemoryView(BaseModel):
    id: str
    agent: str
    key: str | None
    content: str
    tags: list[str]
    created_at: str | None
    updated_at: str | None


# --- metrics -----------------------------------------------------------------

class _Agg(BaseModel):
    total: int
    by_state: dict[str, int]
    active: int
    succeeded: int
    success_rate: float | None
    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    tokens_cache_creation: int
    tool_calls: int
    avg_duration_seconds: float | None
    max_duration_seconds: float | None
    last_run_at: str | None


class MetricsOverview(_Agg):
    runs_24h: int
    runs_7d: int
    dlq: int
    window: int


class AgentMetrics(_Agg):
    agent: str
    failure_streak: int
    last_failed_at: str | None = None


class RunDurationPoint(BaseModel):
    run_id: str
    agent: str
    state: str
    finished_at: str
    seconds: float


class ModelUsage(BaseModel):
    model: str
    runs: int
    tokens_in: int
    tokens_out: int
    tokens_cache_read: int
    tokens_cache_creation: int


class SessionBlob(BaseModel):
    session_id: str
    blob_b64: str


# --- pull requests -----------------------------------------------------------

class PullRequest(BaseModel):
    number: int
    title: str
    url: str
    branch: str
    author: str
    created_at: str


class PullRequestFile(BaseModel):
    filename: str
    status: str
    additions: int
    deletions: int
    patch: str | None


class MergeResult(BaseModel):
    merged: bool
    sha: str | None


class ChangeImpactItem(BaseModel):
    file: str
    block: str | None      # "agent: news" … ; None = outside the building blocks
    area: str              # definition | manifest | entrypoints | SKILL.md | declaration | path
    status: str
    additions: int
    deletions: int
    notable: list[str]     # config-meaningful +/- diff lines


class ChangeImpact(BaseModel):
    items: list[ChangeImpactItem]
    warnings: list[str]


class PrSummary(BaseModel):
    state: str            # ready | pending
    summary: str | None
    sha: str


class SyncStatus(BaseModel):
    # The synced checkout's HEAD — what the cluster is actually running.
    sha: str | None


# --- reports -----------------------------------------------------------------

class ReportTypeView(BaseModel):
    name: str
    description: str
    icon: str
    generator: str
    cadence: str
    retention_days: int
    error: str | None
    count: int
    latest_date: str | None


class ReportMeta(BaseModel):
    id: str
    type: str
    date: str             # YYYY-MM-DD
    time: str             # HH-MM, or "" for daily reports
    title: str
    meta: dict
    run_id: str | None
    created_at: str | None
    updated_at: str | None


class ReportDetail(ReportMeta):
    html: str             # the sanitized body fragment


class ReportSaved(BaseModel):
    id: str
    type: str
    date: str
    time: str
    replaced: bool        # true when this save updated an existing identity


class ChartSvg(BaseModel):
    svg: str


# --- relay (docs/design/19) ---------------------------------------------------
# A participant is a namespaced string (`agent:news`, `user:admin`,
# `discord:<id>`), never a foreign key — see `relay.participant_of`.

class RelayFace(BaseModel):
    """An agent's avatar: its own `AgentDef.icon` when set, else the
    deterministic fallback so `news` looks the same in every client forever."""
    emoji: str
    hue: int


class RelayReactionView(BaseModel):
    emoji: str
    count: int
    mine: bool


class RelayLastMessage(BaseModel):
    id: str
    author: str
    body: str            # truncated — the rail shows a preview, not the message
    created_at: str | None


class RelayBindingView(BaseModel):
    """A room on another network that mirrors this channel (docs/design/19).
    `external_ref` is that network's own id for it — a Discord channel or
    thread snowflake — and is unique per connector platform-wide."""
    id: str
    connector: str       # discord | slack | telegram
    external_ref: str
    config: dict


class RelayBindingRef(BaseModel):
    """The cross-channel listing a connector reads at startup: which of its
    rooms map to which channel. The connector is the query, so it is not
    repeated on every row."""
    channel_id: str
    external_ref: str
    config: dict


class RelayChannel(BaseModel):
    id: str
    kind: str            # dm | channel | group
    name: str | None     # slug, channels only
    # The room's display name: `#general` for a channel, the group's name, the
    # pair for a dm. `name` is the slug and is null off channels, so this is
    # what a rail can always show.
    title: str | None
    topic: str
    open: bool
    archived_at: str | None
    # The legacy single-agent field: set on DMs so /api/conversations keeps
    # working over the same row, null on channels and groups.
    agent: str | None
    # Explicit membership rows. An open channel has none by design (everyone is
    # a member), which is why `open` travels alongside rather than being
    # inferred from an empty list.
    participants: list[str]
    last_message: RelayLastMessage | None
    message_count: int
    # Messages newer than the caller's own last message here; 0 when they have
    # never spoken, so a room nobody has read does not shout.
    unread: int


class RelayChannelDetail(RelayChannel):
    faces: dict[str, RelayFace]
    # The bridges this room is mirrored to, so one fetch tells a client the
    # room is two-sided.
    bindings: list[RelayBindingView] = []
    # participant string -> the name that participant goes by on its own
    # network, for the ones whose string is an id rather than a name
    # (`discord:415…`). Absent for agents and principals.
    display_names: dict[str, str] = {}


class RelayMessage(BaseModel):
    id: str
    channel_id: str
    author: str
    kind: str            # text | system | event
    body: str
    card: dict | None
    reply_to: str | None
    thread_root: str | None
    run_id: str | None
    hop: int
    mentions: list[str]
    created_at: str | None
    edited_at: str | None
    face: RelayFace | None
    reactions: list[RelayReactionView] = []


class RelayPresence(BaseModel):
    agent: str
    state: str                  # idle | thinking | quarantined | disabled
    thinking_in: list[str]      # channel ids of its active runs
    face: RelayFace


class RelayBudget(BaseModel):
    channel_per_hour: int
    global_per_hour: int
    global_used_last_hour: int


class RelaySettings(BaseModel):
    """What the running platform is actually enforcing (docs/design/19).

    READ-ONLY, and here rather than behind a settings endpoint because these
    are environment settings: `Settings` is a pydantic-settings object read
    from AP_* at boot, with no runtime-mutation mechanism anywhere in the API
    to hang a toggle off. Reporting them beside the counters they govern at
    least means an operator reading "suppressed_24h: 40" can see the budget
    that suppressed them without going to read the Helm values."""
    default_grant: bool
    max_hops: int
    channel_per_hour: int
    global_per_hour: int
    cooldown_seconds: int
    context_messages: int


class RelayStats(BaseModel):
    messages_24h: int
    agent_messages_24h: int
    invocations_24h: int
    # Mentions that went UNANSWERED — the hop cap, the hourly budget, an agent
    # that is not in the room. Deliberately not every suppression: a coalesced
    # wake and a DM turn the facade owns are the guards working, and counting
    # them here is what makes a healthy room look like a broken one.
    suppressed_24h: int
    # Every suppression reason of the last day, zero-filled for the ones the
    # router knows about, so a reader can see WHY beside the headline.
    suppressed_by_reason: dict[str, int]
    budget: RelayBudget
    settings: RelaySettings


class RelayChannelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = "channel"
    name: str | None = Field(default=None, max_length=64)
    topic: str = Field(default="", max_length=256)
    # Channels only, and only meaningful there: a dm/group is closed by
    # definition. None = the kind's default (open for a channel).
    open: bool | None = None
    # A room, not a mailing list: the cap is what stops one request writing
    # unbounded membership rows.
    participants: list[str] = Field(default=[], max_length=64)


class RelayChannelPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=64)
    topic: str | None = Field(default=None, max_length=256)
    archived: bool | None = None


class RelayMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No author field, deliberately: authorship comes from the token.
    body: str = Field(min_length=1, max_length=8000)
    reply_to: str | None = None


class RelayReactionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    emoji: str = Field(min_length=1, max_length=8)


class RelayBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    connector: str = Field(max_length=32)
    external_ref: str = Field(min_length=1, max_length=256)
    # Connector-specific detail (a guild id, a webhook name). Opaque here: the
    # platform never interprets it, the bridge does.
    config: dict = {}


class RelayDmIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    # `with` is a keyword, so the wire name and the field name differ here.
    with_: str = Field(alias="with", min_length=3, max_length=128)


# --- schedules ---------------------------------------------------------------

class CronPreview(BaseModel):
    """What a cron expression means and when it will next fire.

    A validation feed, not a request that can fail: an expression the operator
    is still typing is answered 200 with `error` set, because a 4xx per
    keystroke is noise in the console and in the network log. `english` and
    `next` are empty exactly when `error` is set.
    """
    english: str = ""
    # UTC instants, from the scheduler's own next_fire — the times that will
    # actually fire, not a second opinion about them.
    next: list[datetime] = []
    error: str | None = None


class ScheduleRow(BaseModel):
    agent: str
    cron: str
    enabled: bool
    last_fire: datetime | None
    next_fire: datetime | None


class ScheduleToggle(BaseModel):
    agent: str
    enabled: bool


# --- secrets -----------------------------------------------------------------

class SecretKeyField(BaseModel):
    name: str
    hint: str = ""


class SecretStatus(BaseModel):
    name: str
    status: str
    # Has a secrets/<name>/secret.yaml declaration (first-class); undeclared
    # rows are bare values the registry knows nothing about.
    declared: bool
    required: bool
    hint: str
    key: str
    probeable: bool
    # Every declared data key (name + where-to-get-it hint), so the value
    # editor can render one field per key and set a multi-key secret in one go.
    # Empty for undeclared/bare secrets (the editor falls back to a single box).
    keys: list[SecretKeyField] = []


class SecretVerify(BaseModel):
    name: str
    status: str
    code: int | None
    detail: str


class SecretDeclaration(BaseModel):
    name: str
    raw: str              # secret.yaml as written (comments preserved)
    error: str | None


# --- skills ------------------------------------------------------------------

class SkillView(BaseModel):
    name: str
    description: str
    icon: str
    secrets: list[str]
    error: str | None
    used_by: list[str]


class SkillDetail(SkillView):
    body: str
    raw: str    # full SKILL.md (frontmatter + body) — what the editor edits


class WhoAmI(BaseModel):
    """Verified caller identity for the MCP broker's grant checks."""
    principal: str
    role: str
    agent: str | None      # set for agent-scoped API keys
    run_id: str | None     # set for per-run keys
    # Root principal from the run JWT's frozen claims (design/13 C/D).
    initiated_by: str | None = None
    # The mcp__platform__* tools the caller's agent definition declares
    # (None for non-agent callers, e.g. the admin session).
    tools: list[str] | None


# --- custom tools (docs/design/12) -------------------------------------------

class ToolView(BaseModel):
    name: str
    description: str
    secrets: list[str]        # secret block names the executor injects per-call
    database: bool            # owns a provisioned tool_<name> pg schema
    has_requirements: bool    # pip deps baked into the executor image by CI
    timeout_seconds: int
    error: str | None
    used_by: list[str]


class ToolDetail(ToolView):
    params: dict              # JSON Schema for the tool's arguments
    files: dict[str, str]     # tool.yaml / run.py / requirements.txt / test_run.py


# --- setup -------------------------------------------------------------------

class SetupState(BaseModel):
    needs_admin: bool
    secrets: list[SecretStatus]


__all__ = [n for n in dir() if n[0].isupper()]
