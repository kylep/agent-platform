import asyncio
import base64
import binascii
import hashlib
import hmac
import json
import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from agentplatform import workbench
from agentplatform.agentdefs import model_of
from agentplatform.api.artifacts import _bounded_body
from agentplatform.api.auth import (ANNOTATE_ROLES, INVOKE_ROLES, READ_ROLES,
                                     require_admin, require_role)
from agentplatform.api.gitedit import _github_app_token
from agentplatform.db import (ACTIVE_STATES, AgentDef, Conversation, RelaySession, Run,
                              SecretAccess, Ticket, TranscriptEvent, utcnow)
from agentplatform.events import TOPIC_RUN_REQUESTS
from agentplatform.github import GitHubClient
from agentplatform.secrets import CODEX_CREDENTIAL
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

class CodexAuth(BaseModel):
    auth_json: str
    sha256: str = ""

class CodexThread(BaseModel):
    thread_id: str

def _summary(r: Run) -> dict:
    return {"id": r.id, "agent": r.agent, "state": r.state, "trigger": r.trigger,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "summary": r.summary, "tags": r.tags or [],
            "ticket_id": r.ticket_id}

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


def _session_key(run: Run) -> dict:
    """A resume blob belongs to (channel, agent), not to the channel: a Relay
    room holds several agents and each keeps its own CLI session
    (docs/design/19). The backfill moved the design-14 blobs onto this key, so
    a run that started before it still finds its own."""
    return {"channel_id": run.conversation_id, "agent": run.agent}


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
        row = await s.get(RelaySession, _session_key(run))
        cap = request.app.state.settings.session_blob_max_bytes
        if row is None or not row.session_blob or len(row.session_blob) > cap:
            return {"session_id": None, "blob_b64": None}
        return {"session_id": row.claude_session_id,
                "blob_b64": base64.b64encode(row.session_blob).decode()}


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


async def _codex_run_or_404(request: Request, run_id: str) -> Run:
    _own_run_or_403(request, run_id)
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "unknown run")
    info = request.app.state.agent_store.get(run.agent)
    if info is None:
        await request.app.state.agent_store.reload()
        info = request.app.state.agent_store.get(run.agent)
    runtime = run.runtime or (info.manifest.runtime if info and info.manifest else "")
    if runtime != "codex":
        raise HTTPException(404, "not a codex run")
    return run


@router.get("/api/runs/{run_id}/codex-auth",
            dependencies=[Depends(require_role("session", "admin"))])
async def get_codex_auth(run_id: str, request: Request):
    """Hand native Codex OAuth state only to the runner that owns this run."""
    if request.app.state.settings.codex_proxy_url:
        raise HTTPException(404, "Codex credentials are brokered")
    await _codex_run_or_404(request, run_id)
    data = await request.app.state.secret_store.get(CODEX_CREDENTIAL)
    raw = (data or {}).get("auth.json", "")
    if not raw:
        raise HTTPException(404, "codex credentials are not set")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(409, "codex auth.json is invalid") from None
    if not isinstance(parsed, dict):
        raise HTTPException(409, "codex auth.json is invalid")
    return {"auth_json": raw, "sha256": hashlib.sha256(raw.encode()).hexdigest()}


@router.put("/api/runs/{run_id}/codex-auth",
            dependencies=[Depends(require_role("session", "admin"))])
async def put_codex_auth(run_id: str, body: CodexAuth, request: Request):
    """Persist token refreshes made by Codex, with optimistic concurrency."""
    if request.app.state.settings.codex_proxy_url:
        raise HTTPException(404, "Codex credentials are brokered")
    await _codex_run_or_404(request, run_id)
    if len(body.auth_json.encode()) > 128 * 1024:
        raise HTTPException(413, "codex auth.json is too large")
    try:
        parsed = json.loads(body.auth_json)
    except json.JSONDecodeError:
        raise HTTPException(422, "codex auth.json must be a JSON object") from None
    if not isinstance(parsed, dict):
        raise HTTPException(422, "codex auth.json must be a JSON object")
    current = await request.app.state.secret_store.get(CODEX_CREDENTIAL)
    current_raw = (current or {}).get("auth.json", "")
    current_hash = hashlib.sha256(current_raw.encode()).hexdigest()
    if body.sha256 and current_hash != body.sha256:
        raise HTTPException(409, "codex credentials changed during the run")
    await request.app.state.secret_store.set(CODEX_CREDENTIAL,
                                             {"auth.json": body.auth_json})
    return {"ok": True}


