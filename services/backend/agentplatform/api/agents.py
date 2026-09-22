"""The agents API: an agent definition is a ROW, edited directly (design/15).

There is no pending/approval state here any more. A save writes `agent_defs`
immediately and appends a full snapshot to `agent_versions`; "undo" is a
rollback, not a rejected pull request. What used to be enforced by workflow
(only a human with repo access could change an agent) is enforced by
authorization instead, at two levels:

- the admin session may write anything;
- an AGENT may write only what its own row grants it — `agents_edit` for what
  an agent *is* (prompt, config, entrypoints) and `agents_grant` for what it
  may *do* (see GRANT_FIELDS). Splitting them is the whole point: `agents_edit`
  must not be able to escalate, its own agent or any other's.

Validation moved here too. CI used to lint the definition files; now every
write is checked against the code registries (skills, secret declarations,
platform tools) before it lands, so a grant naming something the repo does not
ship is rejected at save time instead of failing at pod launch.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.agentdefs import (DEF_FIELDS, AgentDefModel, apply_snapshot,
                                     model_of, next_version, snapshot_of,
                                     validate_def)
from agentplatform.agentspec import (CODEX_MODELS, GRANTABLE_PLATFORM_TOOLS, KNOWN_MODELS,
                                     TOOL_ARTIFACTS, TOOL_IMAGE_GEN, TOOL_QUOTA,
                                     TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI)
from agentplatform.api.auth import (READ_ROLES, authenticate, require_admin,
                                    require_role, role_allows)
from agentplatform.api.schemas import (AgentCreateIn, AgentDefIn, AgentDefOut,
                                       AgentImageIn, AgentImportResult,
                                       AgentModels, AgentSummary,
                                       AgentVersionDetail, AgentVersionRow,
                                       WebhookSecretIn, WebhookSecretState)
from agentplatform.db import AgentDef, AgentVersion

log = logging.getLogger("agents-api")
router = APIRouter()

# The two code-defined platform tools that let an agent write definitions
# (docs/design/15). Stored grants are full MCP names — the same strings the
# broker matches on — so the check is a plain membership test, no parsing.
TOOL_AGENTS_EDIT = "mcp__platform__agents_edit"
TOOL_AGENTS_GRANT = "mcp__platform__agents_grant"
# The grants new agents are born holding while their setting says so: the three
# participant ones (docs/design/19, docs/design/20, docs/design/21), reading
# how much usage is left (docs/design/22) and the file store (docs/design/23).
# A setting each and not one between them: an operator who wants the messenger
# without the work tracker is asking a reasonable question, and a single switch
# could not answer it. Each entry is (tool, setting), and the payload knob is
# the tool's last segment — `relay`, `tickets`, `wiki`, `get_quota_usage`,
# `artifacts` — so a sixth is one line here.
DEFAULT_GRANTS = ((TOOL_RELAY, "relay_default_grant"),
                  (TOOL_TICKETS, "tickets_default_grant"),
                  (TOOL_WIKI, "wiki_default_grant"),
                  (TOOL_QUOTA, "quota_default_grant"),
                  (TOOL_ARTIFACTS, "artifacts_default_grant"))

# The definition fields that are GRANTS — capability, not identity. Changing
# one is an authorization decision (`agents_grant`); changing anything else is
# an editorial one (`agents_edit`).
#
# Four of these are wider than design/15's four name lists, because privilege in
# this platform is not carried only by name lists:
#
#   `role`       — "dev" enables the Workbench development toolchain and its
#                  platform-mediated publishing path. Writing `role` therefore
#                  changes what an agent can modify and attempt to publish.
#   `can_invoke` — makes the launcher mint an OPERATOR-scoped run token instead
#                  of the narrow annotator one, i.e. the grant of "may start
#                  other agents' runs".
#
#   `push_path_globs` — where a dev run may LAND without review (docs/design/24);
#                  widening it is widening what reaches main unreviewed.
#   `may_delete_tests` — whether a publish may delete a test file, i.e. whether
#                  the agent may take down the thing that checks its work.
#
# All are the escalation the edit/grant split exists to prevent, so all need
# `agents_grant`. The quota thresholds are deliberately NOT here: they only make
# an agent MORE reluctant to run, so `agents_edit` may tune them.
GRANT_FIELDS: tuple[str, ...] = ("harness_tools", "platform_tools", "skills",
                                 "secrets", "can_invoke", "role",
                                 "push_path_globs", "may_delete_tests")
# Everything the definition holds except its identity — the two halves the
# authorization split is drawn between, and the comparison surface for "did
# this write actually change anything".
EDIT_FIELDS: tuple[str, ...] = tuple(f for f in DEF_FIELDS
                                     if f != "name" and f not in GRANT_FIELDS)
MUTABLE_FIELDS: tuple[str, ...] = GRANT_FIELDS + EDIT_FIELDS


# --- who may write, and which half of a definition ---------------------------

@dataclass(frozen=True)
class WriteScope:
    """A verified caller's authority over agent definitions.

    `principal` is what lands in `agent_versions.changed_by` — it comes from
    the auth chain (session name, api-key name, or `sa:<agent>`), never from
    the payload, so attribution cannot be forged by whoever is writing.
    """
    principal: str
    admin: bool
    may_edit: bool
    may_grant: bool

    def changed_via(self, *, grants: bool) -> str:
        """How the change log labels this write. A change that touched grants
        is attributed to the escalation-capable tool even if the same caller
        also holds `agents_edit`: the log should name the stronger authority
        that was actually exercised."""
        if self.admin:
            return "admin"
        return "tool:agents_grant" if grants else "tool:agents_edit"

    def authorize(self, *, grant_fields: list[str], edit_fields: list[str]) -> None:
        """403 unless the caller may change every field it is trying to change.
        A request that mixes the two halves needs both authorities."""
        if grant_fields and not (self.admin or self.may_grant):
            raise HTTPException(403, "changing grants requires the admin session or "
                                     f"the agents_grant tool: {', '.join(grant_fields)}")
        if edit_fields and not (self.admin or self.may_edit):
            raise HTTPException(403, "changing the definition requires the admin session "
                                     f"or the agents_edit tool: {', '.join(edit_fields)}")

    def require_edit(self, what: str) -> None:
        """Guard for a whole-definition action (create, delete) — there are no
        individual fields to name, but it is still the editorial authority."""
        if not (self.admin or self.may_edit):
            raise HTTPException(403, f"{what} requires the admin session or the "
                                     "agents_edit tool")


async def _caller_platform_tools(request: Request, agent: str) -> list[str]:
    """The grant set the CALLER acts under. A run JWT froze its grants at
    launch (design/13 C), so a grant added mid-run must not widen the run that
    is already using its token; without one, the agent's current row is the
    truth. Same rule, same order, as /api/whoami."""
    frozen = getattr(request.state, "frozen_tools", None)
    if frozen is not None:
        return list(frozen)
    store = request.app.state.agent_store
    await store.reload()
    info = store.get(agent)
    return list(info.platform_tools) if info else []


async def agent_write_scope(request: Request) -> WriteScope:
    """Dependency: resolve the caller to a `WriteScope`, or 401/403.

    Deliberately NOT built on `require_role`. An agent that holds a custom
    platform tool lands on the `tools` rung of the design-12 ladder, which
    satisfies no endpoint allow-list at all — its authority to write a
    definition comes from the GRANT, not from a role. Task 4's tool-executor
    path resolves the same way (an agent-bound bearer), so this is the one
    place the rule lives.
    """
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    name, role = ident
    if role == "admin":
        return WriteScope(name, admin=True, may_edit=True, may_grant=True)
    agent = getattr(request.state, "api_key_agent", None)
    granted = await _caller_platform_tools(request, agent) if agent else []
    scope = WriteScope(name, admin=False,
                       may_edit=TOOL_AGENTS_EDIT in granted,
                       may_grant=TOOL_AGENTS_GRANT in granted)
    if not (scope.may_edit or scope.may_grant):
        raise HTTPException(403, "writing agent definitions requires the admin session "
                                 "or an agent granted agents_edit / agents_grant")
    return scope


async def agent_read_access(request: Request) -> str:
    """Dependency for the definition READS: `READ_ROLES`, or anyone the write
    side would accept.

    If you can edit an agent, you can read it. The tools that write definitions
    (`agents_edit` / `agents_grant`) put their holder on the design-12 `tools`
    rung, which satisfies no role allow-list — so without this the grant would
    be write-only, and a full-replacement PUT is unusable without a read: both
    tools work read-modify-write, precisely so that editing prose leaves grants
    exactly as they were and vice versa. Widening reads to the callers who may
    already rewrite the row leaks nothing they could not have discovered by
    writing to it.

    Admin and READ_ROLES behaviour is unchanged; a `tools` agent holding
    neither definition tool is still refused.
    """
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    name, role = ident
    if role_allows(role, READ_ROLES):
        return name
    agent = getattr(request.state, "api_key_agent", None)
    granted = await _caller_platform_tools(request, agent) if agent else []
    if TOOL_AGENTS_EDIT in granted or TOOL_AGENTS_GRANT in granted:
        return name
    raise HTTPException(403)


# --- validation --------------------------------------------------------------

def _registries(request: Request) -> dict[str, set[str]]:
    """The code-defined names a definition may reference. Capability is code
    (docs/design/15): a grant naming a skill, secret or tool the repo does not
    ship is a dead grant, and this is where CI's file linting went."""
    st = request.app.state
    st.skill_store.reload()
    st.secret_registry.reload()
    st.tool_registry.reload()
    return {"skill_names": {s.name for s in st.skill_store.list()},
            "secret_names": {s.name for s in st.secret_registry.list()},
            # Every broker tool that exists is grantable — including the two
            # definition-writing ones, which are code-defined like the rest but
            # live on their own auth rung (see agentspec).
            "tool_names": set(GRANTABLE_PLATFORM_TOOLS) | set(st.tool_registry.mcp_names())}


