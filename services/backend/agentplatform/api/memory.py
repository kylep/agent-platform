import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, func, or_, select

from agentplatform.api.auth import MEMORY_ROLES, READ_ROLES, require_role
from agentplatform.db import Memory

log = logging.getLogger("memory")

router = APIRouter()


class MemoryIn(BaseModel):
    content: str
    key: str | None = None
    tags: list[str] | None = None
    # Only honored for human/admin callers; an agent key is pinned to its own
    # namespace and may not target another agent.
    agent: str | None = None


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


@router.post("/api/memories", status_code=201, dependencies=[Depends(require_role(*MEMORY_ROLES))])
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
        await s.commit()
        return _view(m)


@router.get("/api/memories", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_memories(request: Request, agent: str | None = None, q: str | None = None,
                        limit: int = Query(50, ge=1, le=500)):
    """List or search memories in a namespace. `q` is split into terms; a memory
    matches when every term appears (case-insensitive) in its content or key.
    Portable across sqlite/postgres (no engine-specific FTS)."""
    ns = _resolve_ns(request, agent)
    conds = [Memory.agent == ns]
    for term in (q or "").split():
        needle = f"%{term.lower()}%"
        conds.append(or_(func.lower(Memory.content).like(needle),
                         func.lower(func.coalesce(Memory.key, "")).like(needle)))
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Memory).where(and_(*conds))
                .order_by(Memory.updated_at.desc()).limit(limit))).scalars().all()
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


@router.get("/api/memories/{memory_id}", dependencies=[Depends(require_role(*READ_ROLES))])
async def get_memory(request: Request, memory_id: str):
    return _view(await _owned(request, memory_id))


@router.delete("/api/memories/{memory_id}", dependencies=[Depends(require_role(*MEMORY_ROLES))])
async def delete_memory(request: Request, memory_id: str):
    m = await _owned(request, memory_id)
    async with request.app.state.session_factory() as s:
        row = await s.get(Memory, m.id)
        if row is not None:
            await s.delete(row)
            await s.commit()
    return {"ok": True, "id": memory_id}