@router.get("/api/runs/{run_id}/codex-session",
            dependencies=[Depends(require_role("session", "admin"))])
async def get_codex_session(run_id: str, request: Request):
    run = await _codex_run_or_404(request, run_id)
    if not run.conversation_id:
        raise HTTPException(404, "no conversation")
    async with request.app.state.session_factory() as s:
        row = await s.get(RelaySession, _session_key(run))
    return {"thread_id": row.codex_thread_id if row else ""}


@router.put("/api/runs/{run_id}/codex-session",
            dependencies=[Depends(require_role("session", "admin"))])
async def put_codex_session(run_id: str, body: CodexThread, request: Request):
    run = await _codex_run_or_404(request, run_id)
    if not run.conversation_id:
        raise HTTPException(404, "no conversation")
    async with request.app.state.session_factory() as s:
        key = _session_key(run)
        row = await s.get(RelaySession, key)
        if row is None:
            row = RelaySession(**key)
            s.add(row)
        row.codex_thread_id = body.thread_id[:64]
        await s.commit()
    return {"ok": True}


async def _store_session(s, key: dict, session_id: str, blob: bytes | None) -> None:
    """Upsert one (channel, agent) session. Takes the key, not the run: a retry
    runs after a rollback, where touching an expired ORM attribute would be
    implicit IO the async session cannot do."""
    row = await s.get(RelaySession, key)
    if row is None:
        row = RelaySession(**key)
        s.add(row)
    row.claude_session_id = session_id
    row.session_blob = blob
    await s.commit()


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
        if await s.get(Conversation, run.conversation_id) is None:
            raise HTTPException(status_code=404, detail="no conversation")
        key = _session_key(run)
        reset = len(blob) > request.app.state.settings.session_blob_max_bytes
        session_id, stored = ("", None) if reset else (body.session_id, blob)
        try:
            await _store_session(s, key, session_id, stored)
        except IntegrityError:
            # A concurrent first PUT for this (channel, agent) won the insert.
            # Its row IS the session; re-read and write this blob into it.
            await s.rollback()
            await _store_session(s, key, session_id, stored)
    return {"ok": True, "reset": reset}


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


# --- the Workbench (docs/design/24) ---------------------------------------------
# Two run-scoped routes with the `get_agentdef` shape: the run's own session
# token, `_own_run_or_403`, the agent off the store. The dev role is the
# third check — a non-dev agent has no branch to prepare and nothing to
# publish, and its token must not be able to make the API push on its behalf.

async def _dev_run_or_403(request: Request, run_id: str):
    """The run, its ticket and its definition, for a `role: dev` agent only."""
    _own_run_or_403(request, run_id)
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="unknown run")
        ticket = await s.get(Ticket, run.ticket_id) if run.ticket_id else None
        row = await s.get(AgentDef, run.agent)
    store = request.app.state.agent_store
    info = store.get(run.agent)
    if info is None:
        await store.reload()
        info = store.get(run.agent)
    if info is None or row is None:
        raise HTTPException(status_code=404, detail="unknown agent")
    if info.manifest is None:
        raise HTTPException(status_code=409, detail="agent quarantined")
    if info.manifest.role != "dev":
        raise HTTPException(status_code=403, detail="not a dev agent")
    # The grants (push_path_globs, may_delete_tests) are read off the row, not
    # the store's projection: a fence an operator just narrowed applies to the
    # publish that lands after it, not five seconds later.
    return run, ticket, model_of(row)


async def _gh_client(request: Request) -> GitHubClient | None:
    """The GitHub REST side, or None when the App is not configured. A seam:
    tests replace it with a recording fake."""
    token = await _github_app_token(request)
    repo = request.app.state.settings.github_repo
    return GitHubClient(token, repo) if token and repo else None


NONCE_HEADER = "X-AP-Publish-Nonce"


def _nonce_hash(nonce: str) -> str:
    return hashlib.sha256(nonce.encode()).hexdigest()


async def _mint_nonce(request: Request, run_id: str) -> str | None:
    """A fresh nonce for the run's first workbench GET, None on every later
    one. The conditional UPDATE is the whole guarantee: two first calls racing
    cannot both win the row, so exactly one caller ever holds a nonce whose
    hash the row carries."""
    nonce = secrets.token_hex(32)
    async with request.app.state.session_factory() as s:
        r = await s.execute(update(Run).where(Run.id == run_id, Run.publish_nonce_hash.is_(None))
                            .values(publish_nonce_hash=_nonce_hash(nonce),
                                    publish_nonce_issued_at=utcnow()))
        await s.commit()
    return nonce if r.rowcount == 1 else None


