"""Self-management is a narrow capability, never an agent-definition editor."""
import hashlib
import hmac
import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Literal

from croniter import croniter

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentplatform import artifact_store
from agentplatform.appdata import artifacts as app_artifacts
from agentplatform.agentdefs import CronEntry, next_version, snapshot_of
from agentplatform.agentspec import CODEX_MODELS, KNOWN_MODELS, TOOL_SELF
from agentplatform.authority import assert_readable_run, ensure_run_authority
from agentplatform.db import ACTIVE_STATES, AgentDef, AgentVersion, Run
from agentplatform.api.auth import authenticate
from agentplatform.api.agents import _caller_platform_tools, _managed_guard, _model, _payload, _registries
from agentplatform.api.schemas import AgentImageIn

router = APIRouter(tags=["agent-self"])

# An agent that schedules itself spends the shared quota unattended, so the
# server bounds how often each trigger fires: never more than hourly. How
# many triggers it declares is up to the agent (Kyle, 2026-11-07). Kyle's admin routes are not subject to these.
MIN_CRON_INTERVAL = timedelta(hours=1)


class SelfCron(CronEntry):
    """A cron entry an agent writes for itself: a bounded prompt, and a model
    that is empty (the agent's own) or one the platform knows about."""
    prompt: str = Field(default="", max_length=8000)
    model: str = Field(default="", max_length=64)


def _check_cron_rate(schedule: str) -> None:
    """422 if two consecutive fires of `schedule` are closer than the floor.
    Sampled over the next 48 fires: enough to see every gap in a daily or
    weekly pattern, while a `*/5` or `0,5 * * * *` fails on the first pair."""
    it = croniter(schedule, datetime(2026, 1, 1, tzinfo=UTC))
    prev = it.get_next(datetime)
    for _ in range(48):
        nxt = it.get_next(datetime)
        if nxt - prev < MIN_CRON_INTERVAL:
            raise HTTPException(422, f"cron {schedule!r} fires more often than every "
                                     f"{int(MIN_CRON_INTERVAL.total_seconds() // 60)} minutes")
        prev = nxt


class SelfProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str | None = Field(default=None, min_length=1, max_length=64000)
    description: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=64)
    backup_runtime: Literal["claude", "codex"] | None = None
    backup_model: str | None = Field(default=None, max_length=64)
    runtime: Literal["claude", "codex"] | None = None
    # The COMPLETE list of this agent's own cron triggers (replaces it), read in
    # `timezone`; webhooks and topics are never touched through here.
    crons: list[SelfCron] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    expected_version: int = Field(ge=0)


class SelfProfileOut(BaseModel):
    name: str
    prompt: str
    description: str
    runtime: str
    model: str
    backup_runtime: str | None
    backup_model: str
    image_artifact_id: str | None
    crons: list[CronEntry]
    timezone: str
    version: int
    system_source: str | None


def profile_view(row, version):
    entry = row.entrypoints or {}
    return {field: getattr(row, field) for field in
            ("name", "prompt", "description", "runtime", "model", "backup_runtime", "backup_model", "image_artifact_id", "system_source")} | {
        "crons": entry.get("crons") or [], "timezone": entry.get("timezone") or "", "version": version}


async def actor(session, request):
    if await authenticate(request) is None:
        raise HTTPException(401)
    name = getattr(request.state, "api_key_agent", None)
    run_id = getattr(request.state, "api_key_run_id", None)
    if not name or not run_id:
        raise HTTPException(403, "self-management requires an agent's active run")
    if TOOL_SELF not in await _caller_platform_tools(request, name):
        raise HTTPException(403, "agent_self Tool is not granted")
    run = await session.get(Run, run_id)
    if run is None or run.agent != name or run.state not in ACTIVE_STATES:
        raise HTTPException(403, "self-management requires this agent's active run")
    if not await ensure_run_authority(session, run):
        await session.commit()
        raise HTTPException(403, "run authority has changed")
    from agentplatform.village.commands import village_run
    if await village_run(session, run.id) is not None:
        # A village run changes its prompt only through `village prompt`,
        # section-only and applied at close (GEN-11 §6.2). The API fence
        # already refuses these paths; this is the handler's own check.
        raise HTTPException(403, "a village run may not use agent_self")
    row = await session.get(AgentDef, name, with_for_update=True)
    if row is None:
        raise HTTPException(403, "agent no longer exists")
    # Self-modification is a persona privilege; a worker's definition is Kyle's,
    # whatever its stored grants say.
    if row.agent_type != "persona":
        raise HTTPException(403, "agent_self is only for persona agents")
    return row, run


