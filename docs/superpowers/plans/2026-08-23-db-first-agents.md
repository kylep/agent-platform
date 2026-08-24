# DB-First Agents Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. One Opus implementer per task, task review after each, Sonnet for Playwright UI verification.

**Goal:** Agent identity (prompt/context, grants, entrypoints, config) lives in Postgres with an append-only change log and tool-based RBAC; capabilities (tools, skills, secret declarations, apps) remain code. `agents/` tree deleted after live migration.

**Architecture:** See spec. Read interface preserved via a DB-backed AgentStore so dispatcher/launcher/recorder/readiness are minimally touched. Definitions reach run pods via run-scoped fetch (design-14 pattern). RBAC = `agents_edit` + `agents_grant` platform tools through the tool-executor chokepoint.

**Tech Stack:** Python 3.12 (prod) / 3.14 (dev venv), FastAPI, SQLAlchemy async, React/TS, k3s on pai.

**Spec:** `docs/design/15-db-first-agents.md` — binding authority for every task.

## Global Constraints

- Single `main` branch, commit directly; no `git add -A`; each file added explicitly.
- Additive DB migration only (`_ensure_columns`/create_all path); NULL-safe reads.
- Sensitive harness tools (Bash, Read, Edit, Write, NotebookEdit) stay unconditionally denied for non-self-edit runs in `runner._permission_args` regardless of DB grants.
- Every `agent_defs` write appends a full-snapshot `agent_versions` row with verified principal; no write path may skip this.
- `agents_edit` must not modify grant fields; only `agents_grant` (or admin) may.
- Seed: no agent is granted `agents_edit`/`agents_grant`.
- Backend tests: `cd services/backend && .venv/bin/pytest tests/ -q` must end green. Runner: `cd services/runner && ../backend/.venv/bin/python -m pytest test_runner.py -q`. Web: `npx tsc --noEmit` + `npm run build -w web`.
- Deploy mechanics per `docs/deployment.md` (build `--platform linux/amd64 --provenance=false` on Mac; `docker save | ssh pai 'sudo k3s ctr -n k8s.io images import -'`; web via `Dockerfile.prebuilt` after native `npm run build -w web` + storybook; backend image feeds ap-api/ap-dispatcher/ap-recorder; runner image needs no restart).
- Live DB snapshot (pg_dump) BEFORE the migration deploy.

---

### Task 1: Schema, models, validation

**Files:** `services/backend/agentplatform/db.py`, new `services/backend/agentplatform/agentdefs.py` (pydantic def model + validation), tests `tests/test_agentdefs_schema.py`.