def _model(request: Request, payload: dict, name: str,
           registries: dict[str, set[str]]) -> AgentDefModel:
    """Payload + authoritative name → a validated definition, or 422. Shape and
    semantics (role, cron, slug) come from the model; grant existence from the
    registries."""
    try:
        model = AgentDefModel(**{**payload, "name": name})
    except ValidationError as e:
        raise HTTPException(422, "; ".join(
            f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in e.errors()))
    problems = validate_def(model, **registries)
    if problems:
        raise HTTPException(422, "; ".join(problems))
    return model


async def _check_webhook_conflicts(session, models: list[AgentDefModel]) -> None:
    """422 if any model's `entrypoints.webhooks` path is already declared by an
    agent NOT part of this write.

    `webhooks.py` (api/webhooks.py:27) routes an inbound POST to the first
    alphabetical declarer of a path — two agents declaring the same path
    silently collide, with the second one just never firing. `validate_def` is
    IO-free (agentdefs.py's module docstring), so this lives here instead,
    where the store/DB is reachable.

    Checked against every OTHER agent's stored definition, plus — for a
    multi-definition import — every other definition in the same batch. The
    names being written in this call are excluded from "other", so an agent
    re-declaring its own path always passes."""
    names = {m.name for m in models}
    rows = (await session.execute(select(AgentDef.name, AgentDef.entrypoints))).all()
    owners: dict[str, str] = {}
    for name, entrypoints in rows:
        if name in names:
            continue  # this row is being overwritten by the current write
        for w in (entrypoints or {}).get("webhooks") or []:
            path = w.get("path")
            if path:
                owners.setdefault(path, name)
    for model in models:
        for w in model.entrypoints.webhooks:
            owner = owners.get(w.path)
            if owner is not None:
                raise HTTPException(422,
                    f"webhook path {w.path!r} is already declared by agent "
                    f"{owner!r} (conflicts with {model.name!r})")
            owners[w.path] = model.name


