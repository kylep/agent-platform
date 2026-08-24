"""The agent definition as data (docs/design/15).

`db.AgentDef` is the row; this module is everything that gives it meaning
without touching the world: the pydantic mirror the API/tools validate against,
the registry check that replaced CI's file linting, and the snapshot helpers
the `agent_versions` change log is built from.

Deliberately IO-FREE: nothing here reads the repo, the DB (beyond a session
handed in), or the network. Callers pass the code registries — skills, secrets,
tools — into `validate_def`, which keeps validation testable and keeps this
module importable from the API, the tool executor, and the launcher alike.
"""
from __future__ import annotations

from pydantic import BaseModel, field_validator

from agentplatform.agentspec import CLAUDE_TOOLS, validate_agent_name
from agentplatform.db import AgentDef, AgentVersion

# The roles an agent DEFINITION may declare — api.auth.ROLES minus two:
# `admin` (an agent must never be able to mint itself admin scope; the human
# session is the only admin) and `tools` (a DERIVED machine role the launcher's
# ladder assigns from an agent's platform-tool grants, not something a
# definition declares). A test pins this to a subset of auth.ROLES so the two
# lists cannot drift apart silently.
AGENT_ROLES: tuple[str, ...] = ("reader", "annotator", "operator", "coder")

# Harness tools are Claude Code's own fixed set, so unlike skills/secrets/
# platform tools they are checked against a constant rather than a registry.
HARNESS_TOOLS: tuple[str, ...] = tuple(CLAUDE_TOOLS)

# Every field that makes up the definition — the snapshot surface, and what a
# rollback restores. Deliberately excludes created_at/updated_at: timestamps
# are row bookkeeping, not part of what an agent *is*.
DEF_FIELDS: tuple[str, ...] = (
    "name", "prompt", "description", "model", "role", "system", "can_invoke",
    "concurrency", "timeout_seconds", "result_topic", "transcript_retention_days",
    "harness_tools", "platform_tools", "skills", "secrets", "entrypoints",
    "enabled",
)


def _clean_names(v):
    """Grant lists are names: strip them, drop blanks, keep first occurrence.
    Non-strings fall through to pydantic, which rejects them with a real error
    (a dict or an int in a grant list is a malformed definition, not noise)."""
    if not isinstance(v, list):
        return v
    out: list = []
    for item in v:
        if isinstance(item, str):
            item = item.strip()
            if not item or item in out:
                continue
        out.append(item)
    return out


class CronEntry(BaseModel):
    """A durable cron trigger. Unlike the old entrypoints.yaml (bare
    expressions), each fire carries its own prompt — the same 1:many shape as
    ScheduledJob, so an agent can have two rhythms with different asks."""
    schedule: str
    prompt: str = ""

    @field_validator("schedule")
    @classmethod
    def _valid_cron(cls, v: str) -> str:
        from croniter import croniter
        if not croniter.is_valid(v):
            raise ValueError(f"invalid cron expression: {v!r}")
        return v


# How a declared webhook path authenticates its callers (docs/design/16).
# `none` is exactly the pre-design-16 behavior — a platform API key with the
# operator role — so nothing gets less secure by default. `secret` additionally
# accepts the shared secret in the `X-AP-Webhook-Secret` header, which is what
# makes a webhook reachable by a service that cannot hold a platform key.
# The list is open at the bottom on purpose: cert/mTLS-style caller auth is the
# next rung, and the UI dropdown already has room for it.
WEBHOOK_AUTH_MODES: tuple[str, ...] = ("none", "secret")


class WebhookEntry(BaseModel):
    path: str
    # The MODE only. The secret VALUE lives in `webhook_secrets` and never on
    # the definition — see webhooksecrets.py and docs/design/16: this blob is
    # snapshotted into `agent_versions` on every write, so anything stored here
    # is in the change log forever and comes back on rollback.
    auth: str = "none"

    @field_validator("auth")
    @classmethod
    def _known_auth(cls, v: str) -> str:
        if v not in WEBHOOK_AUTH_MODES:
            raise ValueError(f"webhook auth must be one of {WEBHOOK_AUTH_MODES}")
        return v


