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
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

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

    # The two read-only fields every GET carries (`AgentDefOut`) — the picture
    # and its face (docs/design/23) — are discarded here, before validation,
    # for `WebhookEntryIn.secret_set`'s reason: the editor and the `agents_edit`
    # tool PUT back what they GET, and `extra="forbid"` would 422 the round
    # trip. Discarded rather than declared, so they are not fields of the
    # input at all (a test pins `AgentDefIn` to DEF_FIELDS): the image route
    # is the only way to change the picture, and nothing echoed here can reach
    # the column or a snapshot.
    @model_validator(mode="before")
    @classmethod
    def _drop_read_only(cls, data):
        if isinstance(data, dict) and ("image_artifact_id" in data or "face" in data):
            return {k: v for k, v in data.items() if k not in ("image_artifact_id", "face")}
        return data

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
    # The Workbench (docs/design/24): the two path-policy grants, then the
    # two quota thresholds. Bounds live on `AgentDefModel`, which every write
    # goes through; here the shape only — strict, so `true` and "55" are 422
    # here rather than 1 and 55 there.
    push_path_globs: list[str] = []
    may_delete_tests: bool = False
    quota_5h_max_pct: int = Field(default=80, strict=True)
    quota_7d_max_pct: int = Field(default=50, strict=True)


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
    # The Tickets default grant (docs/design/20), the same tri-state for the
    # same reason: an operator may want the messenger without the work tracker,
    # and one knob could not say so.
    tickets: bool | None = None
    # The Wiki default grant (docs/design/21), the third of the same shape.
    wiki: bool | None = None
    # The usage-reading default grant (docs/design/22), the fourth. Named for
    # the tool's last segment like its siblings (`agents._knob`), which is why
    # this one reads as a verb: the tool is `get_quota_usage`.
    get_quota_usage: bool | None = None
    # The artifacts default grant (docs/design/23), the fifth. `image_gen` has
    # no knob: it is granted on purpose, never by default.
    artifacts: bool | None = None