# --- row <-> wire ------------------------------------------------------------

def _payload(row: AgentDef) -> dict:
    """A row as the API returns it, read straight off the columns rather than
    through AgentDefModel. A QUARANTINED row (one that no longer validates)
    must stay readable, because reading it is how you fix it. Nones are dropped
    so the response model's defaults fill in for a row written before a column
    existed."""
    return {f: v for f in DEF_FIELDS if (v := getattr(row, f, None)) is not None}


def _with_secret_state(payload: dict, secret_paths: set[str]) -> dict:
    """Annotate a definition's webhook entries with `secret_set` (design/16).

    DERIVED on the way out, never stored: the secret's existence is a fact
    about `webhook_secrets`, not about the definition, so it must not reach
    `entrypoints` (and through it every future `agent_versions` snapshot). The
    payload is rebuilt rather than mutated for the same reason — the dict here
    is the ORM row's own JSON value, and writing into it would dirty the row.

    Defensive about shape for the same reason `_payload` reads columns raw: a
    definition whose entrypoints blob is malformed must still be readable, and
    that is what the editor's whole-blob PUT repairs. Anything that isn't a
    recognizable webhook entry passes through untouched — there is no secret it
    could be reporting on, and the tolerated-garbage read stays verbatim.
    """
    entrypoints = payload.get("entrypoints")
    if not isinstance(entrypoints, dict):
        return payload
    hooks = entrypoints.get("webhooks")
    if not isinstance(hooks, list):
        return payload
    annotated = [{**w, "secret_set": w["path"] in secret_paths}
                 if isinstance(w, dict) and isinstance(w.get("path"), str) else w
                 for w in hooks]
    return {**payload, "entrypoints": {**entrypoints, "webhooks": annotated}}


def _with_face(payload: dict, row: AgentDef, faces: dict[str, dict]) -> dict:
    """The picture and the face (docs/design/23), which are not definition
    fields — `_payload` reads DEF_FIELDS — but ride out with every read so the
    Agents pages need no second fetch. The face comes from `faces_for`, the
    one renderer every room, ticket and wiki card also uses."""
    return {**payload, "image_artifact_id": row.image_artifact_id, "face": faces[row.name]}


async def _annotated(session, row: AgentDef) -> dict:
    """One row as the API returns it, `secret_set` and the face included."""
    from agentplatform import webhooksecrets
    from agentplatform.relay_store import faces_for
    return _with_face(
        _with_secret_state(_payload(row),
                           await webhooksecrets.paths_with_secrets(session, row.name)),
        row, await faces_for(session, {row.name}))


async def _prune_webhook_secrets(session, model: AgentDefModel) -> None:
    """After a definition lands, drop secrets for paths it no longer declares.

    A secret is scoped to a path; removing the path removes the thing the
    secret guards, and keeping the row would mean re-declaring that path later
    silently re-arms a credential nobody can see (docs/design/16)."""
    from agentplatform import webhooksecrets
    await webhooksecrets.prune_undeclared(
        session, model.name, {w.path for w in model.entrypoints.webhooks})