@router.get("/api/agent-self", response_model=SelfProfileOut)
async def get_self_profile(request: Request):
    async with request.app.state.session_factory() as session:
        row, _ = await actor(session, request)
        return profile_view(row, await next_version(session, row.name) - 1)


@router.get("/api/agent-self/models")
async def get_self_models(request: Request):
    async with request.app.state.session_factory() as session:
        await actor(session, request)
        return {"claude": KNOWN_MODELS, "codex": CODEX_MODELS}


@router.patch("/api/agent-self", response_model=SelfProfileOut)
async def update_self_profile(request: Request, body: SelfProfileIn):
    st = request.app.state
    async with st.session_factory() as session:
        row, run = await actor(session, request)
        before_local_edit = snapshot_of(row)
        version = await next_version(session, row.name) - 1
        if body.expected_version != version:
            raise HTTPException(409, "profile changed; read agent_self get before trying again")
        changes = body.model_dump(exclude_unset=True, exclude={"expected_version"})
        if {"model", "runtime"} & changes.keys() and {"backup_model", "backup_runtime"} & changes.keys():
            raise HTTPException(422, "change the primary or the backup in one call, never both")
        if any(value is None for key, value in changes.items() if key != "backup_runtime"):
            raise HTTPException(422, "profile fields cannot be null; use an empty model for the platform default")
        if "runtime" in changes and changes["runtime"] != row.runtime and "model" not in changes:
            raise HTTPException(422, "changing runtime requires an explicit model, or an empty model for its default")
        # Every agent keeps a backup on the other runtime, so one provider's
        # outage never strands it (Kyle, 2026-11-07). A self-edit can replace
        # the backup but not drop it, and moving the primary onto the backup's
        # runtime makes the old primary the new backup.
        if row.backup_runtime and "backup_model" in changes and not changes["backup_model"]:
            raise HTTPException(422, "an agent keeps its backup model; choose a different one instead of clearing it")
        if "backup_runtime" in changes and changes["backup_runtime"] == row.runtime:
            raise HTTPException(422, f"the backup must run on the other runtime than the primary ({row.runtime})")
        new_runtime = changes.get("runtime", row.runtime)
        if ("model" in changes and row.backup_runtime and row.model
                and new_runtime == row.backup_runtime != row.runtime):
            changes["backup_runtime"], changes["backup_model"] = row.runtime, row.model
        payload = {**_payload(row), **changes}
        cron_change = {"crons", "timezone"} & changes.keys()
        if cron_change:
            for entry in changes.get("crons") or []:
                _check_cron_rate(entry["schedule"])
                if entry.get("model") and entry["model"] not in {m["id"] for m in (*KNOWN_MODELS, *CODEX_MODELS)}:
                    raise HTTPException(422, f"unknown cron model: {entry['model']!r}")
            payload.pop("crons", None)
            payload.pop("timezone", None)
            # Only the two cron fields move; webhooks and topics are carried over.
            payload["entrypoints"] = {**(row.entrypoints or {}), **{
                key: value for key, value in changes.items() if key in cron_change}}
        # Grants the row ALREADY holds stay valid here even if the registry has
        # since dropped them (a retired tool): self-edit never adds a grant, and
        # a dead one must not block an unrelated change. Admin writes still
        # validate every name.
        registries = _registries(request)
        registries["skill_names"] |= set(row.skills or [])
        registries["secret_names"] |= set(row.secrets or [])
        registries["tool_names"] |= set(row.platform_tools or [])
        model = _model(request, payload, row.name, registries)
        _managed_guard(row, model)
        changes = {key: getattr(model, key) for key in changes if key not in cron_change and getattr(model, key) != getattr(row, key)}
        entry_new = model.model_dump(mode="json")["entrypoints"] if cron_change else None
        if entry_new is not None and entry_new != (row.entrypoints or {}):
            changes["entrypoints"] = entry_new
        if not changes:
            return profile_view(row, version)
        for key, value in changes.items():
            setattr(row, key, value)
        # Same grants, new persona/model context. Other active runs and all old
        # sessions lose authority; ONLY this already authenticated run may finish.
        # A schedule-only change alters neither, so it leaves sessions alone.
        if changes.keys() != {"entrypoints"}:
            row.authorization_generation = (row.authorization_generation or 0) + 1
            run.authorization_generation = row.authorization_generation
        version += 1
        session.add(AgentVersion(agent=row.name, version=version, snapshot=snapshot_of(row),
            changed_by=f"run:{run.id}", changed_via="tool:agent_self"))
        if run.local_persona_session_id:
            from agentplatform.db import LocalPersonaMutation
            session.add(LocalPersonaMutation(session_id=run.local_persona_session_id,
                run_id=run.id, kind='profile', target_id=row.name,
                before=before_local_edit, after=snapshot_of(row)))
        await session.commit()
        out = profile_view(row, version)
    await st.agent_store.reload()
    return out


