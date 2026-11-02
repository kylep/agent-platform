"""Commitments, dreams and memory history (docs/design/44).

Agent-facing routes are reached only through the broker's memory Tool, on the
run's own token. The namespace is always the verified caller, never an
argument, and every id lookup filters by it. Admin routes sit under
`/api/agents/{name}/...`, `/api/dreams` and `/api/memory-history`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from agentplatform import commitments as C
from agentplatform import dreams as D
from agentplatform import memory_history as H
from agentplatform.api.auth import READ_ROLES, require_admin, require_role
from agentplatform.db import ACTIVE_STATES, AgentDef, Memory, Run, utcnow
from agentplatform.memory_revisions import check_revision, conflict

router = APIRouter()
AGENT_ROLES = (*READ_ROLES, "relay", "tools")
_ID = r"^[0-9a-f]{32}$"


def _text(limit: int):
    return Field(default="", max_length=limit)


def _no_nul(v):
    if isinstance(v, str) and "\x00" in v:
        raise ValueError("must not contain NUL bytes")
    return v


async def _memory_run(s, request: Request, *, persona: bool) -> tuple[str, Run]:
    """The `/api/memory-hybrid` fence: the verified agent, its current and
    frozen memory grant, and an active run it owns."""
    from agentplatform.authority import authority_lock, ensure_run_authority
    agent = getattr(request.state, "api_key_agent", None)
    if not agent:
        raise HTTPException(403, "verified agent namespace required")
    await authority_lock(s)
    row = await s.get(AgentDef, agent, with_for_update=True)
    if (not row or not row.enabled or D.MEMORY_TOOL not in (row.platform_tools or [])
            or (persona and row.agent_type != "persona")):
        raise HTTPException(403, "current persona memory grant required" if persona
                            else "current memory grant required")
    frozen = getattr(request.state, "frozen_tools", None)
    if frozen is not None and D.MEMORY_TOOL not in frozen:
        raise HTTPException(403, "frozen memory grant required")
    rid = getattr(request.state, "api_key_run_id", None)
    run = await s.get(Run, rid) if rid else None
    if (not run or run.agent != agent or run.state not in ACTIVE_STATES
            or not await ensure_run_authority(s, run)):
        raise HTTPException(403, "active owning run authority required")
    if run.definition_snapshot and D.MEMORY_TOOL not in run.definition_snapshot.get("platform_tools", []):
        raise HTTPException(403, "frozen memory grant required")
    return agent, run


async def _dream_run(s, request: Request) -> tuple[str, Run, D.Dream]:
    agent, run = await _memory_run(s, request, persona=False)
    dream = await D.active_dream(s, run.id)
    if dream is None or dream.agent != agent:
        raise HTTPException(403, "this action is only available while dreaming")
    return agent, run, dream


# --- commitments ------------------------------------------------------------

class CommitIn(BaseModel):
    summary: str = Field(min_length=1, max_length=C.LIMITS["summary"])
    destination: str = Field(min_length=1, max_length=C.LIMITS["destination"])
    meaning: str = _text(C.LIMITS["meaning"])
    follow_up: str = _text(C.LIMITS["follow_up"])
    source_note: str = _text(C.LIMITS["source_note"])
    _nul = field_validator("summary", "destination", "meaning", "follow_up", "source_note")(_no_nul)


class CloseIn(BaseModel):
    result_ref: str = _text(C.LIMITS["result_ref"])
    note: str = _text(C.LIMITS["note"])
    _nul = field_validator("result_ref", "note")(_no_nul)


@router.post("/api/commitments", status_code=201, dependencies=[Depends(require_role(*AGENT_ROLES))])
async def commit(request: Request, body: CommitIn):
    async with request.app.state.session_factory() as s:
        agent, run = await _memory_run(s, request, persona=True)
        open_now = len(await C.open_for(s, agent))
        if open_now >= C.OPEN_LIMIT:
            raise HTTPException(409, {"code": "commitment_limit", "open": open_now,
                                      "message": f"{C.OPEN_LIMIT} commitments are open; complete or void one first"})
        dreaming = await D.active_dream(s, run.id) is not None
        row = C.Commitment(agent=agent, summary=body.summary, destination=body.destination,
                           meaning=body.meaning, follow_up=body.follow_up, source_note=body.source_note,
                           source_run_id=run.id, source_channel_id=run.conversation_id,
                           source_message_id=run.trigger_message_id, backfilled=dreaming)
        s.add(row)
        await s.flush()
        out = C.view(row)
        await s.commit()
        return out


@router.get("/api/commitments", dependencies=[Depends(require_role(*AGENT_ROLES))])
async def own_commitments(request: Request, status: Literal["open", "done", "void", "all"] = "open",
                          limit: int = Query(50, ge=1, le=200)):
    async with request.app.state.session_factory() as s:
        agent, _ = await _memory_run(s, request, persona=True)
        stmt = select(C.Commitment).where(C.Commitment.agent == agent)
        if status != "all":
            stmt = stmt.where(C.Commitment.status == status)
        rows = (await s.execute(stmt.order_by(C.Commitment.created_at.desc()).limit(limit))).scalars()
        out = [C.view(c) for c in rows]
        await s.commit()
        return out


async def _close(request: Request, commitment_id: str, body: CloseIn, status: str, agent: str | None,
                 run_id: str | None, s) -> dict:
    stmt = select(C.Commitment).where(C.Commitment.id == commitment_id).with_for_update()
    if agent is not None:
        stmt = stmt.where(C.Commitment.agent == agent)
    row = (await s.execute(stmt)).scalars().first()
    if row is None:
        raise HTTPException(404, "unknown commitment")
    if row.status != "open":
        raise HTTPException(409, f"commitment is already {row.status}")
    row.status, row.closed_at, row.closed_by_run_id = status, utcnow(), run_id
    row.result_ref, row.note = body.result_ref or row.result_ref, body.note or row.note
    await s.flush()
    return C.view(row)


@router.post("/api/commitments/{commitment_id}/{action}", dependencies=[Depends(require_role(*AGENT_ROLES))])
async def close_own(request: Request, action: Literal["complete", "void"], body: CloseIn,
                    commitment_id: str = Path(pattern=_ID)):
    async with request.app.state.session_factory() as s:
        agent, run = await _memory_run(s, request, persona=True)
        out = await _close(request, commitment_id, body, "done" if action == "complete" else "void",
                           agent, run.id, s)
        await s.commit()
        return out


@router.get("/api/agents/{name}/commitments", dependencies=[Depends(require_admin)])
async def agent_commitments(request: Request, name: str,
                            status: Literal["open", "done", "void", "all"] = "all"):
    async with request.app.state.session_factory() as s:
        stmt = select(C.Commitment).where(C.Commitment.agent == name)
        if status != "all":
            stmt = stmt.where(C.Commitment.status == status)
        return [C.view(c) for c in (await s.execute(stmt.order_by(C.Commitment.created_at.desc()))).scalars()]


@router.post("/api/agents/{name}/commitments/{commitment_id}/void", dependencies=[Depends(require_admin)])
async def admin_void(request: Request, name: str, body: CloseIn, commitment_id: str = Path(pattern=_ID)):
    async with request.app.state.session_factory() as s:
        out = await _close(request, commitment_id, body, "void", name, None, s)
        await s.commit()
        return out


@router.get("/api/commitments/stats", dependencies=[Depends(require_admin)])
async def commitment_stats(request: Request):
    async with request.app.state.session_factory() as s:
        return await C.stats(s)


# --- dreaming: the agent's side ---------------------------------------------

class ReviewIn(BaseModel):
    section: Literal["conversations", "memories", "commitments"]
    cursor: str | None = Field(default=None, max_length=12)
    since: datetime | None = None
    until: datetime | None = None


@router.post("/api/memory-review", dependencies=[Depends(require_role(*AGENT_ROLES))])
async def memory_review(request: Request, body: ReviewIn):
    async with request.app.state.session_factory() as s:
        _, _, dream = await _dream_run(s, request)
        page = await D.review(s, dream, body.section, body.cursor, body.since, body.until)
        await s.commit()
        return page


class DeleteIn(BaseModel):
    memory_id: str | None = Field(default=None, pattern=_ID)
    key: str | None = Field(default=None, max_length=128)
    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")


@router.post("/api/memory-delete", dependencies=[Depends(require_role(*AGENT_ROLES))])
async def memory_delete(request: Request, body: DeleteIn):
    if bool(body.memory_id) == bool(body.key):
        raise HTTPException(422, "pass exactly one of memory_id or key")
    async with request.app.state.session_factory() as s:
        agent, run, _ = await _dream_run(s, request)
        stmt = select(Memory).where(Memory.agent == agent).with_for_update()
        stmt = stmt.where(Memory.id == body.memory_id) if body.memory_id else stmt.where(Memory.key == body.key)
        row = (await s.execute(stmt)).scalars().first()
        if row is None:
            raise HTTPException(404, "unknown memory")
        if not check_revision(body.expected_revision, row):
            raise HTTPException(409, conflict(body.expected_revision, row))
        await H.attribute(s, run.id)
        await s.delete(row)
        try:
            await s.commit()
        except DBAPIError as exc:
            await s.rollback()
            if H.loss_limited(exc):
                raise HTTPException(409, {"code": H.LOSS_LIMIT,
                                          "message": "this dream has used its replace/delete budget"})
            raise
    return {"ok": True, "id": row.id}


# --- dreaming and history: admin ---------------------------------------------

class DreamNow(BaseModel):
    agent: str = Field(max_length=128)
    guidance: str = Field(default="", max_length=4000)


@router.post("/api/dreams", status_code=201)
async def dream_now(request: Request, body: DreamNow, principal: str = Depends(require_admin)):
    dream = await D.start(request.app.state.session_factory, request.app.state.producer, body.agent,
                          requested_by=principal, guidance=body.guidance, force=True)
    if dream is None:
        raise HTTPException(409, "agent is not eligible to dream or is already dreaming")
    return D.view(dream)


@router.get("/api/dreams", dependencies=[Depends(require_admin)])
async def list_dreams(request: Request, agent: str | None = None, limit: int = Query(50, ge=1, le=500)):
    async with request.app.state.session_factory() as s:
        stmt = select(D.Dream)
        if agent:
            stmt = stmt.where(D.Dream.agent == agent)
        rows = (await s.execute(stmt.order_by(D.Dream.created_at.desc()).limit(limit))).scalars()
        return [D.view(d) for d in rows]


@router.get("/api/dreams/{dream_id}", dependencies=[Depends(require_admin)])
async def get_dream(request: Request, dream_id: str):
    async with request.app.state.session_factory() as s:
        d = await s.get(D.Dream, dream_id)
        if d is None:
            raise HTTPException(404, "unknown dream")
        return D.view(d, await H.run_changes(s, d.run_id) if d.run_id else [])


@router.get("/api/memory-history", dependencies=[Depends(require_admin)])
async def memory_history(request: Request, agent: str | None = None, memory_id: str | None = None,
                         run_id: str | None = None, key: str | None = None,
                         limit: int = Query(100, ge=1, le=500)):
    if not (agent or memory_id or run_id):
        raise HTTPException(422, "filter by agent, memory_id or run_id")
    async with request.app.state.session_factory() as s:
        stmt = select(H.MemoryVersion)
        for col, value in ((H.MemoryVersion.agent, agent), (H.MemoryVersion.memory_id, memory_id),
                           (H.MemoryVersion.run_id, run_id), (H.MemoryVersion.key, key)):
            if value:
                stmt = stmt.where(col == value)
        rows = (await s.execute(stmt.order_by(H.MemoryVersion.at.desc()).limit(limit))).scalars()
        return [H.view(v) for v in rows]


@router.post("/api/memory-versions/{version_id}/restore")
async def restore_version(request: Request, version_id: str, principal: str = Depends(require_admin)):
    """Write a replaced or deleted state back. The restore is itself a change,
    so it is versioned too."""
    from agentplatform.api.memory import _view
    async with request.app.state.session_factory() as s:
        v = await s.get(H.MemoryVersion, version_id)
        if v is None:
            raise HTTPException(404, "unknown version")
        if v.op == "insert":
            raise HTTPException(422, "an insert version is the note as created; restore an update or delete version")
        await H.attribute(s, f"admin:{principal}")
        row = await s.get(Memory, v.memory_id, with_for_update=True)
        if row is None and v.key:
            row = (await s.execute(select(Memory).where(Memory.agent == v.agent, Memory.key == v.key)
                                   .with_for_update())).scalars().first()
        if row is None:
            row = Memory(id=v.memory_id, agent=v.agent, key=v.key)
            s.add(row)
        row.content, row.tags, row.team_id, row.project_id = v.content, v.tags or [], v.team_id, v.project_id
        if row.key != v.key:
            row.key = v.key
        try:
            await s.flush()
        except DBAPIError:
            raise HTTPException(409, "another memory now holds this key; restore by editing that note instead")
        await s.refresh(row)
        out = _view(row)
        await s.commit()
        return out
