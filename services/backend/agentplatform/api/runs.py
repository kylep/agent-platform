import base64
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import (ANNOTATE_ROLES, INVOKE_ROLES, READ_ROLES,
                                     require_admin, require_role)
from agentplatform.db import ACTIVE_STATES, Conversation, Run, SecretAccess, TranscriptEvent
from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.materialize import materialize_run

log = logging.getLogger("runs")

from agentplatform.api import schemas as S
router = APIRouter()

class RunIn(BaseModel):
    agent: str
    prompt: str

class AnnotateIn(BaseModel):
    summary: str | None = None
    tags: list[str] | None = None

class RunAgentDef(BaseModel):
    """An agent definition as the RUN POD needs it (docs/design/15).

    Not the full row: only what the pod materializes into
    `~/.claude/agents/<name>.md` and derives its permission flags from. The two
    grant lists are EXPLICIT — an empty list means no tools, never "everything"
    — because the runner turns them straight into the file's `tools:` line.

    `description` is here because the CLI requires it: a subagent file carrying
    a `name` but no `description` is SKIPPED (silently, bar a debug-log line),
    and `claude --agent <name>` then reports the agent as not found. It is the
    row's description, not decoration."""
    name: str
    prompt: str
    description: str = ""
    harness_tools: list[str] = []
    platform_tools: list[str] = []
    skills: list[str] = []
    model: str = ""

def _summary(r: Run) -> dict:
    return {"id": r.id, "agent": r.agent, "state": r.state, "trigger": r.trigger,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "summary": r.summary, "tags": r.tags or []}

@router.post("/api/runs", response_model=S.RunAccepted)
async def create_run(request: Request, body: RunIn,
                     principal: str = Depends(require_role(*INVOKE_ROLES))):
    store = request.app.state.agent_store
    info = store.get(body.agent)
    if info is None:
        await store.reload()   # a just-synced agent isn't in the cache yet — refresh
        info = store.get(body.agent)
    if info is None: raise HTTPException(404, "unknown agent")
    if info.error is not None: raise HTTPException(409, "agent quarantined")
    # `enabled` is the soft off-switch (docs/design/15): the definition and its
    # history stay, but the agent takes no work. Refused here as well as in the
    # dispatcher so a disabled agent never even gets a queued run to explain.
    if not info.enabled: raise HTTPException(409, "agent is disabled")
    # Agent-invokes-agent: when the caller authenticated with a per-run token,
    # this run is a child in that run's chain. Depth is derived from the parent
    # run (looked up by the token's run_id), not the request body, so an agent
    # can't reset its own depth to dodge the loop guard.
    parent_run_id = getattr(request.state, "api_key_run_id", None)
    trigger, depth = "manual", 0
    initiated_by = principal if not getattr(request.state, "api_key_agent", None) else None
    if parent_run_id:
        async with request.app.state.session_factory() as s:
            parent = await s.get(Run, parent_run_id)
        if parent is not None:
            trigger, depth = "agent", (parent.depth or 0) + 1
            # The chain keeps its ROOT principal: an agent-invoked child is
            # still being done for whoever started the ancestor (design/13 D).
            initiated_by = parent.initiated_by
            if depth > request.app.state.settings.max_run_chain_depth:
                raise HTTPException(429, "run-chain depth limit exceeded")
    # Synchronous command: materialize the run now (DB-first) and return its id.
    # (Async triggers — webhooks, schedules, connectors — go through run.inbound.)
    run_id = uuid.uuid4().hex
    await materialize_run(request.app.state.session_factory, request.app.state.producer, {
        "run_id": run_id, "agent": body.agent, "prompt": body.prompt,
        "trigger": trigger, "requested_by": principal,
        "initiated_by": initiated_by,
        "parent_run_id": parent_run_id if trigger == "agent" else None, "depth": depth,
    })
    return {"id": run_id, "state": "queued"}

@router.get("/api/runs", response_model=list[S.RunSummary], dependencies=[Depends(require_role(*READ_ROLES))])
async def list_runs(request: Request, limit: int = Query(50, ge=1, le=500),
                    offset: int = Query(0, ge=0),
                    agent: str | None = None, state: str | None = None,
                    tag: str | None = None, needs_summary: bool = False):
    """Run history with paging (`offset`) and agent/state filters pushed to
    SQL — the full history stays reachable, not just the newest window. The
    tag/needs_summary filters stay Python-side over a bounded recent window
    (JSON membership isn't portable across sqlite/postgres)."""
    stmt = select(Run).order_by(Run.created_at.desc())
    if agent:
        stmt = stmt.where(Run.agent == agent)
    if state:
        stmt = stmt.where(Run.state == state)
    python_filtered = bool(tag) or needs_summary
    if not python_filtered:
        stmt = stmt.offset(offset).limit(limit)
    else:
        stmt = stmt.limit(500 + offset)
    async with request.app.state.session_factory() as s:
        rows = list((await s.execute(stmt)).scalars())
    if needs_summary:
        rows = [r for r in rows if not r.summary]
    if tag:
        rows = [r for r in rows if tag in (r.tags or [])]
    if python_filtered:
        rows = rows[offset:offset + limit]
    return [_summary(r) for r in rows]

@router.get("/api/tags", response_model=list[str], dependencies=[Depends(require_role(*READ_ROLES))])
async def list_tags(request: Request):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Run.tags))).scalars()
    seen: set[str] = set()
    for t in rows:
        seen.update(t or [])
    return sorted(seen)