@router.put("/api/agent-self/avatar", response_model=SelfProfileOut)
async def set_self_avatar(request: Request, body: AgentImageIn):
    st = request.app.state
    async with st.session_factory() as session:
        row, avatar_run = await actor(session, request)
        prior_avatar = row.image_artifact_id
        view = None
        if body.artifact_id is not None:
            art = await artifact_store.get(session, body.artifact_id)
            # A face is shown to everyone; an App-owned picture never is one.
            if art is None or await app_artifacts.ownership(session, art.id) is not None:
                raise HTTPException(404, "unknown artifact")
            if art.kind != "image":
                raise HTTPException(422, "avatar must be an image artifact")
            if art.run_id:
                source_run = await session.get(Run, art.run_id)
                if source_run:
                    await assert_readable_run(session, request, source_run)
            view = artifact_store.artifact_view(art)
        row.image_artifact_id = body.artifact_id
        if avatar_run.local_persona_session_id and prior_avatar != body.artifact_id:
            from agentplatform.db import LocalPersonaMutation
            session.add(LocalPersonaMutation(session_id=avatar_run.local_persona_session_id,
                run_id=avatar_run.id, kind='avatar', target_id=row.name,
                before={'image_artifact_id': prior_avatar}, after={'image_artifact_id': body.artifact_id}))
        await session.commit()
        out = profile_view(row, await next_version(session, row.name) - 1)
    await artifact_store.publish_artifact_event(st.producer, event="agent_image", artifact=view, agent=out["name"])
    return out


# --- the village "how I play" section (GEN-11 plan §6.1) ---------------------
# The one writer of a persona's in-game prompt text. Everything outside the
# markers is the persona's own and never changes through here.
PLAY_START = "<!-- village:how-i-play -->"
PLAY_END = "<!-- /village:how-i-play -->"
PLAY_HEADING = "## How I play the village game"
PROMPT_MAX = 64000
_PLAY_RE = re.compile(re.escape(PLAY_START) + r"(.*?)" + re.escape(PLAY_END), re.S)
# Controls (bar newline and tab), zero-width and bidi characters.
_INVISIBLE = re.compile("[\x00-\x08\x0b-\x1f\x7f​-‏‪-‮⁠-⁤﻿]")


class PlaySectionError(Exception):
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status, self.reason = status, reason


def _play_span(prompt: str) -> re.Match | None:
    """The one well-formed section, None when there is none. Malformed (a
    lone marker, two sections) is refused rather than guessed at."""
    found = list(_PLAY_RE.finditer(prompt))
    starts, ends = prompt.count(PLAY_START), prompt.count(PLAY_END)
    if len(found) > 1 or starts != ends or starts != len(found):
        raise PlaySectionError(409, "the prompt's how-I-play markers are malformed")
    return found[0] if found else None


def play_section(prompt: str) -> str:
    """The section's text (heading excluded), "" when absent."""
    span = _play_span(prompt or "")
    if span is None:
        return ""
    body = span[1].strip("\n")
    return body[len(PLAY_HEADING):].strip("\n") if body.startswith(PLAY_HEADING) else body


def hide_play_section(prompt: str) -> str:
    """The prompt as a non-village run sees it under play_section_scope
    village_only: the markers stay, the body goes."""
    empty = PLAY_START + "\n" + PLAY_END
    try:
        span = _play_span(prompt or "")
    except PlaySectionError:
        return _PLAY_RE.sub(empty, prompt)
    if span is None:
        return prompt
    return prompt[:span.start()] + empty + prompt[span.end():]