**Produces:**
- `AgentDef` table `agent_defs`: `name` (String(128) pk), `prompt` (Text, former agent.md body), `description`, `model`, `role` (default "operator"), `system` (bool), `can_invoke` (bool), `concurrency` (int, default 1), `timeout_seconds` (int, default 1800), `result_topic`, `transcript_retention_days` (nullable int), `harness_tools` (JSON list), `platform_tools` (JSON list), `skills` (JSON list), `secrets` (JSON list), `entrypoints` (JSON: `{crons: [{schedule, prompt}], webhooks: [{path}], topics: [...]}` mirroring today's Entrypoints model), `enabled` (bool default true), `created_at`, `updated_at`.
- `AgentVersion` table `agent_versions`: `id` pk, `agent` (indexed), `version` (int, monotonic per agent), `snapshot` (JSON — the full def), `changed_by` (String), `changed_via` (String: "admin"|"tool:agents_edit"|"tool:agents_grant"|"import"), `created_at`.
- `agentdefs.py`: pydantic `AgentDefModel` mirroring the row, `validate_def()` (known skills/secrets/tools against the code registries; parseable entrypoints; role in known roles), and `snapshot_of(row) -> dict`.

Requirements: TDD; NULL-safe defaults; version monotonicity helper `next_version(session, agent)`.

### Task 2: DB-backed AgentStore

**Files:** `services/backend/agentplatform/agents.py` (rework), touched call sites only as needed; tests updated in `tests/test_agents.py`.

**Produces:** `AgentStore(session_factory)` exposing the EXISTING read surface (`get(name) -> AgentInfo`, `.reload()`, iteration/listing, `AgentInfo.agent_md` [now synthesized: prompt text], `.manifest` [Manifest built from row fields], `.crons()`, webhook listing) sourced from `agent_defs`. Sync facade over async DB: internal snapshot cache + `reload()` refreshing from DB (called where the store is already reloaded today); a short TTL (≤5s) auto-refresh so dispatcher/recorder see UI edits without restart. `parse_agent_tools(agent_md)` callers must be repointed: tool grants come from row `platform_tools`/`harness_tools`, NOT frontmatter — update `joblauncher._platform_token_role`, `_frozen_tools`, and `runner`-side expectations via Task 4/5 interfaces. Keep a compatibility note in the module docstring.

Constraint: dispatcher/recorder/api constructor signatures change minimally (they already receive session_factory or can).

### Task 3: Agents API — CRUD, versions, import; PR flow removed

**Files:** `services/backend/agentplatform/api/agents.py` (rewrite), `api/schemas.py`, remove agent-PR endpoints & pending-lock logic (grep `pending`, `pull`, agent-edit PR paths — platform-code self-edit PR flow for coder runs STAYS), tests `tests/test_agents_api.py` (+ update `test_agent_config_editor.py`, `test_agents_quickedit.py` or replace).

**Produces:**
- `GET /api/agents` (list, incl. grants + enabled), `GET /api/agents/{name}`, `POST /api/agents` (create), `PUT /api/agents/{name}` (update def fields; REJECTS grant fields unless caller is admin or holds `agents_grant` — single endpoint, field-level guard), `DELETE /api/agents/{name}` (system agents protected as today), `GET /api/agents/{name}/versions` (+ `GET .../versions/{n}` snapshot), `POST /api/agents/{name}/rollback/{n}` (admin; applies snapshot as a new version).
- `POST /api/agents/import` (admin): body = list of full defs (the migration payload); idempotent upsert, `changed_via="import"`.
- Every write validates (`validate_def`) and appends `agent_versions` with verified principal (`require_role` name / run identity).

### Task 4: `agents_edit` + `agents_grant` platform tools

**Files:** `tools/agents_edit/`, `tools/agents_grant/` following the existing tool pattern (see `tools/memory/`, `tools/linear/`), executor/broker wiring per design-12, tests per existing `tools/*/test_run.py` convention + broker-side grant test.

**Produces:** Tool `agents_edit`: ops create/update/delete/get/list on defs, EXCLUDING grant fields (server-side enforced, not tool-side). Tool `agents_grant`: set/add/remove `harness_tools`/`platform_tools`/`skills`/`secrets` on any agent. Both call the platform API with the run's identity; audited via ToolAudit; `changed_by` = verified run agent, `changed_via` = tool name. Sensitive-harness-tool grants are storable but remain inert for non-self-edit runs (runner denies).

### Task 5: Launcher/runner definition delivery

**Files:** `services/backend/agentplatform/joblauncher.py`, `services/backend/agentplatform/api/runs.py` (add `GET /api/runs/{run_id}/agentdef`, same auth pattern as `/session`: run-scoped token role `session` or admin), `services/runner/runner.py`, tests both sides.

**Produces:** Launcher: every run gets `AP_SESSION_TOKEN` + `AP_API_URL` now (rename usage-neutral; token role `session` covers agentdef+session endpoints), stops depending on `/agents` mount for definitions. Runner: `_install_agent` fetches `{name, prompt}` from `GET /api/runs/{id}/agentdef` and writes `~/.claude/agents/<name>.md` (frontmatter with declared harness+mcp tools for `--allowedTools` parity, body = prompt); falls back to the mounted `/agents` tree if env absent (transition safety, one release). `_frozen_tools`/role ladder read DB rows (via Task 2 store). Skills continue installing from the mount.

### Task 6: Web UI

**Files:** `services/web/src/pages/*` (AgentDetail/editor, Agents list, New-Agent wizard), `api.ts`; remove agent Pending-Changes UI; add Version History panel (list versions, view snapshot diff-ish, rollback button) and Grants panel (harness tools, platform tools from a `GET /api/tools` style registry if present — else static list from API, skills, secrets). Direct save (no PR language). `tsc` + `npm run build -w web` green; keep Terminal design tokens, no raw hex.

### Task 7: Test-suite migration

**Files:** `services/backend/tests/conftest.py` (agent fixtures seed `agent_defs` rows instead of tmp agent.md trees), every failing suite.

**Produces:** Whole backend suite green under the DB-backed store. Delete/replace tests that exist solely to test the git-tree store or agent PR flow. Add: RBAC field-guard test (non-grant caller cannot change grants), version-append-on-every-write test, import idempotency test.

### Task 8: Docs sweep

**Files:** `docs/design/15-db-first-agents.md` (already written — polish only if drift), `docs/building-blocks/*` (agents page, glossary), `README.md`, `docs/deployment.md` (agents-sync no longer serves definitions; pg backup), remove/adjust references to `agents/` tree editing, PR-flow docs. Keep history docs (milestone notes) untouched — they are records.

### Task 9: Repo cleanup

**Files:** delete `agents/` tree; `charts/` + sync deployment references to agents dir (sync pod still serves skills/secrets/reports/apps/docs); `joblauncher` volume mounts for `/agents` KEPT this release (runner fallback + skills) but definitions unread; grep for `agents_root` consumers and adjust.
**Ordering:** merged only AFTER Task 11's live import succeeds (the tree is the migration source). Extraction: `python -m agentplatform.export_agents_tree` style one-off in-repo script (or jq/yaml script) producing the import JSON — written in Task 3's implementer or here.

### Task 10: Postgres backup CronJob

**Files:** `charts/agent-platform/templates/pg-backup.yaml`, values entry (schedule default nightly, retention count), `docs/deployment.md` section.
**Produces:** CronJob running `pg_dump` from the bitnami postgres (creds from its existing secret) to a PVC (`ap-pg-backups`), pruning to last N dumps. Verified live in Task 11 by triggering one manually.

### Task 11: Deploy + live migration (orchestrator-driven)

pg_dump snapshot first; build/import backend+runner+web images; roll api→(recorder,dispatcher,web); POST the import payload (extracted from the tree at current HEAD); verify each migrated agent row vs its files; trigger pg-backup job once; then Task 9's deletion commit.

### Task 12: Live verification

API smoke: conversation turn on migrated `pai` (session resume from design-14 must still work: turn-2 memory + cache growth + blob upload). Runner def delivery: run pod got its definition without the mount (log/frame evidence). RBAC: non-admin token PUT on grants → 403; version rows appended for a UI edit. Playwright (Sonnet): login → Agents list shows migrated agents → open editor, edit description, save, version history shows the change → grants panel renders → New-Agent wizard creates + deletes a scratch agent → Reporting still renders. Final report to ledger.
