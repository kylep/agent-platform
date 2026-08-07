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

from pydantic import BaseModel

from agentplatform.agents import AgentInfo  # re-exported as the get_agent model


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


# --- agents ------------------------------------------------------------------

class AgentSummary(BaseModel):
    name: str
    description: str
    quarantined: bool
    error: str | None
    # Blocked — unmet required secret dependency (docs/design/10). Recoverable
    # by fixing the SECRET; quarantined is recoverable by fixing the agent.
    blocked: bool
    blocked_reason: str | None
    system: bool
    schedule: str


class AgentTools(BaseModel):
    tools: list[str]
    # Presentation labels for awkward harness-fixed ids (TodoWrite → "Todo");
    # keys are tool names, manifests always declare the real id.
    labels: dict[str, str] = {}


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
    agent: str
    cron: str
    prompt: str
    enabled: bool
    last_fire: str | None
    next_fire: str | None


class JobRunAccepted(BaseModel):
    id: str
    agent: str


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


# --- schedules ---------------------------------------------------------------

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


# --- setup -------------------------------------------------------------------

class SetupState(BaseModel):
    needs_admin: bool
    secrets: list[SecretStatus]


__all__ = [n for n in dir() if n[0].isupper()] + ["AgentInfo"]