class AgentImageIn(BaseModel):
    """`PUT /api/agents/{name}/image` (docs/design/23): the picture's artifact,
    or null to take it off. The key is required so an empty body is a 422 and
    not a silent clear."""
    model_config = ConfigDict(extra="forbid")
    artifact_id: str | None


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
    push_path_globs: list[str] = []
    may_delete_tests: bool = False
    quota_5h_max_pct: int = 80
    quota_7d_max_pct: int = 50
    # The agent's picture (docs/design/23) and the face it makes: OUTSIDE the
    # definition (never in `AgentDefIn`, never in a snapshot), carried on every
    # read so the Agents pages need no second fetch. `face` is what every other
    # consumer of an agent's face gets — the same `faces_for`, so an agent
    # looks the same on its own page as it does in a room.
    image_artifact_id: str | None = None
    face: RelayFace


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
    # The ticket this run was summoned from (docs/design/20), so a run always
    # points back at what asked for it. Null for every other trigger.
    ticket_id: str | None = None


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
    # The verb of a multi-action tool, when the broker named one. Nullable and
    # required, like `run_id` beside it: the route always answers the field, and
    # what varies is whether the call had a verb.
    action: str | None
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
    deterministic fallback so `news` looks the same in every client forever.
    `image_url` is the agent's picture (docs/design/23) — the thumb route of
    its `image_artifact_id` — which a client shows over the emoji when set.
    Optional so every producer of a face keeps working; `faces_for` fills it."""
    emoji: str
    hue: int
    image_url: str | None = None


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
    # A channel with a prefix IS a project (docs/design/20). Null on groups and
    # DMs, and on a channel whose prefix an operator never gave it.
    ticket_prefix: str | None
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
    # Making a channel a project (docs/design/20): 2-6 uppercase letters,
    # unique among channels, and only until the first ticket is filed under it.
    # Null is "unchanged" like every other field here — a project that has
    # issued keys cannot un-become one, because those keys are forever.
    ticket_prefix: str | None = Field(default=None, max_length=8)


class RelayMessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No author field, deliberately: authorship comes from the token.
    body: str = Field(min_length=1, max_length=8000)
    reply_to: str | None = None


class RelayReactionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A glyph, not a string: the value is stored as given and echoed to every
    # viewer of the room, in the message payload and in the SSE frame, so what
    # this accepts is what a reaction pill eventually holds. Whitespace and
    # control characters are not glyphs, and `<`/`>`/`&` are refused here so no
    # client's rendering choice can turn a reaction into markup. The length is
    # in code points and one emoji is many of them — a skin-toned family of
    # four is eleven — so the cap is sixteen, which holds every compound
    # sequence a keyboard offers and still nothing anybody would call a word.
    emoji: str = Field(min_length=1, max_length=16,
                       pattern=r"^[^\s<>&\x00-\x1f\x7f]{1,16}$")


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


# --- tickets (docs/design/20) -------------------------------------------------
# A ticket is a row with a state AND a Relay thread, so these models are the
# row: the thread is read through Relay's own message endpoints. Participants
# are the same namespaced strings Relay uses, and a face is only ever resolved
# for an agent — a human participant may live outside this platform, so there
# is nothing to look its avatar up in.

class TicketView(BaseModel):
    id: str
    key: str                     # 'OPS-12', stable for the life of the ticket
    channel_id: str              # the project
    title: str
    body: str
    state: str                   # open|in_progress|blocked|review|done|cancelled
    priority: str                # p0|p1|p2|p3
    assignee: str | None
    reporter: str
    labels: list[str]
    parent_id: str | None
    due_at: str | None
    # The run that opened it (agents only) and the event card in the channel,
    # which is also the root of the ticket's thread.
    run_id: str | None
    root_message_id: str | None
    created_at: str | None
    updated_at: str | None
    # Distinct from updated_at: a comment is activity but not an edit, and
    # `stale` below is read off this one.
    last_activity_at: str | None
    closed_at: str | None
    # Attached by the API, not stored: the board draws faces without a second
    # call, and a card badges itself as stale without knowing the setting.
    assignee_face: RelayFace | None = None
    reporter_face: RelayFace | None = None
    stale: bool = False


class TicketEventView(BaseModel):
    id: str
    ticket_id: str
    actor: str
    kind: str                    # created|moved|assigned|edited|commented|reopened
    from_value: str | None
    to_value: str | None
    reason: str | None
    # The Relay message this change produced — the system row, or the comment.
    message_id: str | None
    run_id: str | None
    created_at: str | None
    actor_face: RelayFace | None = None


class TicketRunRef(BaseModel):
    """A run that was summoned from this ticket's thread."""
    id: str
    agent: str
    state: str
    trigger: str
    created_at: str | None


class TicketThinking(BaseModel):
    """The run working on this ticket right now, if there is one. Derived from
    the runs, never stored, exactly as Relay's presence is."""
    run_id: str
    agent: str


class TicketDetail(BaseModel):
    ticket: TicketView
    events: list[TicketEventView]
    # The card in the channel: the client reads the discussion by asking Relay
    # for this thread, rather than having the messages duplicated here.
    root_message_id: str | None
    runs: list[TicketRunRef]
    thinking: TicketThinking | None


class TicketProject(BaseModel):
    """A Relay channel that has a prefix, with the two counts a project picker
    shows."""
    id: str
    name: str | None
    title: str | None
    prefix: str
    open: int
    in_progress: int


class TicketActorCount(BaseModel):
    """One row of the board's "Today" strip: who moved how much."""
    actor: str
    label: str                   # the actor as a room says it (`news`)
    count: int
    face: RelayFace | None = None


class TicketAgentBudget(BaseModel):
    agent: str
    used: int                    # tickets opened in the trailing hour
    left: int
    face: RelayFace | None = None


class TicketBudgetView(BaseModel):
    creates_per_hour: int
    stale_days: int
    # Only the agents that have actually opened something this hour, busiest
    # first: a list of every agent at zero would say nothing.
    agents: list[TicketAgentBudget] = []


