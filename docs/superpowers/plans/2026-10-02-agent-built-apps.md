# Agent-built Apps — build plan

**Design:** `docs/design/39-agent-built-apps.md`, revision 6. Kyle's final
decisions (2026-10-02):
- **Kai keeps `agents_grant` / `agents_edit`** under the new limits.
- **`claude-ttrpg` stays its own repo**, a third-party tool that contracts
  development and testing to this platform. Its storage adapter is built there.
- **No check-ins.** Build the whole job end to end, merging and deploying as we
  go.

**Orchestrator:** Claude (Opus 5.5). Implementers are subagents in per-task git
worktrees.

**Review:**
- each phase's diff gets a code review by Sol (`gpt-6-sol`) and Fable;
- UI phases also get a visual review.

**Shipping:**
- every phase ships as a PR to `main` with CI green;
- it's deployed to pai with the reference script
  (`docs/superpowers/plans/reference-deploy-pai.sh`, copied to the scratchpad);
- it's verified live, and the evidence is recorded below.

## Loop protocol (read before every step)

1. **Find your place.** Re-read this file and work the first `- [ ]` task. A
   `- [~]` is implemented but not merged; finish it first.
2. **Quota gate.** Before each phase, check `mcp__ap__get_quota`. Above 90%
   weekly, schedule a wakeup for the reset and continue then. That is waiting,
   not checking in.
3. **Dispatch.** Send implementers the task text, the ground rules below, the
   design sections the task names (pasted, not linked), and the file paths.
   Tasks marked ∥ go out together, each told which paths the others own.
4. **Verify.** Run the named test commands yourself. Never record "green"
   without the output in your own transcript.
5. **Review.** At a phase's end, merge task branches into the phase branch,
   then run the reviews. Fix every confirmed finding with tests before the PR.
6. **Ship.** PR, CI green, merge, deploy, live verification. Tick the tasks
   with commit SHAs, and write the evidence under "Live verification".
7. **Memory.** Update `memory/app-collections-idea.md` with the phase reached.

## Ground rules for implementers (paste verbatim)

- **Workspace.** Work only in your assigned worktree. Stage files by explicit
  path; never `git add -A`. Don't push. End commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- **TDD.** Write the failing test first, then the code. Never weaken or delete
  an existing test to get to green. Every guard in the design gets a test.
- **Style.** Match the surrounding code: house comment voice, explain why,
  sparingly.
- **Python:** `/Users/kp/gh/agent-platform/services/backend/.venv/bin/python`.
- **Backend tests:**
  `cd services/backend && PYTHONPATH=. <python> -m pytest -q -p no:cacheprovider -o addopts="" <paths>`
- **Broker tests:** `cd services/mcp-broker && <python> -m pytest -q`
- **Web:** `npm run -s build -w web`, `npx playwright test` in `services/web`,
  and `node services/web/bin/check-no-raw-hex.mjs`.
- **Lockstep tests you must keep green:** `test_operation_catalog.py`,
  `test_toolregistry.py`, `test_help.py`, `test_agent_write_tools.py` and the
  facade suite. Regenerate `live_operation_catalog.json` with
  `scripts/compile_live_operation_catalog.py` when tool actions change.
- **Report:** files changed, tests added (names), the exact commands you ran
  with their final lines, and every place you deviated from the task or design.

## Tasks

### R0 — authority fixes (design: "Phase 0")

- [~] **R0.1 Kyle-only tools.** A constant
  `KYLE_ONLY_TOOLS = {"mcp__platform__apps", "mcp__platform__app_data",
  "mcp__platform__agents_grant", "mcp__platform__agents_edit"}`. Adding or
  removing any of them from any agent's `platform_tools` (create or update,
  any target, including the caller itself) is allowed only for a Kyle session:
  `request.state.auth_kind == "session"` and role `admin`. Agent run tokens,
  API keys (even admin) and the broker's `agents_grant` are refused with a
  clear 403. Files: `services/backend/agentplatform/api/agents.py`
  (`_changed_fields`, `WriteScope.authorize`, the create path) and wherever
  the broker's `agents_grant` reaches it. Tests: a matrix of self, proxy
  (A grants B), mutual, create-with-grants, admin API key and Kyle session.
- [~] **R0.2 Protected agents.** An agent whose `platform_tools` hold any
  `KYLE_ONLY_TOOLS` entry can be edited (any field, prompt included) only by a
  Kyle session or by itself through `agent_self` (which already forbids grant
  fields). `agents_edit` and `agents_grant` from any other agent are refused.
  Tests: Kai's `agents_edit` can't edit a builder, but can still edit an
  ordinary worker.
- [~] **R0.3 No self-edits through `agents_grant` / `agents_edit`.** A caller
  targeting its own definition through those tools is refused; `agent_self`
  is the self path. Tests included.
