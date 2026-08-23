# Conversation Session Resume + Cache Metrics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make conversation continuations resume real Claude CLI sessions (full fidelity + prompt-cache hits) with the existing text-replay as fallback, and surface prompt-cache token metrics in the DB, API, and Reporting page.

**Architecture:** Conversations gain a stored Claude session blob (`~/.claude/projects/<slug>/<sid>.jsonl`, treated as an opaque blob in Postgres). The runner restores it on pod boot, invokes `claude --resume <sid> -p "<new message>"`, and uploads the updated file after the turn via two new run-token-authenticated API endpoints. If no blob exists or resume fails, the runner falls back to today's flattened-history prompt (which moves from a 20-turn cap to a token budget). Separately, the recorder starts capturing `cache_read_input_tokens` / `cache_creation_input_tokens` that it currently drops, and the metrics API + Reporting UI expose them.

**Tech Stack:** Python 3.12 (prod; dev venv is 3.14 — beware annotation-shadow bugs), FastAPI, SQLAlchemy async, React/TypeScript web UI, Claude Code CLI 2.1.241, k8s Jobs.

**Spec:** `docs/design/14-conversation-session-resume.md` (written in Task 1 — it records the empirical findings that justify this design; read it first).

## Global Constraints

- Single `main` branch; commit directly to it. No `git add -A` — add each file explicitly.
- DB migrations are additive-only via `_ensure_columns` in `services/backend/agentplatform/db.py:262` — new columns on existing tables appear automatically at startup; existing rows get NULL, so all aggregation code must `or 0` / `coalesce`.
- The session `.jsonl` format is INTERNAL to Claude Code — store/restore it as an opaque blob only; never parse or generate it.
- Backend tests: `cd services/backend && .venv/bin/pytest tests/ -v`. Runner tests: `cd services/runner && python3 -m pytest test_runner.py -v`.
- Env var names introduced here (exact): `AP_SESSION_TOKEN`, `AP_USER_MESSAGE`. Reused: `AP_API_URL`, `AP_RUN_ID`, `AP_PROMPT`.
- New API-key role string (exact): `"session"`.
- Network policy: runner→api ingress is ALREADY allowed (`charts/agent-platform/templates/networkpolicy.yaml:53` includes `runner` in allow-api). No chart change expected; live-verify in Task 10.
- Deploy order for image changes (from ops history): push images → agents-sync → restart dispatcher → helm upgrade. Never `helm --reuse-values` (known trap); pass explicit values.

---

## Empirical facts this plan is built on (verified 2026-08-23, CLI 2.1.241)

1. **History is NOT cached today.** Live conversation `9fbe9e64…` (agent pai, discord): turn 1 run `42fbe609…` and turn 2 run `06e4d8cf…` both show `cache_read_input_tokens: 2277` (flat = only system+tools cached; the replayed transcript re-bills at full price). 1-hour cache tier confirmed (`ephemeral_1h_input_tokens`).
2. **`--input-format stream-json` cannot inject assistant history.** Injected `{"type":"assistant",...}` lines are silently dropped (secret-word test: model answered its own word, not the injected one). The "replay history as a message array via stdin" design is DEAD — do not resurrect it.
3. **Session-file restore works.** Deleting `<sid>.jsonl` breaks `--resume`; restoring the exact bytes from a copy fully restores it (model recalled prior-turn content; `cache_read: 29482, cache_creation: 167` — replayed history HIT the prefix cache). Same-session multi-turn caching is incremental and exact (turn-2 read = turn-1 read + creation).
4. **The recorder drops cache tokens.** `recorder.py` result-frame handler reads only `input_tokens`/`output_tokens`; `modelUsage` frames carry `cacheReadInputTokens`/`cacheCreationInputTokens` which are also dropped.

---

### Task 1: Design doc

**Files:**
- Create: `docs/design/14-conversation-session-resume.md`

**Interfaces:**
- Produces: the spec later tasks argue from; also the doc Kyle asked for ("document it in docs/ so it's clear why we do this").

- [ ] **Step 1: Write the design doc** with exactly this content:

```markdown
# 14 — Conversation session resume

## Problem

Conversation continuations are stateless full-transcript replays: each turn is a
fresh `claude -p` whose prompt is the entire history flattened into one string
(`conversation.py:build_prompt`). Measured on a live conversation (2026-08-23):
`cache_read_input_tokens` stayed flat at 2277 across turns — only the system
prompt + tool definitions hit Anthropic's prompt cache. The flattened history is
a single user block whose bytes change every turn (and the trailing "Respond to
the latest user message" instruction sits after it), so the transcript re-bills
at full input price on every turn. The flattening also throws away tool_use,
tool_result, and thinking blocks — an agent cannot remember what tools it ran
last turn. History was additionally capped at an arbitrary 20 turns.

## Rejected: structured message replay via --input-format stream-json

Tested on CLI 2.1.241: injected `{"type":"assistant",...}` stdin lines are
silently DROPPED — each user line becomes a live turn the model answers itself.
There is no CLI/SDK path to seed assistant history into a fresh process.

## Decision: persist the CLI session file as an opaque blob

`claude --resume <sid>` restores everything from
`~/.claude/projects/<cwd-slug>/<sid>.jsonl` (full message history incl.
tool_use/thinking). Verified: the file IS the state — delete it and resume
fails; restore the exact bytes and resume works, with the replayed history
hitting the prefix cache (measured `cache_read: 29482` on the resumed turn).

Flow per conversation turn:
1. Backend stores `(claude_session_id, session_blob)` on the Conversation row.
2. Runner (run pod) GETs the blob via a run-scoped `session` token, writes it to
   the CLI's expected path, and invokes `claude --agent X --resume <sid> -p
   "<new user message>"`.
3. Runner captures the turn's `session_id` from the result frame and PUTs the
   updated `.jsonl` back.
4. Fallback: no blob / restore failure / resume exit≠0 → the existing flattened
   text replay (now token-budgeted instead of 20-turn-capped). A fallback turn
   starts a fresh session whose blob replaces the old one — self-healing.

The blob is opaque: we never parse or generate the .jsonl (its format is
internal to Claude Code and version-dependent). An oversized PUT clears the
blob instead of keeping a stale one — a stale blob would resume a session
missing recent turns, which is worse than a clean reset.

## What this buys

- Fidelity: tool calls, tool results, and thinking survive across turns.
- Cost: the replayed history becomes a byte-stable prefix → cache reads at ~10%
  of input price (1h TTL on the subscription tier; conversations idle >1h pay
  one re-write, then hit again).
- The platform still owns history: (user_message, result) pairs on Run rows
  remain the source of truth for the UI, connectors, and the fallback path.

## Cache metrics

The recorder previously dropped `cache_read_input_tokens` /
`cache_creation_input_tokens` (and the per-model equivalents in `modelUsage`).
They are now captured on Run and RunModelUsage and surfaced in
/api/metrics/* and the Reporting page, so cache health is observable instead of
a one-off spot check.

## Known limits

- Resume replays history to the API每 turn (no server-side session state) — the
  win is cache pricing + fidelity, not fewer tokens sent.
- A CLI version bump in the runner image may invalidate old session files; the
  fallback path absorbs this (turn still succeeds, new session starts).
- Session growth: blobs beyond `session_blob_max_bytes` reset to fallback,
  which also bounds context growth.
```

Fix the one mojibake risk: ensure the file is plain ASCII ("every turn", not "每 turn") before saving.

- [ ] **Step 2: Commit**

```bash
cd /Users/kp/gh/agent-platform
git add docs/design/14-conversation-session-resume.md
git commit -m "docs(design): 14 — conversation session resume + cache metrics rationale"
```

---

### Task 2: DB columns (cache tokens + session blob)

**Files:**
- Modify: `services/backend/agentplatform/db.py` (Run ~line 20-60, RunModelUsage ~line 86-96, Conversation ~line 103-115)
- Test: `services/backend/tests/test_db_columns.py` (create)

**Interfaces:**
- Produces: `Run.tokens_cache_read: int`, `Run.tokens_cache_creation: int`; `RunModelUsage.tokens_cache_read: int`, `RunModelUsage.tokens_cache_creation: int`; `Conversation.claude_session_id: str` (default `""`), `Conversation.session_blob: bytes | None` (LargeBinary, nullable).

