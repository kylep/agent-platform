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

- [x] **R0.1 Kyle-only tools.** A constant
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
- [x] **R0.2 Protected agents.** An agent whose `platform_tools` hold any
  `KYLE_ONLY_TOOLS` entry can be edited (any field, prompt included) only by a
  Kyle session or by itself through `agent_self` (which already forbids grant
  fields). `agents_edit` and `agents_grant` from any other agent are refused.
  Tests: Kai's `agents_edit` can't edit a builder, but can still edit an
  ordinary worker.
- [x] **R0.3 No self-edits through `agents_grant` / `agents_edit`.** A caller
  targeting its own definition through those tools is refused; `agent_self`
  is the self path. Tests included.
- [x] **R0.4 Audit and docs.**
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
- [~] **A2 Definition language** (`d684571`; reconciled with A3 in `e02116b`) ∥ A3, A4, A5.
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
- [~] **A4 Tool-call credentials** (`696952f`; repair: deliver the endpoint over stdin, not env) ∥.
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
- [~] **A6 Records engine** (`3594fea`; all appdata tests green on SQLite and Postgres 16) (after A1 and A2).
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
- [~] **A7 Batch and batch jobs** (`6c2ba09`) (after A6).
  - `insert`, `upsert` and `skip_existing` modes.
  - Staging sets, with commit re-validation and 24-hour expiry.
- [~] **A8 Artifacts in records** (`2a1ec09`) (after A6).
  - An `artifact` field and App-owned artifacts with an owning-field rule.
  - Byte, metadata and feed authorization; a 64 MiB cap counted against the
    App quota; deletion when the last reference goes.
  - Files: `api/artifacts*.py`, `artifact_store.py`, `appdata/artifacts.py`.
- [~] **A9 App lifecycle and APIs** (`1a48afa`; agent routes listed in its report) (after A2, A3 and A6).
  - `create`, `draft`, `notes`, `validate` (the whole App, including
    records, and `stale_base`), `preview` (sample records, `as:`), `publish`
    (compare-and-swap, consistency, subset), `rollback`, `retire`, `authority`
    and health.
  - `build_ops` with `request_id` plus argument hash.
  - API routes: broker-backed agent routes checking the run token's frozen
    tools and App ownership; Kyle-session routes for the web.
  - Files: `appdata/lifecycle.py`, `api/app_data.py`.
- [x] **A10 Tools `apps` and `app_data`** (after A9). (`cdaf1bd`, merged)
  - Broker tools with `@_metered` and grants.
  - Agentspec `GRANTABLE_PLATFORM_TOOLS` entries (Kyle-only, per R0).
  - Help topics, operation-catalog entries, facade classification, SDK
    regeneration.
- [x] **A11 Quotas and scan budgets** (`85e7f08`, merged with settle on every commit path, `6a2c9eb`) (after A6).
  - Per App and per owner, set by Kyle, failing closed.
- [x] **A12 Performance gate** (after A7). (`feat/r1a-a12`, merged; numbers and decisions in `appdata-perf-2026-10.md`. Scan cap is now 1M rows / 60 s. GIN drop, partial indexes and the c3 index go to R1b. PVC and `shared_buffers` sizing go in helm before M2.)
  - Load 10⁶ bars-shaped and 10⁶ results-shaped records on Postgres.
  - Measure the design's read paths and a 140k-record batch-job write.
  - Record the numbers and set timeouts from them.