- [~] **R0.4 Audit and docs.**
  - A one-time marked migration that writes a change-log note for every agent
    currently holding a Kyle-only tool (today Kai), without changing access.
  - Rewrite the broker's "grant what you don't hold" docstring and the
    `agents_grant` / `agents_edit` help text.
  - Update `docs/building-blocks/security.md` and `agents.md`.

R0.1 ∥ R0.3 (both in `agents.py`, so one implementer does both); R0.2 and
R0.4 follow.

### R1a — store and builder (design: "Apps as state", "Tool-call credentials", "Collections", "Batch writes", "Rules", "Storage", "Views", "Pages" Release 1, "Tools")

All new backend code goes in a package,
`services/backend/agentplatform/appdata/`, to keep tasks disjoint. Tables are
prefixed `app_data_` in the platform database (SQLite-compatible for tests),
and the schema is created through the existing `init_db` and `SchemaMark`
pattern.

- [~] **A1 Schema and models** (`895f1c5` on feat/r1a-a1) (first, alone).
  - Tables:
    - `app_data_apps`: id, unique never-reused name, owner kind/id, timezone,
      status (active/retired), notes and notes_revision,
      approved_version, authority_generation, timestamps;
    - `app_data_definitions`: app_id, kind (collection/view/page), name,
      version, body JSON, state (draft/published), revision, author, run_id,
      reason;
    - `app_data_records`: app_id, collection, id, current_version, system
      fields, `via`, collection_version, `ix_text1`, `ix_text2`, `ix_num1`,
      `ix_time1`, `doc` JSON (JSONB on Postgres);
    - `app_data_record_versions`;
    - `app_data_build_ops` (request_id, args_hash, receipt);
    - `app_data_staging_sets`;
    - `app_data_quotas`;
    - `app_data_write_counters`.
  - The fixed indexes from the design.
  - Model tests on SQLite and Postgres.
- [ ] **A2 Definition language** ∥ A3, A4, A5.
  - Pydantic models and validators for collections, views and v2 pages
    (Release 1 components). These cover field types, access, refs, rules,
    indexed fields, retention, filters and `within_last`.
  - JSON Schema export for `apps schema`, plus a capabilities version.
  - Stable error codes with JSON paths and suggested fixes.
  - Files: `appdata/definitions.py`, `appdata/errors.py`.
- [~] **A3 Authority-fact engine** (`7639d6d` on feat/r1a-a3; key names reconciled with A2 before merge) ∥.
  - Compute fact tuples from a definition set, and run the subset test with
    the per-kind "relax" rules (design: "The authority model").
  - New fields start private; unmapped fields mean proposal.
  - Golden tests covering every fact type.
  - File: `appdata/authority.py`.
- [ ] **A4 Tool-call credentials** ∥.
  - Broker exchange; API endpoint minting a call credential (claims per the
    design; `cnf` set to the executor's ServiceAccount; `call_id`; `exp` set
    to the tool timeout).
  - A new credential kind in `api/auth.py`, with a distinct verifier and
    `author`/`via` stamping.
  - An executor-mediated local endpoint per call that attaches the credential
    and is dropped at return; revocation by `jti`.
  - No credential for callers without a run.
  - Files: `services/mcp-broker/broker.py`, `services/tool-executor/executor.py`,
    `api/auth.py`, `appdata/credentials.py`.
  - Tests: replay after return, a copied credential, the wrong call, an admin
    caller.
- [~] **A5 v2 renderer, web side** (`fff86c0` on feat/r1a-a5; API contract in `services/web/src/lib/appData.ts`, which A9 must match) ∥.
  - `LiveView.tsx` v2 path: table, detail, metric, text, with the 503 state.
  - An Apps page listing state Apps.
  - A builder area: App list, definitions with drafts, build notes, health
    (read-only).
  - Built against a contract fixture in `mock-api.ts`; Playwright specs.
- [ ] **A6 Records engine** (after A1 and A2).
  - CRUD under `editable` / `immutable`.
  - Per-field access, enforced on results and predicates.
  - Refs with `restrict` / `unlink` and delete plans.
  - Rules: `writer`, `immutable_after_create`, `unique` (advisory lock).
  - Indexed side columns, `contains`, the view query builder (ungrouped
    count, paging), retention, and write counters.
  - Add the design's missing fixed index `(app_id, collection, created_at)`,
    which retention and default ordering need.
  - Refuse side-column values over 256 characters.
  - Files: `appdata/records.py`, `appdata/views.py`, `appdata/retention.py`.
- [ ] **A7 Batch and batch jobs** (after A6).
  - `insert`, `upsert` and `skip_existing` modes.
  - Staging sets, with commit re-validation and 24-hour expiry.