- [ ] **Step 1: Write the failing test**

```python
# services/backend/tests/test_db_columns.py
from agentplatform.db import Conversation, Run, RunModelUsage, RunState


async def test_cache_and_session_columns(sf):
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.RUNNING,
                  tokens_cache_read=100, tokens_cache_creation=7)
        conv = Conversation(connector="web", agent="hello-world", title="t",
                            claude_session_id="abc-123", session_blob=b"\x00jsonl")
        s.add(run); s.add(conv)
        s.add(RunModelUsage(run_id="r1", model="m", agent="a",
                            tokens_in=1, tokens_out=2,
                            tokens_cache_read=3, tokens_cache_creation=4))
        await s.commit()
        rid, cid = run.id, conv.id
    async with sf() as s:
        r = await s.get(Run, rid)
        assert (r.tokens_cache_read, r.tokens_cache_creation) == (100, 7)
        c = await s.get(Conversation, cid)
        assert c.claude_session_id == "abc-123" and c.session_blob == b"\x00jsonl"
```

Note: `Conversation`'s actual required constructor fields may differ — mirror how `test_conversations.py` constructs one.

- [ ] **Step 2: Run it — expect FAIL** (`TypeError: invalid keyword argument`):
`cd services/backend && .venv/bin/pytest tests/test_db_columns.py -v`

- [ ] **Step 3: Add the columns.** In `Run` (next to `tokens_in`/`tokens_out`):

```python
    tokens_cache_read: Mapped[int] = mapped_column(Integer, default=0)
    tokens_cache_creation: Mapped[int] = mapped_column(Integer, default=0)
```

In `RunModelUsage` (after `tokens_out`): same two lines. In `Conversation` (after `status`):

```python
    # Claude CLI session resume (docs/design/14): the id + raw bytes of the
    # CLI's session .jsonl, stored OPAQUELY — never parsed or generated here.
    claude_session_id: Mapped[str] = mapped_column(String(64), default="")
    session_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
```

Add `LargeBinary` to the existing `sqlalchemy` import line.

- [ ] **Step 4: Run tests — expect PASS**, then run the whole backend suite to catch fallout: `.venv/bin/pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add services/backend/agentplatform/db.py services/backend/tests/test_db_columns.py
git commit -m "feat(db): cache-token columns on Run/RunModelUsage, session blob on Conversation"
```

---

### Task 3: Recorder captures cache tokens

**Files:**
- Modify: `services/backend/agentplatform/recorder.py:82-84` (modelUsage merge) and `:108-110` (result-frame usage)
- Test: `services/backend/tests/test_recorder.py` (extend)

**Interfaces:**
- Consumes: Task 2 columns.
- Produces: populated `tokens_cache_read`/`tokens_cache_creation` on Run + RunModelUsage for every recorded result frame.

- [ ] **Step 1: Extend the existing usage test.** In `test_transcript_and_metrics` (test_recorder.py:24), change the result frame and assertion:

```python
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 4, "type": "result",
                      "usage": {"input_tokens": 10, "output_tokens": 5,
                                "cache_read_input_tokens": 2277,
                                "cache_creation_input_tokens": 193},
                      "modelUsage": {"claude-opus-4-8": {
                          "inputTokens": 10, "outputTokens": 5,
                          "cacheReadInputTokens": 2277,
                          "cacheCreationInputTokens": 193}}})
```

and assert:

```python
        assert run.tokens_cache_read == 2277 and run.tokens_cache_creation == 193
        mu = (await s.execute(select(RunModelUsage))).scalars().one()
        assert mu.tokens_cache_read == 2277 and mu.tokens_cache_creation == 193
```

(import `RunModelUsage` at the top of the test file).

- [ ] **Step 2: Run — expect FAIL** (`assert 0 == 2277`):
`.venv/bin/pytest tests/test_recorder.py -v`

- [ ] **Step 3: Implement.** At recorder.py:108-110, after the two existing lines:

```python
            run.tokens_cache_read = ((run.tokens_cache_read or 0)
                                     + usage.get("cache_read_input_tokens", 0))
            run.tokens_cache_creation = ((run.tokens_cache_creation or 0)
                                         + usage.get("cache_creation_input_tokens", 0))
```