def _value(model: AgentDefModel, field: str):
    """One definition field as it is stored/compared — nested models flattened
    to plain JSON, which is what the column holds."""
    value = getattr(model, field)
    return value.model_dump(mode="json") if isinstance(value, BaseModel) else value


def _apply(row: AgentDef, model: AgentDefModel) -> None:
    """Write a validated definition onto a row. Full replacement: every field
    of the definition comes from the model, so what is stored always equals
    what the caller sent."""
    for field in MUTABLE_FIELDS:          # identity is the pk; it never moves
        setattr(row, field, _value(model, field))


def _with_grants(tools: list[str], grants: list[str]) -> list[str]:
    """`tools` plus these grants — idempotent, and a new list: the one it is
    given is the model's own, and mutating that would edit the definition the
    caller sent out from under the authorization diff."""
    return [*tools, *(g for g in grants if g not in tools)]


def _knob(tool: str) -> str:
    """The create payload's field for a grant: `mcp__platform__relay` -> `relay`."""
    return tool.rsplit("__", 1)[-1]


def _asked_for(body) -> list[str]:
    """The participant grants the CALLER asked for outright.

    A caller asking is a caller granting, so these ride in through the model
    and are authorized exactly as if they had written the tool into
    `platform_tools` themselves."""
    return [tool for tool, _ in DEFAULT_GRANTS if getattr(body, _knob(tool)) is True]


def _by_default(settings, body) -> list[str]:
    """The participant grants the PLATFORM adds of its own accord.

    Each is a tri-state on the create payload: None follows the setting, False
    is the explicit opt-out, True is the caller asking (see `_asked_for`). Only
    the None case is a platform default."""
    return [tool for tool, setting in DEFAULT_GRANTS
            if getattr(body, _knob(tool)) is None and getattr(settings, setting)]


async def _log_version(session, row: AgentDef, *, changed_by: str, changed_via: str):
    """Append the row's current definition to the change log. Called after the
    row is flushed so the snapshot is what actually landed."""
    session.add(AgentVersion(agent=row.name,
                             version=await next_version(session, row.name),
                             snapshot=snapshot_of(row),
                             changed_by=changed_by, changed_via=changed_via))


def _conflict_detail(exc: Exception, duplicate: str | None) -> str:
    """Which conflict a lost race was. Best effort by constraint text — the
    wording differs between sqlite ("UNIQUE constraint failed: agent_defs.name")
    and postgres ("...unique constraint \"agent_defs_pkey\"") but both name the
    table, and the change log's constraint names `agent_versions` instead. An
    unrecognized one falls back to the generic conflict rather than guessing."""
    if duplicate and "agent_defs" in str(getattr(exc, "orig", None) or exc):
        return duplicate
    return "conflicting concurrent write, retry"


@asynccontextmanager
async def _conflict_as_409(session, *, duplicate: str | None = None):
    """Turn a lost write race into a 409 instead of a 500.

    Two writers can collide on the agent's primary key (simultaneous creates)
    or on the change log's (agent, version) unique constraint — `next_version`
    is a read-then-write, and Task 1 added that constraint precisely so the
    loser fails loudly rather than filing two snapshots under one version.
    Loudly should still mean "someone got there first, try again", not a
    server fault.
    """
    try:
        yield
    except IntegrityError as e:
        await session.rollback()
        raise HTTPException(409, _conflict_detail(e, duplicate)) from e


def _changed_fields(row: AgentDef, model: AgentDefModel, fields) -> list[str]:
    """Which of `fields` this definition would actually change.

    Both sides go through AgentDefModel first, so the comparison is between
    normalized definitions rather than between a payload and raw columns:
    re-saving an unchanged agent is a no-op even though the row stores
    `entrypoints` as `{}` and the model as the full four-key shape. A row that
    no longer validates is treated as wholly changed — every field of a
    quarantined agent is in play, and it needs the authority to match.
    """
    try:
        current = model_of(row)
    except ValidationError:
        return list(fields)
    return [f for f in fields if _value(model, f) != _value(current, f)]


# --- readiness ---------------------------------------------------------------

async def _blocked_reasons(request: Request) -> dict[str, str]:
    """agent -> blocking reason, for agents whose derived secret dependencies
    (manifest secrets + skills' secrets) have an unmet REQUIRED one. Distinct
    from quarantined: blocked is fixed by fixing the secret, quarantined by
    fixing the agent."""
    from agentplatform import readiness
    from agentplatform.db import SecretMeta
    skills = request.app.state.skill_store
    skills.reload()
    agents = [a for a in request.app.state.agent_store.list() if a.manifest]
    dep_names = {d.secret for a in agents
                 for d in readiness.deps_for(a.manifest, skills)}
    if not dep_names:
        return {}
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(SecretMeta)
                .where(SecretMeta.name.in_(dep_names)))).scalars()
        statuses = {m.name: m.status for m in rows}
    for n in dep_names - set(statuses):
        # No meta row yet — the store is the truth for existence (out-of-band set).
        if await request.app.state.secret_store.exists(n):
            statuses[n] = "unprobed"
    out = {}
    for a in agents:
        reason = readiness.blocking_reason(a.manifest, skills, statuses)
        if reason:
            out[a.name] = reason
    return out