class TicketStats(BaseModel):
    open: int
    in_progress: int
    blocked: int
    review: int
    done_24h: int
    moved_24h: list[TicketActorCount]
    # Both computed, never stored: in-progress work nobody has touched for
    # `stale_days`, and open work assigned to an agent that is disabled or gone.
    stale: int
    orphaned: int
    budget: TicketBudgetView


class TicketIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # A channel id or a `#name`. Not optional: a ticket without a project has
    # no key, no card and nowhere to be discussed.
    channel: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=256)
    body: str = Field(default="", max_length=16000)
    # No `reporter`: authorship comes from the token, and `extra="forbid"`
    # makes asking for one a 422 rather than a field quietly ignored.
    assignee: str | None = Field(default=None, max_length=128)
    priority: str = "p2"
    labels: list[str] = Field(default=[], max_length=32)
    parent: str | None = Field(default=None, max_length=64)   # a key or an id
    due_at: datetime | None = None
    # Whether an agent assignee is actually summoned. The ticket is assigned
    # either way (docs/design/20).
    notify: bool = True


class TicketPatch(BaseModel):
    """The editable fields. Every one is nullable AND defaulted, so "clear the
    due date" and "leave it alone" are told apart by which keys the caller
    actually sent (`model_fields_set`), not by the value."""
    model_config = ConfigDict(extra="forbid")
    title: str | None = Field(default=None, max_length=256)
    body: str | None = Field(default=None, max_length=16000)
    priority: str | None = None
    labels: list[str] | None = Field(default=None, max_length=32)
    parent: str | None = Field(default=None, max_length=64)
    due_at: datetime | None = None
    reason: str | None = Field(default=None, max_length=1000)


class TicketMoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: str = Field(min_length=1, max_length=16)
    reason: str | None = Field(default=None, max_length=1000)


class TicketAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Required but nullable: `null` is the unassign, and leaving the field out
    # is a request that says nothing.
    to: str | None = Field(max_length=128)
    reason: str | None = Field(default=None, max_length=1000)
    notify: bool = True


class TicketCommentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # No author field, deliberately: the same rule as a Relay message.
    body: str = Field(min_length=1, max_length=8000)


# --- wiki (docs/design/21) ----------------------------------------------------
# A page is a row plus the history that proves who said it. Authors are the same
# namespaced participant strings Relay uses, so a face is resolved for an agent
# and is null for anybody else. Nothing here is room-scoped: a page belongs to
# the platform, and `#wiki` is only where its diff cards are narrated.

class WikiPageView(BaseModel):
    id: str
    slug: str                    # the URL and the `[[link]]` target
    title: str
    body: str
    summary: str                 # the first paragraph, recomputed on every write
    tags: list[str]
    version: int
    created_by: str
    updated_by: str
    # The memory this page was promoted from, if it was (docs/design/21).
    source_memory_id: str | None
    created_at: str | None
    updated_at: str | None
    archived_at: str | None
    # Attached by the API, never stored — the same seam as a ticket's faces.
    updated_by_face: RelayFace | None = None


class WikiPageRef(BaseModel):
    """A page named from somewhere else: a backlink in the sidebar."""
    slug: str
    title: str


class WikiCitation(BaseModel):
    """One room message that pointed at this page with a `[[slug]]`. Only ever
    from a room the reader may see: the page is the platform's, the conversation
    about it is still Relay's."""
    message_id: str
    channel_id: str
    author: str
    created_at: str | None


class WikiCitations(BaseModel):
    count: int
    # Whether the scan behind `count` hit its limit, which makes the count a
    # floor rather than a total ("cited in 200+ messages").
    count_capped: bool = False
    # The last three, newest first: a link back into the conversation, not a
    # second copy of it.
    last: list[WikiCitation] = []


class WikiPageDetail(BaseModel):
    page: WikiPageView
    backlinks: list[WikiPageRef]
    cited_in: WikiCitations


class WikiVersionView(BaseModel):
    id: str
    page_id: str
    version: int
    title: str
    body: str                    # the FULL body: diffs are computed on read
    author: str
    run_id: str | None           # the run that wrote it (agents only)
    reason: str
    created_at: str | None
    author_face: RelayFace | None = None