(`or 0` because pre-migration rows carry NULL.) In the modelUsage merge at :82-84 add to the `RunModelUsage(...)` kwargs:

```python
                        tokens_cache_read=u.get("cacheReadInputTokens", 0),
                        tokens_cache_creation=u.get("cacheCreationInputTokens", 0),
```

- [ ] **Step 4: Run — expect PASS**: `.venv/bin/pytest tests/test_recorder.py -v`

- [ ] **Step 5: Commit**

```bash
git add services/backend/agentplatform/recorder.py services/backend/tests/test_recorder.py
git commit -m "feat(recorder): capture prompt-cache read/creation tokens (previously dropped)"
```

---

### Task 4: Metrics API exposes cache tokens

**Files:**
- Modify: `services/backend/agentplatform/api/metrics.py` (`_agg` :29-47, `by_model` :83-100)
- Modify: `services/backend/agentplatform/api/schemas.py` (`_Agg` parent of `MetricsOverview`:345, `ModelUsage`:365)
- Test: `services/backend/tests/test_metrics_api.py` (extend)

**Interfaces:**
- Consumes: Task 2/3.
- Produces: `tokens_cache_read`/`tokens_cache_creation` ints on `/api/metrics/overview`, `/api/metrics/agents` (via `_Agg`), and each row of `/api/metrics/models`.

- [ ] **Step 1: Write failing tests** in the existing style of `test_metrics_api.py` (it seeds Runs and calls the endpoints via the httpx app client fixture). Seed a Run with `tokens_cache_read=2277, tokens_cache_creation=193` and a `RunModelUsage` row with the same, then assert the overview payload contains `"tokens_cache_read": 2277` and the models row contains both fields.

- [ ] **Step 2: Run — expect FAIL** (KeyError / pydantic validation):
`.venv/bin/pytest tests/test_metrics_api.py -v`

- [ ] **Step 3: Implement.** schemas.py — add to `_Agg`:

```python
    tokens_cache_read: int
    tokens_cache_creation: int
```

and to `ModelUsage` (after `tokens_out`): the same two fields. metrics.py `_agg` — after the `tokens_out` line:

```python
        "tokens_cache_read": sum(r.tokens_cache_read or 0 for r in runs),
        "tokens_cache_creation": sum(r.tokens_cache_creation or 0 for r in runs),
```

`by_model` — extend the select with `func.sum(RunModelUsage.tokens_cache_read), func.sum(RunModelUsage.tokens_cache_creation)` and the row unpack/dict accordingly (`"tokens_cache_read": cr or 0, "tokens_cache_creation": cc or 0`).

- [ ] **Step 4: Run the full backend suite — expect PASS** (`_Agg` feeds AgentMetrics too, so other tests may need the seeded fields — fix any that construct expected dicts): `.venv/bin/pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add services/backend/agentplatform/api/metrics.py services/backend/agentplatform/api/schemas.py services/backend/tests/test_metrics_api.py
git commit -m "feat(metrics): cache read/creation tokens in overview, agents, models endpoints"
```

---

### Task 5: Reporting UI shows cache tokens

**Files:**
- Modify: `services/web/src/api.ts` (`ModelUsage` + `MetricsOverview` types)
- Modify: `services/web/src/pages/Reporting.tsx` (:108 stat row, :162-180 models table)

**Interfaces:**
- Consumes: Task 4 payload fields.

- [ ] **Step 1: Extend types** in api.ts: add `tokens_cache_read: number; tokens_cache_creation: number;` to `ModelUsage` and to the overview/agg type feeding `MetricsOverview`.

- [ ] **Step 2: Add a cache stat tile** next to the existing `tokens in/out (uncached)` Stat (Reporting.tsx:108):

```tsx
          <Stat label={`cache read/write · last ${ov.window} runs`}
                value={`${ov.tokens_cache_read.toLocaleString()} / ${ov.tokens_cache_creation.toLocaleString()}`} />
```

- [ ] **Step 3: Add two columns to the models table** (headers `cached` and `hit %`; bump the empty-state `colSpan` from 4 to 6):