# --- read --------------------------------------------------------------------

@router.get("/api/agents", response_model=list[AgentSummary],
            dependencies=[Depends(agent_read_access)])
async def list_agents(request: Request):
    """Every agent's full definition plus server-derived readiness. The
    definition comes from the rows; `quarantined`/`error`/`blocked`/`schedule`
    are things only the platform knows, so they ride alongside rather than
    pretending to be columns."""
    from agentplatform import webhooksecrets
    from agentplatform.relay_store import faces_for
    store = request.app.state.agent_store
    await store.reload()
    blocked = await _blocked_reasons(request)
    infos = {a.name: a for a in store.list()}
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(AgentDef).order_by(AgentDef.name))).scalars().all()
        # One query each for the whole listing rather than one per agent.
        secret_paths = await webhooksecrets.secrets_by_agent(s)
        faces = await faces_for(s, {row.name for row in rows})
    out = []
    for row in rows:
        info = infos.get(row.name)
        out.append({**_with_face(_with_secret_state(_payload(row),
                                                    secret_paths.get(row.name, set())),
                                 row, faces),
                    "quarantined": info is not None and info.error is not None,
                    "error": info.error if info else None,
                    "blocked": row.name in blocked,
                    "blocked_reason": blocked.get(row.name),
                    "schedule": ", ".join(info.crons()) if info else ""})
    return out


@router.get("/api/agents/{name}", response_model=AgentDefOut,
            dependencies=[Depends(agent_read_access)])
async def get_agent(request: Request, name: str):
    async with request.app.state.session_factory() as s:
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        return await _annotated(s, row)


@router.get("/api/agent-models", response_model=AgentModels,
            dependencies=[Depends(require_role(*READ_ROLES))])
async def agent_models():
    """Runtime-specific model catalogs for the agent editor."""
    return {"models": KNOWN_MODELS, "codex_models": CODEX_MODELS}


# --- write -------------------------------------------------------------------

@router.post("/api/agents", status_code=201, response_model=AgentDefOut)
async def create_agent(request: Request, body: AgentCreateIn,
                       scope: WriteScope = Depends(agent_write_scope)):
    """Create an agent. It exists — and runs, if the definition declares an
    entrypoint — the moment this returns; there is no merge to wait for.

    Creating WITH grants needs `agents_grant` for the same reason editing them
    does: otherwise `agents_edit` escalates through the side door by minting a
    new agent that already holds the keys."""
    st = request.app.state
    scope.require_edit("creating an agent")
    # Knobs about the write, not fields of the agent.
    payload = body.model_dump(exclude={_knob(t) for t, _ in DEFAULT_GRANTS})
    payload["platform_tools"] = _with_grants(payload["platform_tools"], _asked_for(body))
    model = _model(request, payload, body.name, _registries(request))
    # A grant the new agent is BORN with is still a grant. "Born with" means
    # beyond the defaults, which is what a blank row reads as — so the same
    # diff that authorizes an update authorizes a create.
    grants = _changed_fields(AgentDef(name=model.name), model, GRANT_FIELDS)
    scope.authorize(grant_fields=grants, edit_fields=[])
    if model.system and not scope.admin:
        raise HTTPException(403, "only an admin may create a system agent")
    async with st.session_factory() as s:
        await _check_webhook_conflicts(s, [model])
        if await s.get(AgentDef, model.name) is not None:
            raise HTTPException(409, "an agent with that name already exists")
        row = AgentDef(name=model.name)
        _apply(row, model)
        # Applied AFTER the authorization above, on purpose: a grant the
        # PLATFORM gives every new agent is not the caller escalating, so an
        # `agents_edit`-only creator must not be refused for it, and the write
        # must not be relabelled `tool:agents_grant` as though they had handed
        # it over. It still lands on the row before the flush, so the version
        # snapshot is the definition that actually exists.
        row.platform_tools = _with_grants(row.platform_tools,
                                          _by_default(st.settings, body))
        async with _conflict_as_409(s, duplicate="an agent with that name "
                                                 "already exists"):
            s.add(row)
            await s.flush()
            # Unreachable today — deletion already clears an agent's secrets,
            # so a fresh name has none. Here anyway so "a secret outlives no
            # path" is an invariant of every write that lands, not a property
            # of three of the four.
            await _prune_webhook_secrets(s, model)
            await _log_version(s, row, changed_by=scope.principal,
                               changed_via=scope.changed_via(grants=bool(grants)))
            await s.commit()
        out = await _annotated(s, row)
    await st.agent_store.reload()
    return out


