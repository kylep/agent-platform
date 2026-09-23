"""Teams and Projects: durable groupings, distinct from execution roles and tickets."""
from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from agentplatform.api.auth import require_role
from agentplatform.db import (AgentDef, Conversation, Project, ProjectAgent,
                              RelayParticipant, Team, TeamAgent, utcnow)

router = APIRouter()
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


class TeamIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    agents: list[str] = []


class TeamPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    agents: list[str] | None = None
    archived: bool | None = None


class ProjectIn(BaseModel):
    slug: str
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    team_slug: str | None = None
    agents: list[str] = []


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    team_slug: str | None = None
    agents: list[str] | None = None
    archived: bool | None = None


class ConversationScopeIn(BaseModel):
    team_slug: str | None = None
    project_slug: str | None = None


def _slug(value: str) -> str:
    if not SLUG.fullmatch(value):
        raise HTTPException(422, "slug must use lowercase letters, digits, and hyphens")
    return value


async def _agents(s, names: list[str]) -> list[str]:
    unique = list(dict.fromkeys(names))
    if unique:
        found = set((await s.execute(select(AgentDef.name).where(
            AgentDef.name.in_(unique)))).scalars())
        missing = [name for name in unique if name not in found]
        if missing:
            raise HTTPException(422, f"unknown agents: {', '.join(missing)}")
    return unique


async def _team(s, slug: str) -> Team:
    row = (await s.execute(select(Team).where(Team.slug == slug))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "unknown team")
    return row


async def _project(s, slug: str) -> Project:
    row = (await s.execute(select(Project).where(Project.slug == slug))).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "unknown project")
    return row


async def _team_view(s, row: Team) -> dict:
    agents = list((await s.execute(select(TeamAgent.agent).where(
        TeamAgent.team_id == row.id).order_by(TeamAgent.agent))).scalars())
    return dict(id=row.id, slug=row.slug, name=row.name, description=row.description,
                agents=agents, relay_channel_id=row.relay_channel_id,
                archived=row.archived_at is not None)


async def _project_view(s, row: Project) -> dict:
    agents = list((await s.execute(select(ProjectAgent.agent).where(
        ProjectAgent.project_id == row.id).order_by(ProjectAgent.agent))).scalars())
    team_slug = None
    if row.team_id:
        team = await s.get(Team, row.team_id)
        team_slug = team.slug if team else None
    return dict(id=row.id, slug=row.slug, name=row.name, description=row.description,
                team_slug=team_slug, agents=agents, archived=row.archived_at is not None)


async def _set_team_agents(s, row: Team, names: list[str]) -> None:
    names = await _agents(s, names)
    old = set((await s.execute(select(TeamAgent.agent).where(
        TeamAgent.team_id == row.id))).scalars())
    new = set(names)
    for name in old - new:
        await s.execute(delete(TeamAgent).where(TeamAgent.team_id == row.id,
                                               TeamAgent.agent == name))
        await s.execute(delete(RelayParticipant).where(
            RelayParticipant.channel_id == row.relay_channel_id,
            RelayParticipant.participant == f"agent:{name}"))
    for name in new - old:
        s.add(TeamAgent(team_id=row.id, agent=name))
        s.add(RelayParticipant(channel_id=row.relay_channel_id,
                               participant=f"agent:{name}"))


async def _set_project_agents(s, row: Project, names: list[str]) -> None:
    names = await _agents(s, names)
    await s.execute(delete(ProjectAgent).where(ProjectAgent.project_id == row.id))
    s.add_all(ProjectAgent(project_id=row.id, agent=name) for name in names)


@router.get("/api/teams", dependencies=[Depends(require_role("admin"))])
async def list_teams(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Team).order_by(Team.slug))).scalars().all()
        return [await _team_view(s, row) for row in rows]


@router.post("/api/teams", status_code=201)
async def create_team(request: Request, body: TeamIn,
                      principal: str = Depends(require_role("admin"))):
    _slug(body.slug)
    async with request.app.state.session_factory() as s:
        if (await s.execute(select(Team.id).where(Team.slug == body.slug))).first():
            raise HTTPException(409, "team slug already exists")
        await _agents(s, body.agents)
        channel_id = uuid.uuid4().hex
        row = Team(id=uuid.uuid4().hex, slug=body.slug, name=body.name, description=body.description,
                   relay_channel_id=channel_id)
        s.add(row)
        s.add(Conversation(id=channel_id, connector="web", agent=None,
                           kind="group", home="relay", reply_mode="linear",
                           dispatch_mode="mentions", title=body.name,
                           team_id=row.id))
        s.add(RelayParticipant(channel_id=channel_id,
                               participant=f"user:{principal}", role="owner"))
        try:
            await _set_team_agents(s, row, body.agents)
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(409, "team slug already exists") from None
        return await _team_view(s, row)