class EntrypointsModel(BaseModel):
    """The agent's defining triggers (formerly entrypoints.yaml): cron fires,
    inbound webhook paths (POST /api/webhooks/<path> only works for a declared
    path), and kafka topic subscriptions. Distinct from DB Jobs, which are
    ad-hoc UI experiments — history, not config."""
    crons: list[CronEntry] = []
    webhooks: list[WebhookEntry] = []
    topics: list[str] = []
    # IANA zone the cron expressions are read in (empty = UTC). One zone for
    # the whole agent: its triggers belong to one rhythm, and per-entry zones
    # would buy nothing but a bigger schema. Carried over from entrypoints.yaml
    # so market-pinned crons don't drift an hour across daylight saving.
    timezone: str = ""

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, v: str) -> str:
        from agentplatform.scheduler import is_valid_timezone
        if not is_valid_timezone(v):
            raise ValueError(f"unknown timezone: {v!r}")
        return v


class AgentDefModel(BaseModel):
    """Validating mirror of the AgentDef row. Defaults match the column
    defaults exactly, so a model built from a partial payload and a row built
    from the same payload are the same agent."""
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
    entrypoints: EntrypointsModel = EntrypointsModel()
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def _valid_name(cls, v: str) -> str:
        # The name is a `claude --agent` identifier and a path segment in the
        # run pod, so it obeys the same slug rule the wizard always enforced.
        return validate_agent_name(v)

    @field_validator("role")
    @classmethod
    def _known_role(cls, v: str) -> str:
        if v not in AGENT_ROLES:
            raise ValueError(f"role must be one of {AGENT_ROLES}")
        return v

    @field_validator("harness_tools", "platform_tools", "skills", "secrets",
                     mode="before")
    @classmethod
    def _coerce_names(cls, v):
        return _clean_names(v)


def validate_def(model: AgentDefModel, *, skill_names: set[str],
                 secret_names: set[str], tool_names: set[str]) -> list[str]:
    """Problems with a definition's GRANTS, as human-readable strings; empty
    list = valid. This is what CI used to do to the files: a grant naming a
    skill/secret/tool that does not exist in the repo is a dead grant that
    would fail at launch instead of at save time.

    Registries come from the caller (`skill_store`, `secret_registry`,
    `tool_registry` + the core mcp names) so this stays IO-free — and so a
    tool-side check and an API-side check can share one implementation.
    Shape/role/cron validity is the model's job and has already happened."""
    problems: list[str] = []
    for label, granted, known in (
            ("skill", model.skills, skill_names),
            ("secret", model.secrets, secret_names),
            ("platform tool", model.platform_tools, tool_names),
            ("harness tool", model.harness_tools, set(HARNESS_TOOLS))):
        for name in granted:
            if name not in known:
                problems.append(f"unknown {label}: {name!r}")
    return problems


def model_of(row: AgentDef) -> AgentDefModel:
    """The validating model for a row — what every reader of a definition goes
    through, so a row is understood the same way whether it came from a write
    payload or the table. Nones are dropped so an UNFLUSHED row (column
    defaults not applied yet) still reads as a complete definition rather than
    a bag of Nones. Raises ValidationError on a row that is no longer a valid
    definition; callers decide whether that quarantines or rejects."""
    values = {f: getattr(row, f, None) for f in DEF_FIELDS}
    return AgentDefModel(**{k: v for k, v in values.items() if v is not None})


def snapshot_of(row: AgentDef) -> dict:
    """The full definition of a row as a JSON-safe dict — the `snapshot`
    column of an AgentVersion, and the payload the import endpoint speaks."""
    return model_of(row).model_dump(mode="json")


def apply_snapshot(row: AgentDef, snapshot: dict) -> None:
    """Write a snapshot's definition onto a row (the rollback/import path).
    Identity is NOT restorable — the row's own name wins, so a snapshot can be
    applied to any row. Missing keys come back as model defaults rather than
    keeping the row's current value: a rollback that silently left a newer
    field in place would not be a rollback. Raises ValidationError if the
    snapshot is no longer a valid definition, which is the honest outcome —
    better than half-applying it."""
    data = dict(snapshot)
    data["name"] = row.name or data.get("name", "")
    model = AgentDefModel(**data)
    for field in DEF_FIELDS:
        if field == "name":
            continue
        value = getattr(model, field)
        setattr(row, field, value.model_dump(mode="json")
                if isinstance(value, BaseModel) else value)


async def next_version(session, agent: str) -> int:
    """The next version number for an agent: max + 1, 1 when it has no history.
    App-enforced rather than a DB sequence because the counter is per agent and
    must survive a definition being deleted and recreated (the change log is
    append-only — it outlives the row)."""
    from sqlalchemy import func, select
    current = (await session.execute(
        select(func.max(AgentVersion.version))
        .where(AgentVersion.agent == agent))).scalar()
    return (current or 0) + 1