@router.put("/api/agents/{name}", response_model=AgentDefOut)
async def update_agent(request: Request, name: str, body: AgentDefIn,
                       scope: WriteScope = Depends(agent_write_scope)):
    """Replace an agent's definition. The body is the WHOLE definition — an
    omitted field resets to its default — and any `name` in it is ignored: the
    path identifies the agent, so a payload can never rename or retarget one.

    A save that changes nothing is a no-op: it returns the row and appends no
    version, because the change log records changes, not visits."""
    st = request.app.state
    async with st.session_factory() as s:
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        model = _model(request, body.model_dump(), name, _registries(request))
        await _check_webhook_conflicts(s, [model])
        grants = _changed_fields(row, model, GRANT_FIELDS)
        edits = _changed_fields(row, model, EDIT_FIELDS)
        scope.authorize(grant_fields=grants, edit_fields=edits)
        if "system" in edits and not scope.admin:
            # The system flag protects platform-managed lifecycle. It grants no
            # authority, but an agent still must not make itself undeletable.
            raise HTTPException(403, "only an admin may change the system flag")
        if not (grants or edits):
            return await _annotated(s, row)
        _apply(row, model)
        async with _conflict_as_409(s):
            await s.flush()
            await _prune_webhook_secrets(s, model)
            await _log_version(s, row, changed_by=scope.principal,
                               changed_via=scope.changed_via(grants=bool(grants)))
            await s.commit()
        out = await _annotated(s, row)
    await st.agent_store.reload()
    return out


@router.delete("/api/agents/{name}", response_model=AgentDefOut)
async def delete_agent(request: Request, name: str,
                       scope: WriteScope = Depends(agent_write_scope)):
    """Delete an agent's definition. Its runs, memories and change log survive.

    Deleting is a write, so it logs one: a TOMBSTONE version whose snapshot is
    the definition as it stood at the moment of deletion, and whose
    `changed_via` is the normal label prefixed `delete:` (`delete:admin`,
    `delete:tool:agents_edit`). The prefix is the whole marker — snapshots stay
    uniformly parseable as definitions, with no synthetic keys inside them — and
    it means the log alone is enough to say who removed an agent and to
    recreate it. System agents are platform-managed and refuse deletion.

    Its webhook secrets do NOT survive: they are credentials for paths that no
    longer exist, and the change log's tombstone snapshot deliberately doesn't
    carry them, so leaving the rows behind would only mean a recreated agent
    silently inheriting a secret nobody can see (docs/design/16).
    """
    from agentplatform import webhooksecrets
    st = request.app.state
    scope.require_edit("deleting an agent")
    async with st.session_factory() as s:
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        if row.system:
            raise HTTPException(409, "system agents are platform-managed and "
                                     "cannot be deleted")
        out = await _annotated(s, row)
        async with _conflict_as_409(s):
            await _log_version(s, row, changed_by=scope.principal,
                               changed_via=f"delete:{scope.changed_via(grants=False)}")
            await webhooksecrets.clear_agent_secrets(s, name)
            await s.delete(row)
            await s.commit()
    await st.agent_store.reload()
    return out


# --- the agent's picture (docs/design/23) ------------------------------------
#
# Its own route, outside the definition, for `icon`'s reason: what an agent
# looks like is not what it is. The picture never enters a snapshot, so a
# rollback leaves it alone, and the change log stays about the definition.
# Authority is wider than a definition edit on purpose — the agent ITSELF may
# choose its face (that is what the artist does after it draws one), which is
# harmless in a way that editing its own prompt or grants would not be.

async def _may_set_image(s, request: Request, name: str) -> None:
    """Admin, an `agents_edit` holder, or the agent itself — else 403.

    "Itself" is the run token's run, not the token's agent claim alone: the
    actor↔run invariant every participant block keeps (`tickets._run_of`), so
    a token whose run is gone cannot dress anyone. And it needs an artifact
    grant, as `require_artifacts_access` asks: the picture lives behind that
    door, and choosing one is a use of it."""
    from agentplatform.api.relay import Caller
    from agentplatform.api.tickets import _run_of
    from agentplatform.relay import participant_of
    ident = await authenticate(request)
    if ident is None:
        raise HTTPException(401)
    principal, role = ident
    if role == "admin":
        return
    agent = getattr(request.state, "api_key_agent", None)
    if agent is not None:
        granted = await _caller_platform_tools(request, agent)
        if TOOL_AGENTS_EDIT in granted:
            return
        run = await _run_of(s, request, Caller(participant_of(agent=agent), agent, principal),
                            writing=True)
        if run.agent == name and (TOOL_ARTIFACTS in granted or TOOL_IMAGE_GEN in granted):
            return
    raise HTTPException(403, "an agent's image is set by the admin session, an agents_edit "
                             "holder, or the agent itself")