def _require_nonce(request: Request, run: Run) -> None:
    """The publish is the runner's, not the model's. The session token is in
    the pod's environment, where the model's shell can read it and POST a
    `verify: {ok: true}` of its own; the nonce was handed to the runner before
    the model existed and lives only in the runner's memory. A run that never
    prepared has no hash and can publish nothing."""
    presented = request.headers.get(NONCE_HEADER, "")
    expected = run.publish_nonce_hash or ""
    if not presented or not expected or \
            not hmac.compare_digest(_nonce_hash(presented), expected):
        raise HTTPException(403, "publish requires the runner's nonce")


@router.get("/api/runs/{run_id}/workbench", response_model=S.WorkbenchView,
            dependencies=[Depends(require_role("session", "admin"))])
async def get_workbench(run_id: str, request: Request):
    """The facts a dev run's prepare step needs: its branch (named here, never
    by the runner), the base, whether the branch already exists on the remote
    and the PR open for it, if any — and, on the run's first call only, the
    publish nonce."""
    run, ticket, _ = await _dev_run_or_403(request, run_id)
    settings = request.app.state.settings
    branch = workbench.branch_for(run, ticket)
    existing = await asyncio.to_thread(workbench.remote_branch_exists,
                                       settings.git_remote_url, branch)
    gh = await _gh_client(request)
    open_pr = await asyncio.to_thread(gh.find_open_pull_request, branch) if gh else None
    view = workbench.workbench_view(run, ticket, existing, open_pr,
                                    base=settings.default_branch,
                                    remote_url=settings.git_remote_url or None)
    return {**view, "publish_nonce": await _mint_nonce(request, run_id)}


def _publish_wire_bound(cap: int) -> int:
    """The most a publish body may be on the wire: the bundle cap as base64,
    4 KiB for the JSON fields and shas, the notes at their own cap, and room
    for the verify record (its tails are capped again server-side, but they
    arrive uncapped)."""
    return cap * 4 // 3 + 4096 + workbench.NOTES_MAX_BYTES + 256 * 1024


@router.post("/api/runs/{run_id}/publish", response_model=S.PublishOut, status_code=201,
             dependencies=[Depends(require_role("session", "admin"))],
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/json": {"schema": S.PublishIn.model_json_schema()}}}})
async def publish_run(run_id: str, request: Request, response: Response):
    """The one door code leaves a dev pod through. The nonce is checked before
    the body is touched; the body is bounded on the wire before any parser
    sees it (413), then the bundle is decoded and checked against
    `publish_max_bytes` again; everything else — ancestry, paths, policy,
    push, PR, ticket, card, envelope — is `workbench.publish`'s. 201 when the
    head landed, 200 when the same head was already the remote tip, 409 when
    the remote disagrees, 422 when the policy refuses."""
    run, ticket, agent_def = await _dev_run_or_403(request, run_id)
    _require_nonce(request, run)
    st = request.app.state
    cap = st.settings.publish_max_bytes
    request = await _bounded_body(request, _publish_wire_bound(cap))
    try:
        body = S.PublishIn.model_validate_json(await request.body())
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_input=False))
    # base64 is 4 chars per 3 bytes: a string this long decodes to over the cap
    # whatever it holds, so it is refused before any decoding happens.
    if len(body.bundle_b64) > (cap * 4 + 2) // 3 + 4:
        raise HTTPException(413, f"bundle over the {cap} byte cap")
    try:
        bundle = base64.b64decode(body.bundle_b64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(422, "bundle_b64 is not base64")
    if len(bundle) > cap:
        raise HTTPException(413, f"bundle over the {cap} byte cap")
    gh = await _gh_client(request)
    if gh is None:
        raise HTTPException(409, "github app is not configured")
    try:
        result = await workbench.publish(
            st.session_factory, st.producer, st.settings, await _github_app_token(request), gh,
            run=run, agent_def=agent_def, bundle=bundle, head_sha=body.head_sha,
            base_sha=body.base_sha, verify=body.verify, notes_md=body.notes_md)
    except workbench.PublishRefused as e:
        raise HTTPException(e.status, e.reason)
    response.status_code = result.status
    return result.body()