```tsx
              <TD className="text-muted">{m.tokens_cache_read.toLocaleString()}</TD>
              <TD className="text-muted">{(m.tokens_cache_read + m.tokens_in) > 0
                ? `${Math.round(100 * m.tokens_cache_read / (m.tokens_cache_read + m.tokens_in))}%`
                : "—"}</TD>
```

Match the surrounding table markup exactly (TH/TD primitives from src/ui; no raw hex — design-system lint enforces it).

- [ ] **Step 4: Verify:** `cd services/web && npx tsc --noEmit` passes, and the Playwright smoke suite if quick to run locally.

- [ ] **Step 5: Commit**

```bash
git add services/web/src/api.ts services/web/src/pages/Reporting.tsx
git commit -m "feat(web): cache token stats on Reporting (tile + models table hit %)"
```

---

### Task 6: Session blob API endpoints

**Files:**
- Modify: `services/backend/agentplatform/config.py` (add `session_blob_max_bytes: int = 8_000_000` near `transcript_retention_days`:52)
- Modify: `services/backend/agentplatform/api/runs.py` (new endpoints)
- Test: `services/backend/tests/test_session_api.py` (create)

**Interfaces:**
- Consumes: Task 2 Conversation columns; existing auth (`require_role`, `request.state.api_key_run_id` — auth.py:101).
- Produces: `GET /api/runs/{run_id}/session` → `{"session_id": str|null, "blob_b64": str|null}`; `PUT /api/runs/{run_id}/session` body `{"session_id": str, "blob_b64": str}` → `{"ok": true, "reset": bool}`. Role `"session"` (or `admin`), and a per-run key may only touch its own run.

- [ ] **Step 1: Write the failing tests.** Follow `test_apikeys.py` / `test_agent_invoke.py` for how per-run keys are minted and sent as `Bearer`. Cover: (a) PUT then GET round-trips blob+id; (b) GET for a run with no conversation → 404; (c) a key for run A calling run B's path → 403; (d) PUT with blob larger than `session_blob_max_bytes` clears the stored blob and returns `reset: true`, and a following GET returns nulls; (e) GET with no stored blob → `{"session_id": null, "blob_b64": null}`.

- [ ] **Step 2: Run — expect FAIL (404s)**: `.venv/bin/pytest tests/test_session_api.py -v`

- [ ] **Step 3: Implement** in runs.py:

```python
import base64

def _own_run_or_403(request: Request, run_id: str) -> None:
    # Per-run session tokens may only touch their own run; admins may debug any.
    key_run = getattr(request.state, "api_key_run_id", None)
    if key_run is not None and key_run != run_id:
        raise HTTPException(status_code=403, detail="not this run's token")

@router.get("/api/runs/{run_id}/session",
            dependencies=[Depends(require_role("session", "admin"))])
async def get_session(run_id: str, request: Request):
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

@router.put("/api/runs/{run_id}/session",
            dependencies=[Depends(require_role("session", "admin"))])
async def put_session(run_id: str, body: S.SessionBlob, request: Request):
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
            # A stale blob resumes a session missing recent turns — worse than
            # a clean reset to the text-replay fallback. Clear, don't keep.
            conv.claude_session_id, conv.session_blob = "", None
            await s.commit()
            return {"ok": True, "reset": True}
        conv.claude_session_id, conv.session_blob = body.session_id, blob
        await s.commit()
    return {"ok": True, "reset": False}
```

Add to api/schemas.py:

```python
class SessionBlob(BaseModel):
    session_id: str
    blob_b64: str
```

Import `Conversation` in runs.py; check how other endpoints reach `settings` (`request.app.state.settings` — confirm the attribute name in `api/app.py` and match it).

- [ ] **Step 4: Run — expect PASS**, then full suite: `.venv/bin/pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add services/backend/agentplatform/config.py services/backend/agentplatform/api/runs.py services/backend/agentplatform/api/schemas.py services/backend/tests/test_session_api.py
git commit -m "feat(api): run-scoped session blob GET/PUT for conversation resume"
```

---

### Task 7: Joblauncher mints session token + env

**Files:**
- Modify: `services/backend/agentplatform/joblauncher.py` (`launch` :391-445, `build_job` :170-230)
- Test: `services/backend/tests/test_joblauncher.py` (extend)

