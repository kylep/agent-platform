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
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from agentplatform.agentdefs import (DEF_FIELDS, AgentDefModel, apply_snapshot,
                                     model_of, next_version, snapshot_of,
                                     validate_def)
from agentplatform.agentspec import KNOWN_MODELS, PLATFORM_MCP_TOOLS
from agentplatform.api.auth import (READ_ROLES, authenticate, require_admin,
                                    require_role)
from agentplatform.api.schemas import (AgentCreateIn, AgentDefIn, AgentDefOut,
                                       AgentImportResult, AgentModels,
                                       AgentSummary, AgentVersionDetail,
                                       AgentVersionRow)
from agentplatform.db import AgentDef, AgentVersion

log = logging.getLogger("agents-api")
router = APIRouter()

# The two code-defined platform tools that let an agent write definitions
# (docs/design/15). Stored grants are full MCP names — the same strings the
# broker matches on — so the check is a plain membership test, no parsing.
TOOL_AGENTS_EDIT = "mcp__platform__agents_edit"
TOOL_AGENTS_GRANT = "mcp__platform__agents_grant"

# The definition fields that are GRANTS — capability, not identity. Changing
# one is an authorization decision (`agents_grant`); changing anything else is
# an editorial one (`agents_edit`).
#
# `can_invoke` is in here even though design/15 lists only the four name lists.
# It is not a config knob: it is what makes the launcher mint an OPERATOR-scoped
# run token instead of the narrow annotator one, i.e. the grant of "may start
# other agents' runs". Leaving it on the editorial side would hand `agents_edit`
# exactly the escalation the split exists to prevent.
GRANT_FIELDS: tuple[str, ...] = ("harness_tools", "platform_tools", "skills",
                                 "secrets", "can_invoke")
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
            "tool_names": set(PLATFORM_MCP_TOOLS) | set(st.tool_registry.mcp_names())}


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


# --- row <-> wire ------------------------------------------------------------

def _payload(row: AgentDef) -> dict:
    """A row as the API returns it, read straight off the columns rather than
    through AgentDefModel. A QUARANTINED row (one that no longer validates)
    must stay readable, because reading it is how you fix it. Nones are dropped
    so the response model's defaults fill in for a row written before a column
    existed."""
    return {f: v for f in DEF_FIELDS if (v := getattr(row, f, None)) is not None}


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


async def _log_version(session, row: AgentDef, *, changed_by: str, changed_via: str):
    """Append the row's current definition to the change log. Called after the
    row is flushed so the snapshot is what actually landed."""
    session.add(AgentVersion(agent=row.name,
                             version=await next_version(session, row.name),
                             snapshot=snapshot_of(row),
                             changed_by=changed_by, changed_via=changed_via))


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
            dependencies=[Depends(require_role(*READ_ROLES))])
async def list_agents(request: Request):
    """Every agent's full definition plus server-derived readiness. The
    definition comes from the rows; `quarantined`/`error`/`blocked`/`schedule`
    are things only the platform knows, so they ride alongside rather than
    pretending to be columns."""
    store = request.app.state.agent_store
    await store.reload()
    blocked = await _blocked_reasons(request)
    infos = {a.name: a for a in store.list()}
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(AgentDef).order_by(AgentDef.name))).scalars().all()
    out = []
    for row in rows:
        info = infos.get(row.name)
        out.append({**_payload(row),
                    "quarantined": info is not None and info.error is not None,
                    "error": info.error if info else None,
                    "blocked": row.name in blocked,
                    "blocked_reason": blocked.get(row.name),
                    "schedule": ", ".join(info.crons()) if info else ""})
    return out


@router.get("/api/agents/{name}", response_model=AgentDefOut,
            dependencies=[Depends(require_role(*READ_ROLES))])
async def get_agent(request: Request, name: str):
    async with request.app.state.session_factory() as s:
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        return _payload(row)


@router.get("/api/agent-models", response_model=AgentModels,
            dependencies=[Depends(require_role(*READ_ROLES))])
async def agent_models():
    """Models the UI offers in the model picker. Advisory — the server accepts
    any model string, so new models work before this list is updated."""
    return {"models": KNOWN_MODELS}


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
    model = _model(request, body.model_dump(), body.name, _registries(request))
    # A grant the new agent is born with is still a grant.
    scope.authorize(grant_fields=[f for f in GRANT_FIELDS if getattr(model, f)],
                    edit_fields=[])
    if model.system and not scope.admin:
        raise HTTPException(403, "only an admin may create a system agent")
    async with st.session_factory() as s:
        if await s.get(AgentDef, model.name) is not None:
            raise HTTPException(409, "an agent with that name already exists")
        row = AgentDef(name=model.name)
        _apply(row, model)
        s.add(row)
        await s.flush()
        await _log_version(s, row, changed_by=scope.principal,
                           changed_via=scope.changed_via(
                               grants=any(getattr(model, f) for f in GRANT_FIELDS)))
        await s.commit()
        out = _payload(row)
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
        grants = _changed_fields(row, model, GRANT_FIELDS)
        edits = _changed_fields(row, model, EDIT_FIELDS)
        scope.authorize(grant_fields=grants, edit_fields=edits)
        if "system" in edits and not scope.admin:
            # The system flag is what protects an agent from deletion and gets
            # it platform credentials injected — an agent must not set it.
            raise HTTPException(403, "only an admin may change the system flag")
        if not (grants or edits):
            return _payload(row)
        _apply(row, model)
        await s.flush()
        await _log_version(s, row, changed_by=scope.principal,
                           changed_via=scope.changed_via(grants=bool(grants)))
        await s.commit()
        out = _payload(row)
    await st.agent_store.reload()
    return out


@router.delete("/api/agents/{name}", response_model=AgentDefOut)
async def delete_agent(request: Request, name: str,
                       scope: WriteScope = Depends(agent_write_scope)):
    """Delete an agent's definition. Its runs, memories and change log survive
    (the log is append-only and outlives the row, so a deleted agent can still
    be read back from its last snapshot). System agents are platform-internal
    and refuse deletion."""
    st = request.app.state
    scope.require_edit("deleting an agent")
    async with st.session_factory() as s:
        row = await s.get(AgentDef, name)
        if row is None:
            raise HTTPException(404, "unknown agent")
        if row.system:
            raise HTTPException(409, "system agents are platform-internal and "
                                     "cannot be deleted")
        out = _payload(row)
        await s.delete(row)
        await s.commit()
    await st.agent_store.reload()
    return out


# --- change log --------------------------------------------------------------

@router.get("/api/agents/{name}/versions", response_model=list[AgentVersionRow],
            dependencies=[Depends(require_role(*READ_ROLES))])
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
            dependencies=[Depends(require_role(*READ_ROLES))])
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
        problems = validate_def(model_of(row), **_registries(request))
        if problems:
            raise HTTPException(422, f"version {version} references things the "
                                     f"repo no longer ships: {'; '.join(problems)}")
        await s.flush()
        await _log_version(s, row, changed_by=principal, changed_via="rollback")
        await s.commit()
        out = _payload(row)
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
    models = [_model(request, d.model_dump(), d.name, registries) for d in body]
    results = []
    async with request.app.state.session_factory() as s:
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
            await _log_version(s, row, changed_by=principal, changed_via="import")
            results.append({"name": model.name, "status": status})
        await s.commit()
    await request.app.state.agent_store.reload()
    return results