@router.put("/api/agents/{name}/image", response_model=AgentDefOut)
async def set_agent_image(request: Request, name: str, body: AgentImageIn):
    """Set or clear the agent's picture. The artifact must be a live image:
    a document would render nothing, and a deleted one is a 404 like every
    other read of it. Publishes `agent_image` on `artifacts.events` — with
    `artifact: null` for a clear — so the Studio and the #art feed see the
    face change as they see the picture land."""
    from agentplatform import artifact_store
    st = request.app.state
    async with st.session_factory() as s:
        # Authority first, as a dependency would have it: a stranger learns
        # nothing about which agents exist from a 401 versus a 404.
        await _may_set_image(s, request, name)
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        view = None
        if body.artifact_id is not None:
            art = await artifact_store.get(s, body.artifact_id)
            if art is None:
                raise HTTPException(404, "unknown artifact")
            if art.kind != "image":
                raise HTTPException(422, "an agent's image must be an image artifact")
            view = artifact_store.artifact_view(art)
        row.image_artifact_id = body.artifact_id
        await s.commit()
        out = await _annotated(s, row)
    await artifact_store.publish_artifact_event(st.producer, event="agent_image",
                                               artifact=view, agent=name)
    return out


# --- webhook secrets (docs/design/16) ----------------------------------------
#
# Their own endpoints, deliberately outside the definition. A webhook secret is
# a credential, and every route a definition field travels — the full-
# replacement PUT, the `agent_versions` snapshot, rollback, import/export, the
# GET the UI renders — is a route it must never travel. Keeping it in its own
# table behind its own write-only endpoint is what makes "the mode is
# versioned, the secret is not" true by construction rather than by care.
#
# Authority is the entrypoints-edit authority (admin or `agents_edit`): whoever
# may declare the path may set the secret guarding it. `agents_grant` alone
# cannot — that half of the split is about what an agent may DO.

async def _declared_webhook_paths(session, name: str) -> set[str]:
    """The webhook paths an agent declares, or 404 if there is no such agent.
    Read off the row rather than the store cache: a path declared moments ago
    must be settable immediately, and the row is the truth the store copies."""
    row = await session.get(AgentDef, name)
    if row is None:
        raise HTTPException(404, "unknown agent")
    entrypoints = row.entrypoints if isinstance(row.entrypoints, dict) else {}
    hooks = entrypoints.get("webhooks")
    return {w["path"] for w in (hooks if isinstance(hooks, list) else [])
            if isinstance(w, dict) and w.get("path")}


async def _require_declared(session, name: str, path: str) -> None:
    if path not in await _declared_webhook_paths(session, name):
        raise HTTPException(404, "this agent does not declare that webhook path")


@router.put("/api/agents/{name}/webhooks/{path}/secret",
            response_model=WebhookSecretState)
async def set_webhook_secret(request: Request, name: str, path: str,
                             body: WebhookSecretIn,
                             scope: WriteScope = Depends(agent_write_scope)):
    """Set or rotate one webhook path's shared secret. Write-only: the response
    reports THAT a secret is set, never what it is, and nothing reads it back.
    Rotation replaces the stored digest in place, so the previous secret stops
    working the moment this returns. Length bounds live on `WebhookSecretIn`
    rather than here so they reach the OpenAPI spec and the generated SDK."""
    from agentplatform import webhooksecrets
    scope.require_edit("setting a webhook secret")
    async with request.app.state.session_factory() as s:
        await _require_declared(s, name, path)
        await webhooksecrets.set_secret(s, name, path, body.secret)
        await s.commit()
    # Attribution without the value: who rotated what, never what it became.
    log.info("webhook secret set for %s/%s by %s", name, path, scope.principal)
    return {"agent": name, "path": path, "secret_set": True}


@router.delete("/api/agents/{name}/webhooks/{path}/secret",
               response_model=WebhookSecretState)
async def delete_webhook_secret(request: Request, name: str, path: str,
                                scope: WriteScope = Depends(agent_write_scope)):
    """Remove one webhook path's secret. Idempotent — the caller asked for "no
    secret on this path", and that is the state either way. A path still in
    `secret` mode with no secret is the fail-closed case: it rejects callers
    rather than falling back to the platform key alone."""
    from agentplatform import webhooksecrets
    scope.require_edit("clearing a webhook secret")
    async with request.app.state.session_factory() as s:
        await _require_declared(s, name, path)
        await webhooksecrets.clear_secret(s, name, path)
        await s.commit()
    log.info("webhook secret cleared for %s/%s by %s", name, path, scope.principal)
    return {"agent": name, "path": path, "secret_set": False}


# --- change log --------------------------------------------------------------

@router.get("/api/agents/{name}/versions", response_model=list[AgentVersionRow],
            dependencies=[Depends(agent_read_access)])
async def list_agent_versions(request: Request, name: str):
    """The agent's change log, newest first. Snapshots are omitted — they are
    whole definitions, and a busy agent's history would be megabytes."""
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(
            select(AgentVersion).where(AgentVersion.agent == name)
            .order_by(AgentVersion.version.desc()))).scalars().all()
    return [{"version": v.version, "changed_by": v.changed_by,
             "changed_via": v.changed_via,
             "created_at": v.created_at.isoformat() if v.created_at else None}
            for v in rows]