**Interfaces:**
- Consumes: `_invoke_token(run, role="session")` (joblauncher.py:69).
- Produces: run pods for conversation runs carry `AP_SESSION_TOKEN`, `AP_USER_MESSAGE`, and `AP_API_URL` (deduped if already set).

- [ ] **Step 1: Write the failing test** in test_joblauncher.py's existing style (it builds jobs and inspects env): a Run with `conversation_id="c1", user_message="hi again"` must yield env containing `AP_SESSION_TOKEN` (non-empty), `AP_USER_MESSAGE == "hi again"`, and exactly one `AP_API_URL`; a Run without `conversation_id` must yield none of them.

- [ ] **Step 2: Run — expect FAIL**: `.venv/bin/pytest tests/test_joblauncher.py -v`

- [ ] **Step 3: Implement.** In `launch()`, after the `api_token`/`sa_identity` block (~:425):

```python
        session_token = None
        if self.sf and run.conversation_id:
            # Conversation turns get a narrow session-blob token regardless of
            # the agent's tool grants (docs/design/14).
            session_token = await self._invoke_token(run, role="session")
```

Pass `session_token=session_token` into the `build_job(...)` call at :440. In `build_job`, add `session_token: str | None = None` to the signature and, after the api_token/sa_identity env blocks:

```python
        if session_token:
            env += [k8s.V1EnvVar(name="AP_SESSION_TOKEN", value=session_token),
                    k8s.V1EnvVar(name="AP_USER_MESSAGE", value=run.user_message or "")]
            if not any(e.name == "AP_API_URL" for e in env):
                env.append(k8s.V1EnvVar(name="AP_API_URL",
                                        value=self.settings.api_internal_url))
```

- [ ] **Step 4: Run — expect PASS**, then full suite: `.venv/bin/pytest tests/ -q`

- [ ] **Step 5: Commit**

```bash
git add services/backend/agentplatform/joblauncher.py services/backend/tests/test_joblauncher.py
git commit -m "feat(launcher): session token + user-message env for conversation runs"
```

---

### Task 8: Runner restores, resumes, uploads

**Files:**
- Modify: `services/runner/runner.py` (helpers near `_write_mcp_config`:57; invocation block :283-300; frame loop :304-318)
- Test: `services/runner/test_runner.py` (extend)

**Interfaces:**
- Consumes: Task 6 endpoints via `AP_API_URL` + `AP_SESSION_TOKEN`; `AP_USER_MESSAGE`; Task 7 env.
- Produces: helpers `_project_dir(cwd: str) -> Path`, `_restore_session(cwd: str) -> str | None`, `_upload_session(cwd: str, run_id: str, session_id: str) -> None`; resume-aware invocation with one fallback retry.

- [ ] **Step 1: Write the failing tests** (pure functions; monkeypatch env + `HOME`):

```python
def test_project_dir_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    from runner import _project_dir
    d = _project_dir("/workspace/some.dir_x")
    assert d == tmp_path / ".claude" / "projects" / "-workspace-some-dir-x"

def test_restore_session_writes_blob(tmp_path, monkeypatch):
    import base64, json, runner
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AP_API_URL", "http://api")
    monkeypatch.setenv("AP_SESSION_TOKEN", "ap_x")
    monkeypatch.setenv("AP_RUN_ID", "r1")
    monkeypatch.setattr(runner, "_api_req", lambda m, p, body=None: {
        "session_id": "sid-1", "blob_b64": base64.b64encode(b"{}").decode()})
    assert runner._restore_session("/workspace") == "sid-1"
    assert (tmp_path / ".claude/projects/-workspace/sid-1.jsonl").read_bytes() == b"{}"

def test_restore_session_absent_env(monkeypatch):
    import runner
    monkeypatch.delenv("AP_SESSION_TOKEN", raising=False)
    assert runner._restore_session("/workspace") is None
```

- [ ] **Step 2: Run — expect FAIL (no attribute)**: `cd services/runner && python3 -m pytest test_runner.py -v`

- [ ] **Step 3: Implement the helpers** (stdlib only — the runner has no requests dep):

