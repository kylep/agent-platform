import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError

from agentplatform.api.auth import MEMORY_ROLES, READ_ROLES, require_role
from agentplatform.db import Memory

log = logging.getLogger("memory")

from agentplatform.api import schemas as S
router = APIRouter()

# Column caps (see db.Memory). Validated at the edge so malformed input is a
# 422, not a DB DataError → 500 (postgres also rejects NUL bytes in text).
_NAME_MAX = 128


def _reject_nul(value: str | None) -> str | None:
    if value is not None and "\x00" in value:
        raise HTTPException(422, "value must not contain NUL bytes")
    return value


class MemoryIn(BaseModel):
    content: str
    key: str | None = Field(default=None, max_length=_NAME_MAX)
    tags: list[str] | None = None
    # Only honored for human/admin callers; an agent key is pinned to its own
    # namespace and may not target another agent.
    agent: str | None = Field(default=None, max_length=_NAME_MAX)

    @field_validator("content", "key", "agent")
    @classmethod
    def _no_nul(cls, v: str | None) -> str | None:
        if v is not None and "\x00" in v:
            raise ValueError("must not contain NUL bytes")
        return v


def _view(m: Memory) -> dict:
    return {"id": m.id, "agent": m.agent, "key": m.key, "content": m.content,
            "tags": m.tags or [],
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "updated_at": m.updated_at.isoformat() if m.updated_at else None}


def _resolve_ns(request: Request, requested: str | None) -> str:
    """The namespace a request may act on. An agent key is locked to its own
    agent (a mismatched `agent` param is refused); a human/admin caller must
    name the namespace explicitly and may target any."""
    key_agent = getattr(request.state, "api_key_agent", None)
    if key_agent:
        if requested and requested != key_agent:
            raise HTTPException(403, "cross-namespace access denied")
        return key_agent
    if not requested:
        raise HTTPException(400, "agent namespace required")
    return requested


@router.post("/api/memories", status_code=201, response_model=S.MemoryView, dependencies=[Depends(require_role(*MEMORY_ROLES))])
async def save_memory(request: Request, body: MemoryIn):
    """Save a memory in the caller's namespace. A save reusing an existing
    `key` overwrites it (idempotent remember); otherwise a new memory is added."""
    ns = _resolve_ns(request, body.agent)
    async with request.app.state.session_factory() as s:
        existing = None
        if body.key:
            existing = (await s.execute(select(Memory).where(
                Memory.agent == ns, Memory.key == body.key))).scalar_one_or_none()
        if existing is not None:
            existing.content = body.content
            if body.tags is not None:
                existing.tags = body.tags
            m = existing
        else:
            m = Memory(agent=ns, key=body.key, content=body.content, tags=body.tags or [])
            s.add(m)
        try:
            await s.commit()
        except IntegrityError:
            # Two concurrent saves of the same new key: the (agent, key) unique
            # index made the loser fail — retry as the overwrite it wanted.
            await s.rollback()
            winner = (await s.execute(select(Memory).where(
                Memory.agent == ns, Memory.key == body.key))).scalar_one()
            winner.content = body.content
            if body.tags is not None:
                winner.tags = body.tags
            await s.commit()
            m = winner
        return _view(m)


@router.get("/api/memories", response_model=list[S.MemoryView], dependencies=[Depends(require_role(*READ_ROLES))])
async def list_memories(request: Request,
                        agent: str | None = Query(None, max_length=_NAME_MAX),
                        q: str | None = Query(None, max_length=1000),
                        limit: int = Query(50, ge=1, le=500)):
    """List or search memories, newest first. Scope: an agent-scoped key is
    locked to its own namespace; a human/admin caller may pass `agent` to scope
    to one namespace, or omit it to search **across all agents** (the global
    Memories view). `q` is split into terms; a memory matches when every term
    appears (case-insensitive) in its content or key. Portable across
    sqlite/postgres (no engine-specific FTS)."""
    _reject_nul(agent)
    _reject_nul(q)
    key_agent = getattr(request.state, "api_key_agent", None)
    conds = []
    if key_agent:                       # agent key: locked to its namespace
        if agent and agent != key_agent:
            raise HTTPException(403, "cross-namespace access denied")
        conds.append(Memory.agent == key_agent)
    elif agent:                         # admin scoped to one namespace
        conds.append(Memory.agent == agent)
    # else: admin, no agent → global (all namespaces)
    for term in (q or "").split():
        needle = f"%{term.lower()}%"
        conds.append(or_(func.lower(Memory.content).like(needle),
                         func.lower(func.coalesce(Memory.key, "")).like(needle)))
    async with request.app.state.session_factory() as s:
        stmt = select(Memory)
        if conds:
            stmt = stmt.where(and_(*conds))
        rows = (await s.execute(stmt.order_by(Memory.updated_at.desc())
                .limit(limit))).scalars().all()
    return [_view(m) for m in rows]


async def _owned(request: Request, memory_id: str) -> Memory:
    """Fetch a memory, enforcing namespace ownership. A missing memory and one
    in another agent's namespace both read as 404 (don't leak existence)."""
    key_agent = getattr(request.state, "api_key_agent", None)
    async with request.app.state.session_factory() as s:
        m = await s.get(Memory, memory_id)
    if m is None or (key_agent and m.agent != key_agent):
        raise HTTPException(404, "unknown memory")
    return m


@router.get("/api/memories/{memory_id}", response_model=S.MemoryView, dependencies=[Depends(require_role(*READ_ROLES))])
async def get_memory(request: Request, memory_id: str):
    return _view(await _owned(request, memory_id))


class MemoryPatch(BaseModel):
    content: str | None = None
    tags: list[str] | None = None

    @field_validator("content")
    @classmethod
    def _no_nul(cls, v: str | None) -> str | None:
        if v is not None and "\x00" in v:
            raise ValueError("must not contain NUL bytes")
        return v


@router.patch("/api/memories/{memory_id}", response_model=S.MemoryView, dependencies=[Depends(require_role(*MEMORY_ROLES))])
async def edit_memory(request: Request, memory_id: str, body: MemoryPatch):
    """Edit a memory's content/tags in place (the key is identity and stays)."""
    m = await _owned(request, memory_id)
    if body.content is None and body.tags is None:
        raise HTTPException(422, "nothing to change")
    async with request.app.state.session_factory() as s:
        row = await s.get(Memory, m.id)
        if row is None:
            raise HTTPException(404, "unknown memory")
        if body.content is not None:
            row.content = body.content
        if body.tags is not None:
            row.tags = body.tags
        await s.commit()
        return _view(row)


@router.delete("/api/memories/{memory_id}", response_model=S.OkId, dependencies=[Depends(require_role(*MEMORY_ROLES))])
async def delete_memory(request: Request, memory_id: str):
    m = await _owned(request, memory_id)
    async with request.app.state.session_factory() as s:
        row = await s.get(Memory, m.id)
        if row is not None:
            await s.delete(row)
            await s.commit()
    return {"ok": True, "id": memory_id}