@router.get("/api/agents/{name}/versions/{version}", response_model=AgentVersionDetail,
            dependencies=[Depends(agent_read_access)])
async def get_agent_version(request: Request, name: str, version: int):
    """One logged version, snapshot included — what the history view diffs
    against and what a rollback would re-apply."""
    async with request.app.state.session_factory() as s:
        v = (await s.execute(select(AgentVersion).where(
            AgentVersion.agent == name,
            AgentVersion.version == version))).scalar_one_or_none()
    if v is None:
        raise HTTPException(404, "unknown version")
    return {"version": v.version, "changed_by": v.changed_by,
            "changed_via": v.changed_via, "snapshot": v.snapshot,
            "created_at": v.created_at.isoformat() if v.created_at else None}


@router.post("/api/agents/{name}/rollback/{version}", response_model=AgentDefOut)
async def rollback_agent(request: Request, name: str, version: int,
                         principal: str = Depends(require_admin)):
    """Re-apply a logged snapshot as a NEW version. Rollback is a write like
    any other — the log is append-only, so undoing is recorded rather than
    erased, and it stays admin-only because it can restore any past grant set.

    The restored definition is re-validated: a snapshot naming a skill or tool
    the repo has since dropped is a dead grant now, and re-applying it would
    just quarantine the agent later."""
    st = request.app.state
    async with st.session_factory() as s:
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        v = (await s.execute(select(AgentVersion).where(
            AgentVersion.agent == name,
            AgentVersion.version == version))).scalar_one_or_none()
        if v is None:
            raise HTTPException(404, "unknown version")
        try:
            apply_snapshot(row, v.snapshot)
        except ValidationError as e:
            raise HTTPException(422, f"version {version} is no longer a valid "
                                     f"definition: {e}")
        restored = model_of(row)
        problems = validate_def(restored, **_registries(request))
        if problems:
            raise HTTPException(422, f"version {version} references things the "
                                     f"repo no longer ships: {'; '.join(problems)}")
        # A snapshot's webhook paths are re-checked against everyone else's
        # CURRENT definitions, not against the world as it stood when the
        # snapshot was taken. Since design/16 the path is the auth routing key:
        # a rollback that resurrected a path another agent has since claimed
        # would silently re-point that path — and with it whichever secret
        # guards it — at the wrong agent.
        await _check_webhook_conflicts(s, [restored])
        async with _conflict_as_409(s):
            await s.flush()
            # The MODE is restorable; the secret never was. A rolled-back
            # definition that drops a path drops its secret with it.
            await _prune_webhook_secrets(s, restored)
            await _log_version(s, row, changed_by=principal, changed_via="rollback")
            await s.commit()
        out = await _annotated(s, row)
    await st.agent_store.reload()
    return out


# --- import ------------------------------------------------------------------

@router.post("/api/agents/import", response_model=list[AgentImportResult])
async def import_agents(request: Request, body: list[AgentCreateIn],
                        principal: str = Depends(require_admin)):
    """Idempotent bulk upsert of whole definitions — the one-shot migration
    path (docs/design/15) and the way a set of agents is seeded into a fresh
    cluster. Re-running the same payload is a no-op that logs nothing, so it is
    safe to run twice.

    All-or-nothing: every definition is validated before any of them is
    written, because a half-applied import leaves the platform in a state
    nobody described."""
    registries = _registries(request)
    settings = request.app.state.settings
    models = []
    for d in body:
        payload = d.model_dump(exclude={_knob(t) for t, _ in DEFAULT_GRANTS})
        # Unlike create, the participant defaults go into the DEFINITION here
        # rather than onto the row afterwards. Import is an admin-only UPSERT,
        # so there is no authorization diff to keep them out of — and there is
        # an idempotence promise to keep: a grant applied after the comparison
        # would make every re-run of the same payload an "update" that logs a
        # version, which is exactly what this endpoint says it does not do.
        payload["platform_tools"] = _with_grants(
            payload["platform_tools"], _asked_for(d) + _by_default(settings, d))
        models.append(_model(request, payload, d.name, registries))
    results = []
    async with request.app.state.session_factory() as s:
        await _check_webhook_conflicts(s, models)
        async with _conflict_as_409(s):
            for model in models:
                row = await s.get(AgentDef, model.name)
                if row is None:
                    row, status = AgentDef(name=model.name), "created"
                    s.add(row)
                elif _changed_fields(row, model, MUTABLE_FIELDS):
                    status = "updated"
                else:
                    results.append({"name": model.name, "status": "unchanged"})
                    continue
                _apply(row, model)
                await s.flush()
                await _prune_webhook_secrets(s, model)
                await _log_version(s, row, changed_by=principal, changed_via="import")
                results.append({"name": model.name, "status": status})
            await s.commit()
    await request.app.state.agent_store.reload()
    return results