```python
import base64, re, urllib.request

def _api_req(method: str, path: str, body: dict | None = None) -> dict:
    url = os.environ["AP_API_URL"].rstrip("/") + path
    req = urllib.request.Request(
        url, method=method,
        headers={"Authorization": "Bearer " + os.environ["AP_SESSION_TOKEN"],
                 "Content-Type": "application/json"},
        data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def _project_dir(cwd: str) -> Path:
    # Mirror the CLI's project slug: non-alphanumerics -> '-'. The session file
    # must land where `claude --resume` looks for it.
    return Path.home() / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", cwd)

def _restore_session(cwd: str) -> str | None:
    """Fetch the conversation's session blob and place it for --resume.
    Returns the session id, or None -> caller uses the text-replay fallback."""
    if not (os.environ.get("AP_SESSION_TOKEN") and os.environ.get("AP_API_URL")):
        return None
    try:
        data = _api_req("GET", f"/api/runs/{os.environ['AP_RUN_ID']}/session")
    except Exception as e:
        print(f"session restore failed, falling back: {e}", flush=True)
        return None
    sid, blob = data.get("session_id"), data.get("blob_b64")
    if not sid or not blob:
        return None
    d = _project_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.jsonl").write_bytes(base64.b64decode(blob))
    return sid

def _upload_session(cwd: str, run_id: str, session_id: str) -> None:
    p = _project_dir(cwd) / f"{session_id}.jsonl"
    if not p.exists():
        return
    _api_req("PUT", f"/api/runs/{run_id}/session",
             {"session_id": session_id,
              "blob_b64": base64.b64encode(p.read_bytes()).decode()})
```

- [ ] **Step 4: Wire into `_run()`.** Around the args build (:283-300):

```python
    claude = os.environ.get("CLAUDE_BIN", "claude")
    claude_cwd = cwd or os.getcwd()
    user_message = os.environ.get("AP_USER_MESSAGE", "")
    resume_sid = _restore_session(claude_cwd) if user_message else None
    base = [claude, "--agent", agent]
    if resume_sid:
        # Full-fidelity continuation (docs/design/14): prior turns come from the
        # restored session, so the prompt is JUST the new user message.
        args = base + ["--resume", resume_sid, "-p", user_message]
    else:
        args = base + ["-p", prompt]   # text-replay fallback (build_prompt)
    args += ["--output-format", "stream-json", "--verbose"]
```

(the existing `--model` / permission / mcp-config additions stay as-is, applied to `args`). In the frame loop (:304-318), capture the turn's session id:

```python
        if payload.get("type") == "result" and payload.get("session_id"):
            final_sid = payload["session_id"]
```

After `rc = await asyncio.to_thread(proc.wait)`: if `rc != 0 and resume_sid`, publish a `{"type": "session_fallback", "seq": seq+1, ...}` frame, re-run the same Popen/readline block once with the fallback `args` (no `--resume`, `-p prompt`), continuing the `seq` counter — a corrupt or version-incompatible blob must not kill the conversation. Extract the Popen+readline loop into a small local function so it runs twice without duplication. After a successful exit, upload:

```python
    if rc == 0 and final_sid and os.environ.get("AP_SESSION_TOKEN"):
        try:
            await asyncio.to_thread(_upload_session, claude_cwd, run_id, final_sid)
        except Exception as e:
            print(f"session upload failed (non-fatal): {e}", flush=True)
```

- [ ] **Step 5: Run — expect PASS** (all runner tests): `python3 -m pytest test_runner.py -v`

- [ ] **Step 6: Commit**

```bash
git add services/runner/runner.py services/runner/test_runner.py
git commit -m "feat(runner): restore/resume/upload Claude session for conversation turns"
```

---

### Task 9: Token-budget fallback history (drop the 20-turn cliff)

**Files:**
- Modify: `services/backend/agentplatform/conversation.py:13,16-25`
- Test: `services/backend/tests/test_conversations.py` (extend)

**Interfaces:**
- Produces: `_HISTORY_TOKEN_BUDGET = 30_000`; `_history` keeps the newest turns whose estimated tokens fit the budget (oldest dropped first). `build_prompt` unchanged.

- [ ] **Step 1: Write the failing test:**