- [ ] **A8 Artifacts in records** (after A6).
  - An `artifact` field and App-owned artifacts with an owning-field rule.
  - Byte, metadata and feed authorization; a 64 MiB cap counted against the
    App quota; deletion when the last reference goes.
  - Files: `api/artifacts*.py`, `artifact_store.py`, `appdata/artifacts.py`.
- [ ] **A9 App lifecycle and APIs** (after A2, A3 and A6).
  - `create`, `draft`, `notes`, `validate` (the whole App, including
    records, and `stale_base`), `preview` (sample records, `as:`), `publish`
    (compare-and-swap, consistency, subset), `rollback`, `retire`, `authority`
    and health.
  - `build_ops` with `request_id` plus argument hash.
  - API routes: broker-backed agent routes checking the run token's frozen
    tools and App ownership; Kyle-session routes for the web.
  - Files: `appdata/lifecycle.py`, `api/app_data.py`.
- [ ] **A10 Tools `apps` and `app_data`** (after A9).
  - Broker tools with `@_metered` and grants.
  - Agentspec `GRANTABLE_PLATFORM_TOOLS` entries (Kyle-only, per R0).
  - Help topics, operation-catalog entries, facade classification, SDK
    regeneration.
- [ ] **A11 Quotas and scan budgets** (after A6).
  - Per App and per owner, set by Kyle, failing closed.
- [ ] **A12 Performance gate** (after A7).
  - Load 10⁶ bars-shaped and 10⁶ results-shaped records on Postgres.
  - Measure the design's read paths and a 140k-record batch-job write.
  - Record the numbers and set timeouts from them.
- [ ] **A13 Skill v1 `app-building`.**
  - Add it to the reviewed plugin (version bump, release manifest digests).
  - Release through `plugin-release.yaml` attestation and pin the digests in
    `plugin_release.py`.
  - Assign it to Pai, Kai and Olu; grant `apps` and `app_data` to them from a
    Kyle session at deploy.
- [ ] **A14 Docs.**
  - `docs/building-blocks/apps.md` rewrite (Apps as state), a new
    `app-data.md`, and glossary entries.
- [ ] **A15 Live verification.** Deploy; then Pai builds a small App end to
  end (a collection, a view, a page) through its tools, Kyle's session renders
  it, and a reader principal is refused.

### R1b — approval and tool views
Proposals and the review page; sharing; action templates and
`app_data.write@1`; tool views (no-egress pool, scan, cache, materialization);
App tool facts; tool-only collections; App-owned artifact auth; links; restore
maintenance mode.

### R2 — history and page tool actions
Versioned mode, `pin_version`, cascade, lists (including objects), `exists`,
`new_version`, and tool actions on pages (page-intent credentials, budgets,
Task scheduling).

### M1 — judgment · M2 — TCMS
Each one: a definitions bundle, the copy job, the outbox, parity, cutover,
the takeover gate, removal and a restore drill.

### R3 — richer Apps
Grouping and aggregates, windows, anchor, chart, calendar, sparkline,
stat_row, list_filter, image and refresh.

### M3 — running · M4 — news · M5 — stockmarket
Same steps as M1, plus the new App tools and safety-net receipts.

### M6 — TTRPG
Service principals and durable budgets; ledger checkpoints; the `claude-ttrpg`
storage adapter as contracted work in that repo (sized first).

### Cleanup
Delete the provisioner, `query_app`, the read adapters, `apps/` and the Helm
templates; sweep prompts; check backup size; run fresh-install and restore
drills.

## Needs Kyle (collected, not blocking)

- **Grant `mcp__platform__apps` and `mcp__platform__app_data`** to `pai`, `kai`
  and `olu` from his browser session, once R1a is deployed.
  - These are Kyle-only grants (R0), and a cluster-admin bypass path was
    refused as a security weakening.
  - R1a's live verification (A15) waits on this. Everything else continues.

## Repairs

