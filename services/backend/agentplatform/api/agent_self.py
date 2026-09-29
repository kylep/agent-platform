"""Self-management is a narrow capability, never an agent-definition editor."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from agentplatform import artifact_store
from agentplatform.agentdefs import next_version, snapshot_of
from agentplatform.agentspec import CODEX_MODELS, KNOWN_MODELS, TOOL_SELF
from agentplatform.authority import assert_readable_run, ensure_run_authority
from agentplatform.db import ACTIVE_STATES, AgentDef, AgentVersion, Run
from agentplatform.api.auth import authenticate
from agentplatform.api.agents import _caller_platform_tools, _managed_guard, _model, _payload, _registries
from agentplatform.api.schemas import AgentImageIn

router = APIRouter(tags=["agent-self"])


class SelfProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str | None = Field(default=None, min_length=1, max_length=64000)
    description: str | None = Field(default=None, max_length=1024)
    model: str | None = Field(default=None, max_length=64)
    backup_runtime: Literal["claude", "codex"] | None = None
    backup_model: str | None = Field(default=None, max_length=64)
    runtime: Literal["claude", "codex"] | None = None
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
    version: int
    system_source: str | None


def profile_view(row, version):
    return {field: getattr(row, field) for field in
            ("name", "prompt", "description", "runtime", "model", "backup_runtime", "backup_model", "image_artifact_id", "system_source")} | {"version": version}


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
    row = await session.get(AgentDef, name, with_for_update=True)
    if row is None:
        raise HTTPException(403, "agent no longer exists")
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
        model = _model(request, {**_payload(row), **changes}, row.name, _registries(request))
        _managed_guard(row, model)
        changes = {key: getattr(model, key) for key in changes if getattr(model, key) != getattr(row, key)}
        if not changes:
            return profile_view(row, version)
        for key, value in changes.items():
            setattr(row, key, value)
        # Same grants, new persona/model context. Other active runs and all old
        # sessions lose authority; ONLY this already authenticated run may finish.
        row.authorization_generation = (row.authorization_generation or 0) + 1
        run.authorization_generation = row.authorization_generation
        version += 1
        session.add(AgentVersion(agent=row.name, version=version, snapshot=snapshot_of(row),
            changed_by=f"run:{run.id}", changed_via="tool:agent_self"))
        await session.commit()
        out = profile_view(row, version)
    await st.agent_store.reload()
    return out


@router.put("/api/agent-self/avatar", response_model=SelfProfileOut)
async def set_self_avatar(request: Request, body: AgentImageIn):
    st = request.app.state
    async with st.session_factory() as session:
        row, _ = await actor(session, request)
        view = None
        if body.artifact_id is not None:
            art = await artifact_store.get(session, body.artifact_id)
            if art is None:
                raise HTTPException(404, "unknown artifact")
            if art.kind != "image":
                raise HTTPException(422, "avatar must be an image artifact")
            if art.run_id:
                source_run = await session.get(Run, art.run_id)
                if source_run:
                    await assert_readable_run(session, request, source_run)
            view = artifact_store.artifact_view(art)
        row.image_artifact_id = body.artifact_id
        await session.commit()
        out = profile_view(row, await next_version(session, row.name) - 1)
    await artifact_store.publish_artifact_event(st.producer, event="agent_image", artifact=view, agent=out["name"])
    return out