```python
async def test_history_token_budget(sf):
    # 100 turns of ~1.5k estimated tokens each: only the newest ~20 fit 30k;
    # crucially the NEWEST survive and the OLDEST fall off.
    cid = ...  # seed a Conversation as the existing tests do
    async with sf() as s:
        for i in range(100):
            s.add(Run(agent="a", trigger="conversation", requested_by="t",
                      prompt="x", state=RunState.SUCCEEDED, conversation_id=cid,
                      user_message=f"m{i} " + "x" * 3000, result="r" * 3000))
        await s.commit()
    async with sf() as s:
        hist = await _history(s, cid)
    assert hist[-1][0].startswith("m99")
    assert sum(len(u) + len(r) for u, r in hist) // 4 <= 30_000
    assert len(hist) < 100
```

(match how existing tests seed a Conversation and set `created_at` ordering).

- [ ] **Step 2: Run — expect FAIL** (current code returns fixed 20): `.venv/bin/pytest tests/test_conversations.py -v`

- [ ] **Step 3: Implement.** Replace `_HISTORY_TURNS = 20` with:

```python
# Fallback text-replay budget (docs/design/14): bound by estimated tokens, not
# an arbitrary turn count. ~4 chars/token is close enough for a guardrail.
_HISTORY_TOKEN_BUDGET = 30_000

def _est_tokens(user: str, reply: str) -> int:
    return (len(user) + len(reply)) // 4 + 1
```

and in `_history`, replace `return out[-_HISTORY_TURNS:]` with:

```python
    kept, budget = [], _HISTORY_TOKEN_BUDGET
    for user, reply in reversed(out):
        cost = _est_tokens(user, reply)
        if kept and cost > budget:
            break
        kept.append((user, reply))
        budget -= cost
    kept.reverse()
    return kept
```

(`if kept and ...` keeps at least the newest turn even if it alone exceeds the budget.)

- [ ] **Step 4: Run — expect PASS**, plus any existing `_HISTORY_TURNS` tests updated: `.venv/bin/pytest tests/test_conversations.py -v`

- [ ] **Step 5: Commit**

```bash
git add services/backend/agentplatform/conversation.py services/backend/tests/test_conversations.py
git commit -m "feat(conversation): token-budget fallback history instead of 20-turn cap"
```

---

### Task 10: Deploy + live verification

**Files:** none (ops).

- [ ] **Step 1: Full test pass locally:** backend `cd services/backend && .venv/bin/pytest tests/ -q`, runner `cd services/runner && python3 -m pytest test_runner.py -q`, web `cd services/web && npx tsc --noEmit`.

- [ ] **Step 2: Build/push backend, runner, and web images and deploy** using the repo's existing deploy scripts under `bin/` (order: push images → agents-sync → restart dispatcher → `helm upgrade` with explicit values — never `--reuse-values`). Restart api, dispatcher, recorder so `_ensure_columns` runs and new code loads.

- [ ] **Step 3: Live-verify the session round trip.** With `KUBECONFIG=~/.kube/pai-nuc.yaml`, login (source `exports.sh`, `POST /api/login` per the memory recipe), then:
  1. `POST /api/conversations` (web connector, agent `pai`), then `POST /api/conversations/{id}/messages` with "My project codename is flamingo-glacier, acknowledge".
  2. After the run succeeds, `GET /api/runs/{run_id}/session` as admin → expect a non-null `session_id` and blob.
  3. Second message: "What is my codename?" → reply must say flamingo-glacier, and its run's `AP_PROMPT` fallback must NOT have been used: check the run pod logs / events show a `--resume` invocation (no "You are continuing an ongoing conversation" text in the transcript's first user frame).
  4. Cache proof: `GET /api/runs/{run2}/events`, result frame usage — `cache_read_input_tokens` on turn 2 must EXCEED turn 1's (history now cached), unlike the flat 2277/2277 baseline recorded in the design doc.
  5. Reporting page shows the cache tile and models-table columns with non-zero values.

- [ ] **Step 4: Verify fallback.** As admin, PUT a garbage blob (`base64 of "junk"`) onto a test conversation's latest run, send another message, and confirm the turn still succeeds (fallback fired, `session_fallback` frame in events) and the blob was replaced by a fresh valid one.

- [ ] **Step 5: Commit any deploy-doc deltas and update `docs/` wiring** if Help/glossary auto-indexes design docs (design-14 should appear alongside 08-13).