- [~] **A13 Skill v1 `app-building`.** (`3ca2b8f`. Manifest pin `ea3c6b6` approved by Codex at Kyle's direction; merged. Remaining: the attested-bundle pin after `plugin-release.yaml` runs on main.)
  - Add it to the reviewed plugin (version bump, release manifest digests).
  - Release through `plugin-release.yaml` attestation and pin the digests in
    `plugin_release.py`.
  - Assign it to Pai, Kai and Olu; grant `apps` and `app_data` to them from a
    Kyle session at deploy.
- [x] **A14 Docs.** (`85c100c`, merged with the skill)
  - `docs/building-blocks/apps.md` rewrite (Apps as state), a new
    `app-data.md`, and glossary entries.
- [ ] **A15 Live verification.** Deploy; then Pai builds a small App end to
  end (a collection, a view, a page) through its tools, Kyle's session renders
  it, and a reader principal is refused.

### R1b — approval and tool views

Design sections:
- "The authority model" → Proposals;
- "Tool views" and "Tool-only collections";
- "Collections" → artifact fields and `url`;
- "Pages" Release 1 templates;
- "Lifecycle" → restore;
- "Trust boundaries and guards";
- revision 3's "Proposals" and "Pages" text, which revision 6 keeps "as in
  revision 3" (`git show f7cff4e:docs/design/39-agent-built-apps.md`).

**What R1a already delivered (build on it; don't restate it):**
- **Authority facts for every R1b type.** `appdata/authority.py` already
  computes and diffs `template`, `app_tool`, `tool_view`, `tool_action`,
  `service_principal`, `link` and tool-only `writers` facts. `widening()`
  lets a tool view on an approved App tool's `read` self-publish. It refuses
  a new `writers` rule that names a tool that isn't an approved App tool.
  What's missing is the definition language for App tools and tool views:
  `validate_app` refuses a top-level `app_tools`, and a view with `tool`.
- **Tool-only collections are enforced.** `CollectionDef.writers`,
  `Access.tool_allowed`, `AD-TOOL-ONLY`, and the relax rule are in place.
  Two gaps remain. Writers can't self-publish yet, because no App tool can be
  declared (B3). And a template can target a tool-only verb that Kyle's
  write will always fail (B9).
- **App-owned artifacts are authorized on every route.** The byte, metadata,
  list, stats, thumb and feed routes all go through `may_read`
  (`appdata/artifacts.py`, `api/artifacts*.py`). What remains is rendering
  them, and the sharing and narrowing cases (B19).
- **Pieces of the plumbing.** Scan budgets and leases exist
  (`quotas.scan_budget`, `views.scan_view`), but there's no scan route or
  credential. The `page_intent` claim shape exists in `credentials.py`, but
  nothing mints it. The proposal quota (`quotas.check_new_proposal`) exists,
  but there's no proposal store. `Assessment.needs_proposal` refuses
  widening publishes and suggests `propose`, which doesn't exist yet.
- **`url` fields with `link: true`** parse and produce `link` facts. Values
  aren't scheme-checked, and nothing renders them as links.

**Decisions for R1b (Claude, 2026-10-02; Kyle delegated these).** Each one
takes the planner's recommendation:
- **D1** App tools live in a new definition kind, `tool`.
- **D2** Drop the credential's same-name role fallback. No `tool.yaml` uses `app_access` yet.
- **D3** Per-action `view_actions` go in `tool.yaml`. Eligibility needs both that and the reviewed effect-policy row.
- **D4** `scan` takes a spec on a role, and records the fields it used.
- **D5** Add a third credential kind, `view_exec`: minted only by the API, read-only, and used for Kyle's renders and the materializer.
- **D6** The views pool always renders its own deny-all egress policy, whatever the global setting says.
- **D7** The tool-view cache is a Postgres table with a byte cap.
- **D8** Parameter domains are read as distinct values at each refresh.
- **D9** The owner's "home channel" is the owner agent's DM with Kyle.
- **D10** Proposal kinds are `bundle`, `rollback` and `transfer`.
- **D11** On an approved publish the proposer stays the author, `approved_by=kyle` is stamped, and the authority generation is bumped.
- **D12** `app_data.write@1` gets new intent and receipt tables in appdata.
- **D13** Templates on tool-only verbs are refused at validation.
- **D14** The export appends a restore marker to the dump. The outbox check waits for M1.
- **D15** Maintenance pauses crons, schedules, jobs, Tasks and materialization. Runs Kyle starts are still allowed.
- **D16** The QA login gets the read routes, filtered by what it may see.
- **D17** The ref-history index is deferred to M2.
- **D18** Drop the GIN index, and B23 updates the design's "Storage" section.
- **D19** Ship the small generic `app_summary` view tool.

Parallel groups (each group's tasks go out together; each implementer is told
which paths the others own):
- **G1** ∥ B1, B2, B3, B4, B5, B6, B7
- **G2** ∥ B8, B9, B10
- **G3** ∥ B11, B12, B13, B14, B15, B16, B17
- **G4** ∥ B18, B19
- **G5** ∥ B20, B21, B22
- **G6** ∥ B23, B24
- then B25 and B-live

Shared-file notes for G1:
- **B2 and B3 both touch `lifecycle.py`.** B3 changes only `KINDS` and the
  doc assembly in `_doc`. B2 extracts `_assess` and the publish core into
  helpers it can reuse.
- **B1 and B2 both touch `appdata/models.py`.** B1 changes only
  `AppDataRecord.__table_args__`. B2 appends a new class.

#### G1

- [x] **B1 Index changes from the perf gate** (no deps) ∥.
  - **Goal:** drop the unused GIN index and make the half-used composites
    partial.
  - **Design:** "Storage"; `appdata-perf-2026-10.md` → "Index and
    side-column findings".
  - **Changes:**
    - Drop `ix_app_data_records_doc`.
    - Make `t1_num` partial: `WHERE ix_num1 IS NOT NULL`.
    - Make `t1_t2_time` partial: `WHERE ix_text2 IS NOT NULL`.
    - Add a marked migration (`SchemaMark`) for existing Postgres databases.
    - The c3 index is not added (Decision D17).
  - **Files:** `appdata/models.py` (`__table_args__` only), `db.py` (one
    migration), `tests/test_appdata_models.py`.
  - **Tests prove:**
    - On Postgres, the GIN index is gone and both indexes carry their
      predicates.
    - The migration is idempotent on a database that already has the old
      indexes.
    - `EXPLAIN` of the engine's bars query (`ix_text1 = … AND ix_num1 >= …`)
      and results query (`ix_text1 = … AND ix_text2 = …`) uses the partial
      index.
    - SQLite is unaffected.
- [x] **B2 Proposal store and engine** (no deps) ∥.
  - **Goal:** frozen, content-addressed proposals that Kyle's approval
    publishes atomically, or marks stale.
  - **Design:**
    - revision 3 "Proposals" (freeze, review, approve atomically, states);
    - revision 6 "The authority model" ("Always proposals", "Proposals").
  - **Table `app_data_proposals`:** id, app_id, kind (`bundle`, `rollback`
    or `transfer`; D10), the frozen canonical bundle and its digest,
    base `approved_version` and `authority_generation`, delta (plain words,
    from `A.describe`), validation summary, state (`open`, `published`,
    `declined`, `stale`, `withdrawn`), proposer, run_id, reason, decided_by,
    decided_at.
  - **Functions** in `appdata/proposals.py`:
    - `propose`: freezes the selected drafts (`only`), a rollback target or
      a transfer. It refuses a bundle that self-publishes, with
      "use publish", and refuses errors and `stale_base`. It counts against
      `quotas.check_new_proposal`.
    - `get` and `withdraw` (proposer or owner).
    - `approve(digest)`: under `_consistency_lock`, it re-runs `_assess`
      against current records, recomputes the delta, and compare-and-swaps
      on the base version. If the App moved or the delta changed, the
      proposal goes `stale` and nothing publishes. On success it stamps
      `approved_by` and the proposal id on the definition rows, bumps
      `authority_generation` (D11), and clears the published drafts.
    - `decline`.
  - **Build ops** with `request_id` for every write.
  - **Files:** `appdata/models.py` (new class), `appdata/proposals.py`,
    `appdata/lifecycle.py` (extract the shared publish core; `publish`'s
    behaviour is unchanged), `db.py`, `tests/test_appdata_proposals.py`.
  - **Tests prove:**
    - A widening bundle can be proposed but not published.
    - Approval publishes exactly the frozen bundle, even if the draft
      changed after proposing.
    - A publish, a rollback or a record that now violates a rule between
      propose and approve makes the proposal `stale`.
    - A wrong digest is refused.
    - Approve, decline and withdraw are each final.
    - A transfer changes the owner and moves quotas (`quotas.transfer_owner`).
    - A data-dropping change and a wider rollback both go through proposals.
    - The proposal quota fails closed.
    - These run on SQLite and Postgres.
- [x] **B3 App tool facts in the language, and credential scope through
  them** (no deps) ∥.
  - **Goal:** an App can declare which tools reach which collections
    through which roles, and tool-call credentials bind roles only through
    that fact.
  - **Design:** "The authority model" (the App tools row), "Tool-call
    credentials" (claims), "Tool-only collections".
  - **Changes:**
    - A new definition kind `tool` (D1): one per tool name, body
      `{tool, roles: {role: {collection, verbs}}}`, validated against the
      App's collections. Each role's verbs must stay within the
      collection's verbs.
    - `authority.as_definitions` maps the stored kind to the `app_tools`
      document key.
    - `credentials.bind_roles` / `compute_app_scope`:
      - A role binds the collection named by the App's approved App tool
        fact for that tool. The scope is the intersection of that fact's
        verbs, the manifest's verbs and the agent's own access.
      - The same-name fallback and its TODO go (D2).
    - The web `DefinitionKind` gains `tool`, read-only in the builder area.
  - **Files:**
    - `appdata/definitions.py`, `appdata/authority.py` (mapping only);
    - `appdata/lifecycle.py` (`KINDS`, `_doc`), `appdata/credentials.py`;
    - `toolregistry.py` (docstring);
    - `services/web/src/lib/appData.ts`, `services/web/tests/mock-api.ts`;
    - tests: `test_appdata_definitions.py`, `test_appdata_authority.py`,
      `test_api_app_data.py`, and the broker's
      `test_tool_call_credentials.py`.
  - **Tests prove:**
    - Adding an App tool, or widening its verbs, is a proposal. Narrowing
      self-publishes.
    - A `writers` rule naming an approved App tool self-publishes; one
      naming any other tool is a proposal.
    - A tool with `app_access` gets no scope in an App that has no App tool
      fact for it, even when a collection matches the role's name.
    - The scope never exceeds the fact, the manifest or the agent's access.
    - Removing the fact removes the scope on the next mint.
    - An unknown key in a `tool` definition is refused.
- [x] **B4 Tool view declarations and catalog eligibility** (no deps) ∥.
  - **Goal:** a tool can declare reviewed read actions as view actions, and
    the operation catalog marks exactly those `view_eligible`.
  - **Design:** "Tool views" → Eligibility.
  - **Changes:**
    - A `view_actions` block in `tool.yaml` (D3):
      `{<action>: {output_schema, max_rows, max_bytes, sources: [roles],
      params}}`. Each source must be one of `app_access.roles`, and the tool
      must have `app_access` with `read`.
    - In `compile_live_operation_catalog.py`, a custom tool action is
      `view_eligible` only if it is declared there **and** the reviewed
      `_effect_policy` gives it exactly `["reads_sensitive"]`. Its
      `output_schema`, `limits` and `target_scope` (the source roles) come
      from the manifest.
    - Regenerate `live_operation_catalog.json`.
  - **Files:** `toolregistry.py`, `scripts/compile_live_operation_catalog.py`,
    `live_operation_catalog.json`, `operation_catalog.py`,
    `tests/test_toolregistry.py`, `tests/test_operation_catalog.py`.
  - **Tests prove:**
    - A declared action with any effect besides `reads_sensitive` (or
      `unknown`) is not eligible.
    - A missing output schema, a source role not in `app_access`, and an
      action not in the `action` enum are each manifest errors.
    - The catalog lockstep test catches drift.
    - Existing tools compile unchanged.
- [x] **B5 No-egress executor pool (infrastructure)** (no deps) ∥.
  - **Goal:** a second tool-executor Deployment that runs only view actions
    and can reach nothing but the platform API.
  - **Design:** "Tool views" → Execution; "Trust boundaries and guards" →
    Tool views.
  - **Changes:**
    - Helm: a `tool-executor-views` Deployment and Service with its own
      ServiceAccount.
    - A default-deny egress NetworkPolicy for it, always rendered whatever
      `networkPolicy.egress` says (D6). It allows only DNS and the API.
      Ingress comes from the API only.
    - The executor gains `AP_EXECUTOR_POOL=views`. In that mode it:
      - refuses any tool or action without a `view_actions` declaration;
      - refuses secrets, `database`, `kafka` and `files_in`;
      - caps the output at the action's `max_bytes`.
    - Values in `values.yaml` and `values-pai-nuc.yaml` (one replica, small
      requests).
  - **Files:** `charts/agent-platform/templates/tool-executor-views.yaml`,
    `templates/networkpolicy.yaml`, both values files,
    `services/tool-executor/executor.py`,
    `services/tool-executor/test_executor.py`.
  - **Tests prove:**
    - `helm template` renders the pool and its deny policy with
      `networkPolicy.egress=false`.
    - Views mode refuses a non-view action, secrets, a database, Kafka and
      `files_in`.
    - Output over `max_bytes` fails.
    - The default pool's behaviour is unchanged.
- [x] **B6 Maintenance mode: flag, pause and restore marker** (no deps) ∥.
  - **Goal:** a platform-wide maintenance flag that pauses all automation,
    and that every restore starts in.
  - **Design:** "Lifecycle" → Restore, steps 1 and 4.
  - **Changes:**
    - A `platform_maintenance` row: mode (`running` or `restore`), reason,
      entered_at, resumed_by.
    - What it pauses (D15):
      - the scheduler's crons, schedules and jobs (they don't fire, and
        don't catch up on resume);
      - Task dispatch (`task_scheduler`);
      - App-data materializer refreshes (a hook B21 calls).
    - `maintenance.require_running()` is the gate R2 tool actions and M6
      service principals must call. A test pins that both hooks exist.
    - Kyle-initiated runs and conversations still work.
    - `backup_export` appends `mode=restore` SQL to the dump before
      checksumming (D14). Any restore of that dump starts paused, and the
      live database is untouched.
    - A Kyle-session route to resume. Admin API keys can read the status
      only.
  - **Files:** `db.py`, new `agentplatform/maintenance_mode.py`,
    `scheduler.py`, `task_scheduler.py`, `backup_export.py`,
    `api/maintenance.py`, tests `test_scheduler.py`, `test_backups.py`,
    new `test_maintenance_mode.py`.
  - **Tests prove:**
    - Under `restore`, no cron, schedule, job or Task fires, and nothing
      catches up after resume.
    - A dump restored into a fresh database comes up in `restore`.
    - The source database's mode never changes.
    - Only Kyle's session resumes; an admin key is refused.
- [x] **B7 Links** (no deps) ∥.
  - **Goal:** `url` fields hold only safe URLs, and those with `link: true`
    render as outbound links.
  - **Design:** "Collections" (`url`, text unless `link: true`); revision 3
    "Field types"; the outbound links fact.
  - **Changes:**
    - Records refuse a `url` value that isn't absolute `http` or `https`,
      or that has userinfo or control characters, with code `AD-URL`.
    - `page_for_web` marks link columns and detail fields
      `format: "link"` only when the field has `link: true`.
    - The web renders those as `<a rel="noopener noreferrer"
      target="_blank">` and every other `url` as plain text.
    - Text-block links stay internal (unchanged).
  - **Files:** `appdata/records.py` (`normalize_value`),
    `appdata/lifecycle.py` (`page_for_web`, `_column` only),
    `services/web/src/lib/appData.ts`, `services/web/src/pages/LiveView.tsx`
    (the v2 cells), `services/web/tests/state-apps.spec.ts`, `mock-api.ts`,
    `tests/test_appdata_records.py`.
  - **Tests prove:**
    - `javascript:`, `data:`, relative URLs and userinfo URLs are refused.
    - A `link: false` URL renders as text.
    - Adding `link: true` is a proposal (an existing golden test, extended).
    - Playwright shows the anchor's `rel`.

#### G2

- [x] **B8 Proposal routes and tools** (after B2) ∥.
  - **Goal:** builders propose and track proposals, and Kyle reads,
    approves and declines them over HTTP.
  - **Design:** revision 3 "Tools" (`propose`, `proposal`); "Proposals".
  - **Agent routes:**
    - `apps propose` (`only`, `rollback_to` or `transfer_to`, plus
      `reason`);
    - `apps proposal` (`get` or `withdraw`).
    - `apps get` lists the App's open proposals.
  - **Kyle-session routes:** list, get (frozen bundle, delta, validation
    summary, base, and a diff against the current approved state), approve
    with `digest`, and decline with a reason.
  - **Plumbing:** broker actions, help topics, operation-catalog entries,
    facade classification, SDK regeneration.
  - **Files:** `api/app_data.py`, `services/mcp-broker/broker.py`,
    `test_apps_tool.py`, help topics, `live_operation_catalog.json`, the
    SDK, `tests/test_api_app_data.py`, and the lockstep suites.
  - **Tests prove:**
    - Approve and decline answer only Kyle's session. An admin API key, an
      agent run and a tool-call credential are each refused.
    - Approve without the shown digest is refused.
    - A non-owner can't propose.
    - Replaying a `request_id` returns the stored receipt.
    - The lockstep suites are green.
- [x] **B9 Action templates and `app_data.write@1`, server side** (after
  B4) ∥.
  - **Goal:** Kyle's session runs a page's create, update or delete
    template through a confirmed, digest-bound intent.
  - **Design:** revision 3 "Pages" (confirmation, intent binding, contract
    and auth); revision 6 "Pages" Release 1.
  - **Intent route.** It returns a server-generated confirmation:
    - the target record (current values and `version`);
    - the exact resulting values, presets included;
    - for a delete, the plan from `plan_delete`.

    It binds the page version, a payload digest, the target `version` and
    the plan digest. Intents live 5 minutes.
  - **Dispatch route.** It recomputes all of them, refuses on any
    difference with "confirm again", and writes through the records engine
    as `kyle`. It is idempotent per intent.
  - **Tables:** new appdata intent and receipt tables (D12).
  - **Catalog:** add `app_data.write@1` as a human-adapter entry.
  - **Validation:** refuse a template whose verb is tool-only for its
    collection (`JD-TEMPLATE-TOOL-ONLY`, D13).
  - **Rate limits:** the same per-principal and per-App limits as
    `live_invocations`.
  - **Files:**
    - `appdata/models.py` (new classes), new `appdata/intents.py`;
    - new `api/app_data_intents.py` (its own router);
    - `appdata/definitions.py` (`_check_template`);
    - `scripts/compile_live_operation_catalog.py`,
      `live_operation_catalog.json`, `db.py`;
    - new `tests/test_appdata_intents.py`.
  - **Tests prove:**
    - Each refusal case:
      - a stale target version;
      - a changed delete plan;
      - a republished page;
      - a tampered payload;
      - an expired intent;
      - a second dispatch;
      - an admin API key or an agent.
    - Presets can't be overridden.
    - Only `editable_fields` are accepted.
    - Templates on tool-only verbs are refused at validation.
- [x] **B10 Tool views in the language** (after B3 and B4) ∥.
  - **Goal:** a view can be a tool view, validated against the catalog and
    the App's App tool facts.
  - **Design:** "Tool views" (Eligibility, Binding, `cache: none`);
    "Vocabulary".
  - **Shape:** a view with `tool` takes `{view, tool, action, sources,
    params, description, cache: "default"|"none", materialize: {every,
    domain}}`. These match `authority.TOOL_VIEW_KEYS`.
  - **Validation:**
    - the action is `view_eligible` in the catalog;
    - every source role is declared by the action;
    - the App has an App tool fact for the tool that maps the role;
    - `params` match the action's declared params;
    - `materialize.domain` names a declared indexed field of a source (D8);
    - `every` is at least `5m`.
  - Pages may bind tool views like any view.
  - `capabilities()` and `apps schema` gain the shape.
  - **Files:** `appdata/definitions.py`, `appdata/authority.py` (if keys
    change), `tests/test_appdata_definitions.py`,
    `tests/test_appdata_authority.py`.
  - **Tests prove:**
    - Each of these is refused with a stable code:
      - an ineligible action;
      - an undeclared role;
      - a role with no App tool fact;
      - a bad domain;
      - an `every` under 5 minutes.
    - Binding to an existing App tool's view action self-publishes.
      Anything else is a proposal.

#### G3

- [x] **B11 Relay card for proposals** (after B8) ∥.
  - **Goal:** each new proposal posts a card that links to the review page
    and never approves anything.
  - **Design:** revision 3 "Proposals" → Notify.
  - **Changes:**
    - On `open`, post a card to the owner agent's DM with Kyle (D9). It
      shows the App, the proposer, the delta's first lines, and a link to
      `/apps/<id>/proposals/<pid>`.
    - On a state change, edit the card or post a follow-up.
    - The card has no actions.
  - **Files:** `appdata/proposals.py` (a notify hook), `relay_dm.py` or
    `relay_store.py` (a call site only), `tests/test_appdata_proposals.py`.
  - **Tests prove:**
    - Exactly one card per proposal, with the link.
    - A failed post doesn't fail the proposal.
    - The card carries no approve affordance.
- [x] **B12 Review page (web)** (after B8) ∥.
  - **Goal:** Kyle reviews and approves or declines a proposal from the
    builder area.
  - **Design:** revision 3 "Proposals" → Review; "Platform plumbing" →
    Web.
  - **Changes:**
    - A proposals tab on the App detail.
    - A review page showing:
      - the delta in plain words, grouped by kind;
      - the validation summary;
      - a definition diff;
      - the base version, and a stale banner when the App has moved.
    - Approve sends the digest shown. Decline takes a reason.
    - It follows the house style, both themes, 390 and 1280.
  - **Files:** `services/web/src/pages/StateApp.tsx`, a new
    `services/web/src/pages/ProposalReview.tsx`, `src/lib/appData.ts`,
    routes, `tests/mock-api.ts`, `tests/state-apps.spec.ts`.
  - **Tests prove** (Playwright):
    - The approve request carries the digest that was rendered.
    - A stale proposal disables approve.
    - The page passes the a11y spec.
    - `check-no-raw-hex` is green.
    - A visual review happens at the phase end.
- [x] **B13 Sharing** (after B8) ∥.
  - **Goal:** an App approved as shared works for the agents and the QA
    login it names, and for nothing more.
  - **Design:** revision 3 "Proposals" → "Sharing doesn't grant tools";
    "Vocabulary" (`login:qa`); "Collections" → Access.
  - **Agents named for read:**
    - `apps list` shows the App;
    - `app_data describe`, `query` and `get` work within their facts;
    - builder actions stay owner-only.
  - **`login:qa`:**
    - Kyle's GET state-App routes also answer the QA login, read-only,
      filtered by its facts (D16);
    - list shows only the Apps shared with it;
    - pages and views render with restricted fields hidden.
  - **Tests:** a test pins that sharing never adds a platform tool to the
    agent.
  - **Files:** `api/app_data.py` (the Kyle read-route dependency),
    `appdata/lifecycle.py` (`list_apps`, `can_read_page`),
    `tests/test_api_app_data.py`, `services/web/tests/state-apps.spec.ts`.
  - **Tests prove:**
    - A shared agent without `app_data` is still refused (no tool, no
      access).
    - With `app_data`, it reads only its fields. Predicates on hidden
      fields are refused.
    - The QA login can't write, can't see unshared Apps, and gets no
      template actions.
    - Narrowing by an approved proposal takes effect on the next request.
- [x] **B14 Action templates, web** (after B9) ∥.
  - **Goal:** v2 table and detail blocks show their templates, and confirm
    through the server's confirmation.
  - **Design:** revision 3 "Pages" (confirmation, intent binding).
  - **Changes:**
    - Action buttons appear per block (`actions`).
    - A confirmation dialog renders only the server's confirmation, with
      the delete plan for deletes.
    - Dispatch, then a receipt toast and a refresh.
    - A "changed, confirm again" path.
    - Forms only for `editable_fields`, typed by the field.
  - **Files:** `services/web/src/pages/LiveView.tsx` (the v2 path),
    `src/lib/appData.ts`, `tests/mock-api.ts`,
    `tests/state-apps.spec.ts`.
  - **Tests prove** (Playwright):
    - Create, update and delete flows.
    - A stale confirmation re-prompts.
    - Presets aren't editable.
    - No actions render for the QA login.
    - a11y passes.
- [ ] **B15 View-execution credential and `app_data scan`** (after B3, B5
  and B10) ∥.
  - **Goal:** a view action can stream rows from its declared sources as
    its viewer, through a per-call credential held by the views pool.
  - **Design:** "Tool-call credentials" (executor-mediated); "Tool views"
    (Execution, Bulk reads); "Trust boundaries".
  - **Credential.** A new kind, `view_exec` (D5):
    - claims: `call_id`, `principal` (a viewer, or `system:materializer`),
      `tool`, `action`, `sources`, and `exp` set to the action's timeout;
    - `cnf` set to the views pool's ServiceAccount;
    - minted by the API, never by the broker;
    - revoked at return.
  - **`app_data scan`.** A route on a role (D4). It takes `{source role,
    fields, filter, order}`, validated like a record view with no limit,
    and resolves the role through the App tool fact.
    - It runs `views.scan_view` as the principal, under
      `quotas.scan_budget`.
    - It records the fields read and filtered, per execution.
    - It refuses every other credential kind.
  - **Files:**
    - `appdata/credentials.py`, `api/auth.py`;
    - new `api/app_data_scan.py`, `appdata/views.py` (an ad-hoc spec entry);
    - `services/tool-executor/executor.py` (views-mode proxy);
    - tests: new `tests/test_appdata_scan.py`,
      `services/tool-executor/test_app_data_proxy.py`.
  - **Tests prove:**
    - Replay after return, a copied credential, the wrong `cnf` and a
      source outside the claim are each refused.
    - A run-JWT or tool-call credential can't scan.
    - Viewer facts apply: hidden fields aren't returned, and predicates on
      them are refused.
    - The per-execution, per-App, per-owner and concurrency budgets fail
      closed.
    - The fields-used record is exact.
- [ ] **B16 Restore reconcile, report and resume** (after B6 and B10) ∥.
  - **Goal:** after a restore, the platform reconciles every App's tool
    bindings against the current catalog, and Kyle resumes from a report.
  - **Design:** "Lifecycle" → Restore, steps 2–4; "Retired Apps".
  - **Reconcile.** On API start in `restore` mode, check each App's App
    tool facts and tool views against the toolregistry and catalog.
    - It disables bindings whose tool, action, role or eligibility is gone,
      as a health issue plus a `disabled_bindings` list. Definitions are not
      rewritten.
    - It reports Task watermarks (the last dispatched Task per agent against
      now).
    - The outbox check is a hook M1 fills in.
  - **Report.** A Kyle-session report route and a web banner plus report
    page. Resume (B6's route) is offered from it.
  - **Files:** new `appdata/restore.py`, `appdata/lifecycle.py` (health
    reads disabled bindings), `api/maintenance.py`, the web banner, and
    `services/web/src/pages/Settings.tsx` or a new
    `RestoreReport.tsx`, plus tests.
  - **Tests prove:**
    - A tool view whose action has disappeared is disabled and reported,
      not run.
    - Nothing runs before resume.
    - A retired App restored stays paused.
    - The report is deterministic.
- [ ] **B17 Reference view tool `app_summary`** (after B4 and B10) ∥.
  - **Goal:** one reviewed, generic view action, so tool views can be tested
    end to end and live.
  - **Design:** "Tools" (App tools); "Tool views" (D19).
  - **The tool:** a `tools/app_summary` platform tool with
    `app_access: {roles: [source], verbs: [read]}`. Its view action
    `counts` scans one role and returns counts per value of one declared
    field (top N), with a declared output schema and limits. Its effect
    policy is `reads_sensitive`.
  - **Files:** `tools/app_summary/{tool.yaml,run.py,README.md}`,
    `scripts/compile_live_operation_catalog.py` (the policy row),
    `live_operation_catalog.json`, `tests/test_toolregistry.py`, and the
    tool's own tests against a fake scan endpoint.
  - **Tests prove:**
    - The output validates against its schema.
    - It reads only through `_app_data.url`.
    - It's view-eligible in the catalog.
    - It has no egress needs: no secrets and no network beyond the
      endpoint.

#### G4

- [ ] **B18 Tool view execution** (after B5, B15 and B17) ∥.
  - **Goal:** pages, `app_data query` and `apps preview` run a tool view on
    demand, in the views pool, as the viewer.
  - **Design:** "Tool views" (Execution, Binding); "Trust boundaries" →
    Tool views.
  - **Changes:**
    - `run_view` dispatches tool views to a new `appdata/toolviews.py`. It:
      - mints a `view_exec` credential for the viewer and the resolved
        sources;
      - calls the views pool;
      - validates output against the action's schema, `max_rows` and
        `max_bytes`, and fails closed otherwise;
      - returns the same `ViewRows` shape with `as_of`.
    - Agents get the result in an untrusted-data block.
    - Disabled bindings (B16) answer 503.
    - Preview uses the caller's facts and never more.
  - **Files:** new `appdata/toolviews.py`, `appdata/views.py` (dispatch),
    `api/app_data.py` (hooks only), `appdata/lifecycle.py` (preview), new
    `tests/test_appdata_toolviews.py`.
  - **Tests prove:**
    - The viewer's facts bound the result: Kyle and a shared agent see
      different rows.
    - Schema-invalid and oversize output is refused.
    - A view action can't write, because its credential has no write path.
    - The views pool is used, never the default pool.
    - Disabled bindings are refused.
    - An admin API key gets nothing.
- [x] **B19 App-owned artifacts: close-out** (after B7 and B13) ∥.
  - **Goal:** artifact fields render in v2 pages, and sharing and narrowing
    move artifact access with the owning field.
  - **Design:** "Collections" → Artifact fields; "Trust boundaries" →
    App-owned artifacts.
  - **Changes:**
    - `page_for_web` and the web render artifact columns and detail fields
      as a thumbnail or link through the existing byte route.
    - Tests for the cases R1a couldn't reach:
      - sharing the owning field to `agent:x` by proposal lets x read the
        bytes and see feed events;
      - narrowing it through self-publish revokes both at once;
      - `login:qa` reads only when shared.
  - **Files:** `appdata/lifecycle.py` (`page_for_web`), the web v2 cells,
    `tests/test_appdata_artifacts.py`, `services/web/tests/state-apps.spec.ts`.
  - **Tests prove:** the cases above, plus "a second referencing field
    never widens", re-run after an approved sharing proposal.

#### G5

- [ ] **B20 Tool view cache** (after B18) ∥.
  - **Goal:** non-materialized tool-view results are cached under an
    authority-versioned key.
  - **Design:** "Tool views" → Cache.
  - **Key:** the viewer, the App's `approved_version` and
    `authority_generation`, the parameters, and the source collections'
    write counters.
  - **Storage:** a Postgres table with a byte cap and LRU pruning (D7).
  - **Behaviour:** `cache: none` bypasses it. A hit returns the original
    `as_of`.
  - **Files:** new `appdata/viewcache.py`, `appdata/models.py` (new class),
    `appdata/toolviews.py` (one lookup and store hook), `db.py`, tests.
  - **Tests prove:**
    - Each of these misses:
      - a write to a source;
      - a publish;
      - an approved proposal;
      - an ownership transfer (the generation bump);
      - another viewer;
      - other parameters.
    - `cache: none` never stores.
    - The cap prunes.
- [ ] **B21 Materialized views** (after B18) ∥.
  - **Goal:** heavy tool views refresh on a schedule as
    `system:materializer`, and are read with readers computed per access.
  - **Design:** "Tool views" → Materialized views.
  - **Runs.** On a schedule, every `materialize.every`, as
    `system:materializer`, limited to the declared sources. It is paused in
    maintenance mode (B6).
  - **Refresh requests.** A tool's refresh request after a batch-job
    commit, through a route its tool-call credential may use, is coalesced
    to at most once a minute.
  - **Parameter domain.** The distinct values of the domain field, read at
    each refresh (D8).
  - **Readers.** The intersection of the readers of every field the
    refresh read or filtered on (from B15's record), recomputed on each
    read.
  - **Freshness.** Results carry `as_of`.
  - **Files:** new `appdata/materialized.py`, `appdata/models.py` (new
    class), `appdata/toolviews.py` (a read hook), `scheduler.py` (the job
    hook), `db.py`, tests.
  - **Tests prove:**
    - A viewer who can't read one source field gets 403. Granting the field
      by proposal opens it on the next read, with no refresh.
    - Refresh requests coalesce.
    - The domain follows new values.
    - Nothing refreshes under maintenance.
    - A materializer credential can't write.
- [ ] **B22 Tool views in the web** (after B18) ∥.
  - **Goal:** pages show tool-view results like any view, with their
    freshness.
  - **Design:** "Tool views" → Freshness; "Pages".
  - **Changes:**
    - The table, detail and metric blocks bound to tool views render the
      result.
    - The `as_of` stamp is always shown for materialized views, plus a
      "stale" marker.
    - A disabled binding shows the 503 state.
  - **Files:** `services/web/src/pages/LiveView.tsx`, `src/lib/appData.ts`,
    `tests/mock-api.ts`, `tests/state-apps.spec.ts`.
  - **Tests prove** (Playwright): rendering, freshness display, the 503
    state, and both themes at 390 and 1280.

#### G6

- [ ] **B23 Docs and skill v2** (after B8–B22) ∥.
  - **Goal:** builders and maintainers learn proposals, sharing, templates,
    App tools and tool views.
  - **Docs:**
    - update `docs/building-blocks/apps.md` and `app-data.md`, the help
      topics, and `docs/building-blocks/backups.md` (restore maintenance
      mode);
    - update the design doc's "Storage" line on the GIN index and record
      the decisions below.
  - **Skill:** the `app-building` skill gains proposing, reading a delta,
    sharing, templates, App tools and tool views, and taking over a
    tool-only collection. Then a version bump, release manifest digests,
    the `plugin-release.yaml` attestation, and the pin in
    `plugin_release.py`.
  - **Tests prove:** `test_help.py` and the skill's
    `test_appdata_doc_examples.py` examples validate against the shipped
    language.
- [ ] **B24 Performance re-check** (after B1 and B21) ∥.
  - **Goal:** confirm that the index changes didn't regress A12's paths,
    and measure the new ones.
  - **Design:** `appdata-perf-2026-10.md`.
  - **Method.** Re-run `scripts/appdata_perf.py` (10⁶ bars and 10⁶
    results) with the partial indexes. Add three measurements:
    - a scan through the `scan` route at the 1M / 60 s cap;
    - one TCMS-shaped materialization refresh;
    - a cache hit and a cache miss.

    Record the numbers in the perf doc.
  - **Pass criteria:**
    - A12's gate paths stay within 20% of their numbers, or the change is
      reverted.
    - The materialization fits under its `every`.

#### Phase end

- [ ] **B25 Phase review** (after B23 and B24).
  - **Integration:** merge every B branch into `feat/r1b`.
  - **Code review:** Sol and Fable review the whole diff.
  - **Visual review:** the review page, templates, tool views and the
    restore banner, in both themes at 390 and 1280.
  - **Security pass** on:
    - the new credential kind;
    - the views pool's egress;
    - approve and dispatch auth;
    - the cache key;
    - materialized readers.
  - Fix every confirmed finding with tests, then run the full backend,
    broker, executor and web suites.
- [ ] **B-live Live verification** (after B25 is merged and deployed).
  - **With Claude's admin API key** (and `kubectl` on pai):
    - **Deploy and schema.** The deploy is healthy. The migrations ran: the
      GIN index is gone and the partial indexes exist (`psql` via
      `kubectl exec`).
    - **Views pool egress.** The `tool-executor-views` pod is up. From it,
      an outbound request to the internet fails and the API answers.
    - **Catalog.** The operation catalog lists `app_data.write@1` and
      `tool.app_summary.counts@1` as view-eligible.
    - **Refusals.** Approve, decline, intent dispatch and resume each refuse
      the admin key with 403.
    - **Maintenance status.** The maintenance status reads `running`.
    - **Restore drill.** Restore the latest backup into a scratch database
      (not the live one) and check that it comes up in `restore` with no
      fires.
    - **Agent flows**, once Kyle has granted `apps` and `app_data`:
      admin-key `create_run` drives Pai to:
      - create an App;
      - add the `app_summary` App tool (a proposal);
      - propose sharing a view with `login:qa`;
      - check the proposal status and the Relay card.
  - **Needs Kyle's session** (collect these under "Needs Kyle"; never
    through Chrome):
    - granting `apps` and `app_data` to Pai, Kai and Olu, if not done for
      A15;
    - approving Pai's proposals on the review page;
    - one create, one update and one delete through templates;
    - viewing the tool view and its `as_of`;
    - the QA login seeing only the shared page;
    - resuming from maintenance after a real restore.

  Record what was verified and what is waiting on Kyle under
  "Live verification".

#### Decisions needed (recommendations; proceed on them unless Kyle objects)

- **D1. Where App tool facts live.** The authority engine expects a
  top-level `app_tools`, but definitions have only collection, view and page
  kinds. **Recommend** a fourth kind, `tool`, one per tool, versioned with
  the rest.
- **D2. The credential's same-name role fallback** (`bind_roles` TODO)
  contradicts the design's "scope from the App tool fact". **Recommend**
  dropping it: no fact, no scope. No `tool.yaml` declares `app_access` today,
  so nothing breaks.
- **D3. Tool manifests have no per-action declarations.** **Recommend**
  `view_actions` in `tool.yaml`. Eligibility needs both that declaration and
  the reviewed effect-policy row.
- **D4. What `scan` takes.** Tool code is App-agnostic, so it can't name App
  views. **Recommend** an ad-hoc spec on a role (fields, filter, order). The
  platform records the fields used, and that feeds materialized readers.
- **D5. Credential for tool views.** The design says "no run, no App
  access", but views run for Kyle's session and for the materializer.
  **Recommend** a third kind, `view_exec`, minted only by the API, bound to
  the views pool's ServiceAccount, read-only.
- **D6. No-egress enforcement.** **Recommend** the pool always renders its
  own deny-all egress policy, independent of `networkPolicy.egress`.
- **D7. Cache store.** **Recommend** a Postgres table with a byte cap, not
  in-process memory: it survives restarts and holds if the API scales.
- **D8. Parameter domain.** "Maintained as records are written" conflicts
  with revision 6 cutting on-write materialization. **Recommend** the
  distinct values of an indexed field, read at each refresh.
- **D9. "The owner's home channel"** doesn't exist in Relay. **Recommend**
  the owner agent's DM with Kyle.
- **D10. Ownership transfers are "always proposals"**, but R1a has no
  transfer action. **Recommend** proposal kinds `bundle`, `rollback` and
  `transfer`, where the new owner is Kyle or an agent holding `apps`.
- **D11. Who authored an approved publish.** **Recommend** the proposer stays
  the author, with `approved_by=kyle` and the proposal id stamped, and
  `authority_generation` bumped on every approval.
- **D12. `app_data.write@1` storage.** `LiveIntent` is bound to LiveView
  rows. **Recommend** new appdata intent and receipt tables that reuse
  live-invocations' lifetime and rate limits.
- **D13. Templates on tool-only verbs** would pass authority, then always
  fail at write. **Recommend** refusing them at validation; R2 tool actions
  are Kyle's path there.
- **D14. How a restore knows it's a restore.** **Recommend** the export
  appending `mode=restore` to the dump, so every restore path lands paused.
  The outbox check waits for M1, since the outbox doesn't exist yet.
- **D15. What maintenance pauses in R1b:** crons, schedules, jobs, Tasks and
  materialization. Tool actions (R2) and service principals (M6) call the
  same gate when they ship. **Recommend** that Kyle-initiated runs stay
  allowed.
- **D16. `login:qa`.** The design names it as a reader, but the state-App
  routes are Kyle-only. **Recommend** opening the GET routes to it, filtered
  by facts, read-only.
- **D17. The c3 ref-history index.** **Recommend** deferring it to M2, and
  adding it only if TCMS gets a per-case history page.
- **D18. Dropping the GIN index contradicts the design's "Storage"**
  ("plus one GIN `jsonb_path_ops`"). **Recommend** dropping it, per the perf
  gate, and updating the design in B23.
- **D19. Live tool views need a real view action,** and none exists until
  M3. **Recommend** the small generic `app_summary` tool (B17), not a
  test-only fixture, so B-live can verify end to end.

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
  and `olu` from his browser session (Playwright MCP only, never Chrome), once
  R1a is deployed. These are Kyle-only grants (R0). Agents' use of Apps waits
  on this. A15 is verified with Claude's admin key against the App data API
  instead.

## Repairs

- **A4:** move `TOOL_APP_DATA_URL` out of the tool's environment (readable from `/proc` by sibling tools under the same uid) into the stdin payload.
- **A9:** publish doesn't lock out record writes that race its consistency check.
- **A9:** prune the record-write `build_ops` rows.
- **Done (`65f5af8`):** all the repairs above, plus housekeeping, quota routes, health limits and count checks.
- **Phase review (Sol, Fable), all fixed on `feat/r1a-fix2`:**
  - `6cab1c1`: an immutable upsert skipped artifact attach and detach.
  - `be7d9cc`: tool-call credentials reached no route. Also the facade quota exclusions, SDK drift, retryable receipts, quota transfer on agent delete, and tool-call credentials rejected off App data.
  - `c93f5c4`: a delete that would unlink needs `update` scope there.
- **Integration:** all of R1a, A12–A14, the fixes and origin/main are merged on `feat/r1a`. Next: PR, CI, merge, deploy, A15.

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