@router.get("/api/teams/{slug}", dependencies=[Depends(require_role("admin"))])
async def get_team(request: Request, slug: str):
    async with request.app.state.session_factory() as s:
        return await _team_view(s, await _team(s, slug))


@router.patch("/api/teams/{slug}", dependencies=[Depends(require_role("admin"))])
async def update_team(request: Request, slug: str, body: TeamPatch):
    async with request.app.state.session_factory() as s:
        row = await _team(s, slug)
        if body.name is not None:
            row.name = body.name
            (await s.get(Conversation, row.relay_channel_id)).title = body.name
        if body.description is not None:
            row.description = body.description
        if body.agents is not None:
            await _set_team_agents(s, row, body.agents)
        if body.archived is not None:
            row.archived_at = utcnow() if body.archived else None
            (await s.get(Conversation, row.relay_channel_id)).archived_at = row.archived_at
        await s.commit()
        return await _team_view(s, row)


@router.get("/api/projects", dependencies=[Depends(require_role("admin"))])
async def list_projects(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Project).order_by(Project.slug))).scalars().all()
        return [await _project_view(s, row) for row in rows]


@router.post("/api/projects", status_code=201)
async def create_project(request: Request, body: ProjectIn):
    _slug(body.slug)
    async with request.app.state.session_factory() as s:
        if (await s.execute(select(Project.id).where(Project.slug == body.slug))).first():
            raise HTTPException(409, "project slug already exists")
        await _agents(s, body.agents)
        team = await _team(s, body.team_slug) if body.team_slug else None
        row = Project(id=uuid.uuid4().hex, slug=body.slug, name=body.name, description=body.description,
                      team_id=team.id if team else None)
        s.add(row)
        try:
            await _set_project_agents(s, row, body.agents)
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(409, "project slug already exists") from None
        return await _project_view(s, row)


@router.get("/api/projects/{slug}", dependencies=[Depends(require_role("admin"))])
async def get_project(request: Request, slug: str):
    async with request.app.state.session_factory() as s:
        return await _project_view(s, await _project(s, slug))


@router.patch("/api/projects/{slug}", dependencies=[Depends(require_role("admin"))])
async def update_project(request: Request, slug: str, body: ProjectPatch):
    async with request.app.state.session_factory() as s:
        row = await _project(s, slug)
        if body.name is not None:
            row.name = body.name
        if body.description is not None:
            row.description = body.description
        if "team_slug" in body.model_fields_set:
            row.team_id = (await _team(s, body.team_slug)).id if body.team_slug else None
        if body.agents is not None:
            await _set_project_agents(s, row, body.agents)
        if body.archived is not None:
            row.archived_at = utcnow() if body.archived else None
        await s.commit()
        return await _project_view(s, row)


@router.put("/api/relay/channels/{channel_id}/scope",
            dependencies=[Depends(require_role("admin"))])
async def set_conversation_scope(request: Request, channel_id: str,
                                 body: ConversationScopeIn):
    """Attach an existing conversation to one project and optional team.

    Assignment is provenance, not an ACL: room participants remain unchanged.
    A team's own group always retains its team identity.
    """
    async with request.app.state.session_factory() as s:
        room = await s.get(Conversation, channel_id)
        if room is None:
            raise HTTPException(404, "unknown conversation")
        project = await _project(s, body.project_slug) if body.project_slug else None
        team = await _team(s, body.team_slug) if body.team_slug else None
        if room.team_id and (await s.get(Team, room.team_id)) is not None:
            own = await s.get(Team, room.team_id)
            if own.relay_channel_id == room.id and team and team.id != own.id:
                raise HTTPException(422, "a team group cannot change teams")
            if own.relay_channel_id == room.id and team is None:
                team = own
        if project and project.team_id:
            if team and team.id != project.team_id:
                raise HTTPException(422, "project belongs to a different team")
            team = await s.get(Team, project.team_id)
        room.team_id = team.id if team else None
        room.project_id = project.id if project else None
        await s.commit()
        return dict(channel_id=room.id, team_id=room.team_id,
                    project_id=room.project_id,
                    team_slug=team.slug if team else None,
                    project_slug=project.slug if project else None)