- **A4:** move `TOOL_APP_DATA_URL` out of the tool's environment (readable from `/proc` by sibling tools under the same uid) into the stdin payload.
- **A9:** publish doesn't lock out record writes that race its consistency check.
- **A9:** prune the record-write `build_ops` rows.
- **A13 release, step 1 (needs Kyle):** add `"0.2.0": "c660caae157f6972b686d97b9354ee00e706d27cde46fc5948b57182cae135b7"` (the SHA-256 of `plugins/agent-platform-coding/release.json` at the A13 commit) to `APPROVED_RELEASES` in `services/backend/agentplatform/plugin_release.py`. It approves exact skill text, and the auto-mode classifier refused it from an agent, so Kyle adds or confirms it. Until then `test_skills.py` (3 tests) and `test_joblauncher.py::test_plugin_assignment_pins_approved_release_at_launch` fail with "plugin release is not approved", and the catalog refuses the whole package (coder and QA are blocked). Any edit to a package file changes the digest: rerun the regeneration, re-pin.
- **A13 release, step 2 (after the R1a PR merges to `main`):**
  1. The `Coding plugin provenance` workflow runs on the push (paths `plugins/agent-platform-coding/**`, `plugin_release.py`, the workflow). It verifies with `verify_release(root, attested=False)`, prints `sha256sum dist/agent-platform-coding-0.2.0.tar.gz`, attests it and uploads artifact `agent-platform-coding-0.2.0`. If it didn't run: `gh workflow run plugin-release.yaml --ref main`.
  2. `RUN_ID=$(gh run list -R kylep/agent-platform --workflow plugin-release.yaml --branch main -L 1 --json databaseId -q '.[0].databaseId')`
  3. `gh run download $RUN_ID -R kylep/agent-platform -n agent-platform-coding-0.2.0 -D /tmp/ap-plugin-release`
  4. `gh attestation verify /tmp/ap-plugin-release/agent-platform-coding-0.2.0.tar.gz -R kylep/agent-platform --signer-workflow kylep/agent-platform/.github/workflows/plugin-release.yaml --source-ref refs/heads/main`
  5. `shasum -a 256 /tmp/ap-plugin-release/agent-platform-coding-0.2.0.tar.gz` must equal the run log's `sha256sum` line and `release_bundle(Path("plugins/agent-platform-coding"))` reproduced from the merged `main` checkout.
  6. A follow-up PR adds `"0.2.0": "<that digest>"` to `ATTESTED_BUNDLES` with the run id in its comment, and records the run in `docs/agent-platform-coding-plugin.md`. The tests then hold the real pin (`test_plugin_catalog_requires_the_attested_bundle` compares it). That PR re-triggers the workflow, which re-attests identical bytes: expected.
  7. **Deploy only after that PR merges.** Before it, the deployed catalog refuses the package ("plugin bytes differ from the attested release bundle").
  8. From Kyle's session: assign `app-building` to `pai`, `kai` and `olu`, and grant them `mcp__platform__apps` and `mcp__platform__app_data` (see Needs Kyle).
- **A13 ↔ A10:** the skill and `app-data.md` name the tools' actions and arguments after the agent routes (`apps`: schema, list, create, get, draft, notes, validate, preview, publish, rollback, retire, authority, health; `app_data`: describe, query, get, create, update, delete, delete_preview; arguments as the route bodies). A10 must expose exactly these, or the skill needs a new release (steps 1–2 again). If A10 adds `batch`/`batch_job`, update section 2 of the skill and "Batch writes" in `app-data.md`.
- **A4 ↔ A9:** `/api/app-data/agent/*` refuse tool-call credentials (`_authenticate_tool_call` sets `frozen_tools = []`, and `_agent` requires the tool in it), so an `app_access` tool can't reach App data yet. Documented as R1b in `tools.md` and `app-data.md`; decide whether R1a wires it.
- **A9 ↔ A11:** `apps health` reports quota against `lifecycle.DEFAULT_RECORDS_LIMIT`/`DEFAULT_BYTES_LIMIT` (100,000 records, 64 MiB) when no quota row exists, but writes are enforced against the config defaults (250,000, 256 MiB). Read limits through `quotas.defaults`.
- **A11:** `quotas.check_new_app` (max Apps) and `check_new_draft` (open drafts) aren't called by `lifecycle.create`/`draft`; `set_quota` and `quotas.describe` have no route.
- **A6/A7/A8/A9:** nothing schedules `retention.prune_app`, `batch.prune_staging_sets`, `quotas.prune_build_ops` or `artifacts.sweep_unreferenced`.
- **Integration:** all of R1a is merged on `feat/r1a` (worktree `~/gh/ap-r1a`). A11 is still running in `~/gh/ap-a11`, then A10, A12–A15, the phase review, the PR and the deploy.

## Live verification

- **R0 (PR #36, `e60f862`, helm rev 89, 2026-10-03).** The admin API key's
  PUT on `kai` was refused with 403 ("agent 'kai' is protected: it holds
  agents_edit, agents_grant, so only Kyle's session…"). Kai's change log has
  version 10, `changed_via=audit:kyle-only`. Sol's review findings (padded
  names, chat-owner moves, images) were fixed before merge.

## Definition of done

- All six Apps migrated, and `apps/` empty.
- The provisioner, `query_app` and the hardcoded adapters are deleted.
- A fresh install shows no Apps, and a restore drill brings every App back
  working.
- Every maintainer has passed its takeover gate.
- CI is green on `main`, and pai runs the final images.