class WikiHistoryRow(BaseModel):
    """One line of the history drawer. No body, deliberately: the drawer shows
    who, why and ±lines, and a page's whole text per row would make reading the
    history cost more than reading the page."""
    id: str
    version: int
    title: str
    author: str
    run_id: str | None
    reason: str
    created_at: str | None
    added: int
    removed: int
    author_face: RelayFace | None = None


class WikiDiffView(BaseModel):
    """One version and what it changed, against the version before it."""
    version: WikiVersionView
    diff: str
    added: int
    removed: int


class WikiWantedRow(BaseModel):
    """A red link: a slug pages point at that nobody has written yet."""
    slug: str
    linked_from: list[str]


class WikiAuthorCount(BaseModel):
    author: str
    count: int
    face: RelayFace | None = None


class WikiAgentBudget(BaseModel):
    agent: str
    used: int                    # versions written in the trailing hour
    left: int


class WikiBudgetView(BaseModel):
    limit: int
    # Only the agents that have actually written this hour, busiest first.
    agents: list[WikiAgentBudget] = []


class WikiStats(BaseModel):
    pages: int                   # live ones; archiving takes a page out
    edits_24h: list[WikiAuthorCount]
    wanted: int
    # Untouched for `wiki_stale_days` — computed every time, never stored, for
    # the same reason a ticket's staleness is: the setting can change.
    stale: int
    budget: WikiBudgetView


class WikiPageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    slug: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(default="", max_length=65536)
    tags: list[str] = Field(default=[], max_length=20)
    reason: str = Field(default="", max_length=200)


class WikiWriteIn(BaseModel):
    """A full replacement of a page's body. `base_version` is REQUIRED and
    unset is a 422: a write that never says what it read is a writer claiming
    the page has not moved without having looked, and the loser of two of those
    silently erases the winner."""
    model_config = ConfigDict(extra="forbid")
    body: str = Field(max_length=65536)
    reason: str = Field(default="", max_length=200)
    title: str | None = Field(default=None, max_length=120)
    tags: list[str] | None = Field(default=None, max_length=20)
    base_version: int = Field(ge=1)


class WikiAppendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=65536)
    reason: str = Field(default="", max_length=200)


class WikiRestoreIn(BaseModel):
    """Un-archive, or roll back. `version` absent is the un-archive; naming one
    is a roll-back, which is a new version rather than a rewritten history."""
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=200)


class WikiPromoteIn(BaseModel):
    """The memory to harden into a page, named exactly one way.

    `key` exists because the caller that most wants to promote cannot use an
    id: an agent holds the key it remembered under, and its participant token
    is refused by `/api/memories` (a `READ_ROLES` door the wiki's is not), so
    trading a key for an id over HTTP is not a trade it can make. The key is
    therefore resolved server-side, in the caller's own namespace — which is
    also why a human, who has no namespace of their own, must say `agent`.
    """
    model_config = ConfigDict(extra="forbid")
    memory_id: str | None = Field(default=None, min_length=1, max_length=64)
    key: str | None = Field(default=None, min_length=1, max_length=128)
    # Whose memory `key` is. An agent caller's namespace is its own and naming
    # another is refused; a human has none to default to.
    agent: str | None = Field(default=None, min_length=1, max_length=128)
    # Both derived from the memory's key when they are not given.
    slug: str | None = Field(default=None, max_length=64)
    title: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def _one_way_to_name_it(self):
        # Both is two answers to "which memory", and resolving the ambiguity by
        # preferring one would make the other argument silently do nothing.
        if bool(self.memory_id) == bool(self.key):
            raise ValueError("pass exactly one of memory_id or key")
        return self


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


# --- quota (docs/design/22) ---------------------------------------------------

class QuotaWindow(BaseModel):
    """One rate-limit window. `utilization` is a FRACTION whichever form the
    header arrived in (`quota.parse_utilization`), so a bar never has to guess
    whether 22 means a fifth or everything."""
    utilization: float | None
    resets_at: str | None