def guard_play_text(text: str, max_chars: int) -> str:
    """Minimal structural guard so the text can never leave its markers or
    carry tags. U9's textguard (URL refusal, closing-tag neutralizing) is the
    full pass and runs in the engine before this."""
    if not isinstance(text, str):
        raise PlaySectionError(422, "section must be text")
    text = _INVISIBLE.sub("", unicodedata.normalize("NFKC", text))
    text = text.replace("<", "").replace(">", "").strip()
    if len(text) > max_chars:
        raise PlaySectionError(422, f"section is longer than {max_chars} characters")
    return text


def _outside(prompt: str) -> str:
    span = _play_span(prompt)
    return (prompt if span is None else prompt[:span.start()] + prompt[span.end():]).rstrip()


async def apply_play_section(s, agent: str, new_text: str, changed_via: str = "village",
                             expected_version: int | None = None, *,
                             changed_by: str = "system:village", max_chars: int = 1500) -> dict:
    """Replace ONLY the text between the how-I-play markers of `agent`'s prompt
    (appending the section the first time), write one AgentVersion and bump
    `authorization_generation` as any prompt change must. Refuses any result
    that differs outside the markers. Does not commit: the caller commits and
    reloads the agent store. Returns {version, before, after, changed}."""
    row = await s.get(AgentDef, agent, with_for_update=True)
    if row is None:
        raise PlaySectionError(404, "unknown agent")
    if row.agent_type != "persona" or row.system_source:
        raise PlaySectionError(403, "only a persona's own prompt has a how-I-play section")
    version = await next_version(s, agent) - 1
    if expected_version is not None and expected_version != version:
        raise PlaySectionError(409, "the agent changed; read it again")
    text = guard_play_text(new_text, max_chars)
    old = row.prompt or ""
    before = play_section(old)
    block = PLAY_START + "\n" + PLAY_HEADING + "\n" + (text + "\n" if text else "") + PLAY_END
    span = _play_span(old)
    new = (old.rstrip("\n") + "\n\n" + block + "\n" if span is None
           else old[:span.start()] + block + old[span.end():])
    if _outside(new) != _outside(old) or play_section(new) != text:
        raise PlaySectionError(409, "a section edit may not change the prompt outside its markers")
    if len(new) > PROMPT_MAX:
        raise PlaySectionError(422, "the prompt would be too long")
    if new == old:
        return {"version": version, "before": before, "after": text, "changed": False}
    row.prompt = new
    row.authorization_generation = (row.authorization_generation or 0) + 1
    version += 1
    s.add(AgentVersion(agent=agent, version=version, snapshot=snapshot_of(row),
                       changed_by=changed_by[:128], changed_via=changed_via[:32]))
    return {"version": version, "before": before, "after": text, "changed": True}


class PlaySectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str = Field(min_length=1, max_length=128)
    section: str = Field(max_length=4000)
    expected_version: int | None = Field(default=None, ge=0)
    changed_by: str = Field(default="system:village", max_length=128)


@router.post("/api/internal/village/play-section")
async def internal_play_section(request: Request, body: PlaySectionIn):
    """The engine's door to apply_play_section at session close (§6.2):
    the internal secret only (the quota-capture precedent), never an agent key."""
    from agentplatform.api.quota import SECRET_HEADER
    from agentplatform.village.commands import village_rules
    st = request.app.state
    secret = st.settings.internal_secret
    if not secret:
        raise HTTPException(503, "no internal secret configured")
    presented = request.headers.get(SECRET_HEADER, "")
    if not hmac.compare_digest(hashlib.sha256(presented.encode()).digest(),
                               hashlib.sha256(secret.encode()).digest()):
        raise HTTPException(401, "unauthorized")
    async with st.session_factory() as s:
        cap = int((await village_rules(s))["play_section_max_chars"])
        try:
            out = await apply_play_section(s, body.agent, body.section, "village",
                                           body.expected_version, changed_by=body.changed_by,
                                           max_chars=cap)
        except PlaySectionError as exc:
            raise HTTPException(exc.status, exc.reason)
        await s.commit()
    if out["changed"]:
        await st.agent_store.reload()
    return out