@router.get("/api/runs/{run_id}", response_model=S.RunDetail, dependencies=[Depends(require_role(*READ_ROLES))])
async def get_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        d = _summary(run)
        granted = (await s.execute(select(SecretAccess.secret)
                   .where(SecretAccess.run_id == run_id))).scalars().all()
        d.update({"prompt": run.prompt, "exit_code": run.exit_code, "error": run.error,
                  "tokens_in": run.tokens_in, "tokens_out": run.tokens_out,
                  "tool_calls": run.tool_calls, "secrets_granted": sorted(set(granted)),
                  "permission_denials": run.permission_denials or [],
                  "parent_run_id": run.parent_run_id, "depth": run.depth or 0,
                  "requested_by": run.requested_by,
                  "initiated_by": run.initiated_by,
                  "started_at": run.started_at.isoformat() if run.started_at else None,
                  "finished_at": run.finished_at.isoformat() if run.finished_at else None})
        return d

@router.post("/api/runs/{run_id}/annotate", response_model=S.OkId, dependencies=[Depends(require_role(*ANNOTATE_ROLES))])
async def annotate_run(request: Request, run_id: str, body: AnnotateIn):
    """Set a run's summary and/or tags. Used by the run-summarizer system
    agent (with its API key) and available to any operator+."""
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        if body.summary is not None:
            run.summary = body.summary
        if body.tags is not None:
            run.tags = body.tags
        await s.commit()
    return {"ok": True, "id": run_id}

@router.get("/api/runs/{run_id}/events", dependencies=[Depends(require_admin)])
async def run_events(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(TranscriptEvent)
                .where(TranscriptEvent.run_id == run_id).order_by(TranscriptEvent.seq))).scalars()
        return [e.payload for e in rows]


def _own_run_or_403(request: Request, run_id: str) -> None:
    """Per-run session tokens may only touch their own run; admins (session
    cookie, no api_key_run_id) may debug any."""
    key_run = getattr(request.state, "api_key_run_id", None)
    if key_run is not None and key_run != run_id:
        raise HTTPException(status_code=403, detail="not this run's token")


@router.get("/api/runs/{run_id}/session",
            dependencies=[Depends(require_role("session", "admin"))])
async def get_session(run_id: str, request: Request):
    """Fetch a conversation's Claude session blob (docs/design/14) for the
    runner to restore before `claude --resume`. Nulls when absent or oversized
    (the runner then uses the text-replay fallback)."""
    _own_run_or_403(request, run_id)
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None or not run.conversation_id:
            raise HTTPException(status_code=404, detail="no conversation")
        conv = await s.get(Conversation, run.conversation_id)
        cap = request.app.state.settings.session_blob_max_bytes
        if conv is None or not conv.session_blob or len(conv.session_blob) > cap:
            return {"session_id": None, "blob_b64": None}
        return {"session_id": conv.claude_session_id,
                "blob_b64": base64.b64encode(conv.session_blob).decode()}


@router.get("/api/runs/{run_id}/agentdef", response_model=RunAgentDef,
            dependencies=[Depends(require_role("session", "admin"))])
async def get_agentdef(run_id: str, request: Request):
    """The definition this run is executing (docs/design/15). Identity lives in
    `agent_defs`, so the pod no longer reads it off the git-synced /agents
    mount — it asks for exactly its own agent, with the same run-scoped
    `session` token the conversation endpoints use."""
    _own_run_or_403(request, run_id)
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="unknown run")
    store = request.app.state.agent_store
    info = store.get(run.agent)
    if info is None:
        await store.reload()   # created moments ago — not in the cache yet
        info = store.get(run.agent)
    if info is None:
        raise HTTPException(status_code=404, detail="unknown agent")
    # A quarantined row has no manifest; the dispatcher rejects its runs before
    # a pod exists, so this is belt-and-braces rather than a live path.
    m = info.manifest
    return RunAgentDef(name=info.name, prompt=info.agent_md,
                       description=m.description if m else "",
                       harness_tools=info.harness_tools,
                       platform_tools=info.platform_tools,
                       skills=list(m.skills) if m else [],
                       model=m.model if m else "")


@router.put("/api/runs/{run_id}/session",
            dependencies=[Depends(require_role("session", "admin"))])
async def put_session(run_id: str, body: S.SessionBlob, request: Request):
    """Store the updated session blob after a turn. An oversized blob CLEARS
    the stored session (a stale blob would resume a session missing recent
    turns — worse than a clean reset to the fallback)."""
    _own_run_or_403(request, run_id)
    blob = base64.b64decode(body.blob_b64)
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None or not run.conversation_id:
            raise HTTPException(status_code=404, detail="no conversation")
        conv = await s.get(Conversation, run.conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail="no conversation")
        if len(blob) > request.app.state.settings.session_blob_max_bytes:
            conv.claude_session_id, conv.session_blob = "", None
            await s.commit()
            return {"ok": True, "reset": True}
        conv.claude_session_id, conv.session_blob = body.session_id, blob
        await s.commit()
    return {"ok": True, "reset": False}

@router.post("/api/runs/{run_id}/kill", response_model=S.Ok, dependencies=[Depends(require_admin)])
async def kill_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        if run.state not in ACTIVE_STATES: raise HTTPException(409, "run is terminal")
    await request.app.state.producer.publish(TOPIC_RUN_REQUESTS, run_id,
                                             {"type": "cancel", "run_id": run_id},
                                             type="run.request")
    return {"ok": True}