class Quota(BaseModel):
    """The snapshot, as `quota_store.serialize` produces it — the one shape the
    REST body, the SSE frame and the `quota.events` payload share."""
    five_hour: QuotaWindow
    seven_day: QuotaWindow
    status: str | None
    observed_at: str | None
    source: str | None
    # Whether the observation can still be believed: no row at all, or a window
    # that has reset since it was taken.
    stale: bool
    age_seconds: int | None
    # Which probe step answered, on the refresh route only: `count_tokens` when
    # the free step carried the headers, `message` when it took a real
    # completion, null when the call was answered from the cache. Anthropic's
    # behaviour here was not observable before implementation, so the platform
    # records what actually happened rather than asserting it.
    probe: str | None = None


class QuotaOk(BaseModel):
    """The reading turned into a decision (docs/design/24): whether the caller
    may start expensive work now. `ok` is the field a model decides on; the
    percentages and the thresholds beside it are what it was decided from, so
    a "no" can be explained without re-reading the snapshot. The thresholds
    are the caller's own row when the caller is an agent and the column
    defaults when it is a person."""
    ok: bool
    # Whole percents, rounded half-up as `quota._percent` does, and null when
    # the platform has no reading for that window — in which case `ok` is
    # false and `reason` says so.
    five_hour_pct: int | None
    seven_day_pct: int | None
    five_hour_max_pct: int
    seven_day_max_pct: int
    # The reading could not be refreshed and is a description of a window
    # that has already turned over: `ok` was still computed from it, and this
    # is how much to trust it.
    stale: bool
    # One sentence: "ok", "no reading yet", or which window is over its limit.
    reason: str


# What one report may carry. The body is capped in the route before it is
# parsed at all; these bound the SHAPE inside that body, so a well-formed 64 KiB
# document cannot still arrive as ten thousand one-byte headers.
QUOTA_MAX_HEADERS = 128
QUOTA_MAX_HEADER_CHARS = 512


class QuotaObserveIn(BaseModel):
    """What the claude-proxy reports (docs/design/22): the response headers it
    just relayed, verbatim. Extra keys are tolerated — the proxy is a shell
    script's worth of nginx/njs and the contract has to survive it growing a
    field — and a body carrying no usage header at all is ignored, not an
    error, because most responses say nothing about usage.

    The bounds are here rather than left to the store because this is the one
    door on the platform that a session cannot open and an API key cannot
    open: whatever reaches it has already been trusted on a shared secret, so
    the shape is the only thing left to check."""
    headers: dict[str, Any] = Field(default_factory=dict)
    # The HTTP status the proxy saw with those headers. Diagnostic only — no
    # typed column, nothing branches on it — but it is kept in the snapshot's
    # `raw` as `http_status`, because a 0.99 reading off a 429 and the same
    # reading off a 200 mean different things to whoever reads the row.
    status: int | None = None
    # When Anthropic answered. Parsed, and only used when it parses: a report
    # that queued behind something must not look newer than it is, and a
    # garbage timestamp must not make the snapshot look older than it is. A
    # time in the future is clamped rather than believed (`parse_observed_at`)
    # — it would otherwise outrank every later observation for good.
    observed_at: str | None = None

    @model_validator(mode="after")
    def _bounded(self):
        if len(self.headers) > QUOTA_MAX_HEADERS:
            raise ValueError(f"at most {QUOTA_MAX_HEADERS} headers")
        for key, value in self.headers.items():
            if len(key) > QUOTA_MAX_HEADER_CHARS or len(str(value)) > QUOTA_MAX_HEADER_CHARS:
                raise ValueError(
                    f"header names and values are at most {QUOTA_MAX_HEADER_CHARS} characters")
        return self


class QuotaIgnored(BaseModel):
    """The answer to a report that said nothing about usage.

    Most responses the proxy relays carry no usage header at all, so this is
    the COMMON outcome, not an error — writing those would overwrite a real
    snapshot with nulls. It is a 200 with a body rather than a 204 because the
    proxy is njs, whose `ngx.fetch` never settles its promise on a bodyless
    204: the request would hang until nginx timed it out, once per relayed
    Anthropic call. It deliberately carries no snapshot — the hot path must not
    cost a database read to say "nothing to record"."""
    ignored: bool = True


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
