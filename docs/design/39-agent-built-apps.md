# 39 — Agent-built Apps

Status: **revision 6 (final for review), 2026-10-02.**
- **Review history:** revisions 1–3 had two review passes each by Fable,
  Sol, Sonnet and Astra. Revision 4 rebuilt the design around Kyle's decisions
  below, and revisions 4 and 5 had two passes by all four.
- **This revision** folds in the last pass. It cuts what no migrated App needs
  and resolves the remaining credential, budget, materialization, restore and
  maintainer gaps.
- **Build plan:** `docs/superpowers/plans/<date>-agent-built-apps.md`.

Kyle's framing: Apps are for agents. Agents build and maintain them, from simple
reports to full tools. They can ask humans for new App Builder tools, but the
agents themselves build the Apps.

**Kyle's decisions (2026-10-02)**
1. **Builders:** any agent Kyle grants `apps` can build.
2. **Single user:** the platform is single-user. A logged-in browser session with
   the admin role is Kyle.
3. **Apps are pure state, and the whole catalogue migrates.** A fresh install has
   no Apps; restoring a backup brings every App back. Code lives only in tools.

## The problem

1. **Apps are still deployed code.**
   - Six Apps (news, running, stockmarket, tcms, judgment, ttrpg) are services
     with their own schemas, images, Kafka topics and API keys.
   - The provisioner creates them from `apps/*/app.yaml`, and
     `import_legacy_apps` re-creates them on every boot, so a fresh install gets
     all six.
   - A restored backup brings their schemas back owned by the wrong role and
     unusable (`backup_restore.py` skips `app-*` secrets, and the provisioner has
     no GRANT path).
   - TTRPG's world lives on a volume and isn't backed up at all.
2. **Agents can't author anything.** Live-view authoring is `require_admin`,
   `_reader` rejects agent keys, and no platform tool creates an App.
3. **There's nowhere to put data without code.** Live pages read ten hardcoded
   bindings into those services and offer two actions.
4. **Nothing teaches agents** when an App is the answer, how to model one, what
   needs Kyle's approval, or how to keep it working.

## What this delivers

- **Apps as state.**
  - An App is rows in one platform-owned store: definitions plus records.
  - A fresh install has none, and `pg_dump` restores them all, working.
- **Code only in tools.** Tools do the computation and write Apps:
  - connectors, such as Strava and Yahoo Finance;
  - App tools, such as backtest, tcms, judgment, news, running and ttrpg;
  - platform tools.

  A tool writes as its caller, through a per-call credential, or as a named
  service principal Kyle approved. Tools also expose reviewed read actions that
  pages can bind to.
- **Builders.** Any agent granted `apps` creates and maintains Apps through
  versioned, attributed, resumable steps.
- **An authority model Kyle controls.** Changes publish freely until they widen
  what anyone can see or do. Widening is a frozen proposal Kyle approves from his
  session.
- **The whole catalogue migrated.** Six Apps rebuilt as state, with data moved,
  parity checked, and maintainers handed runbooks they've been tested against.
  The coded services, schemas, images, keys and topics are deleted.
- **A request path** for missing primitives, and an **`app-building` skill**.

Not delivered:
- author HTML or JavaScript;
- Apps visible to humans other than Kyle;
- cross-App reads;
- an expression language.

## Starting point (review)

### The six coded Apps (inventory, 2026-10-02)

**news.**
- **Data:** topics (~20); items (~30/day, about 10k/year, no retention).
- **Written by:** a Kafka consumer on `app.news.inbound`, fed by the `news`
  agent's result.
- **Code to move:** digest parsing, freshness gates, URL and headline dedup
  (`same_story` over 7 days), automatic topics, and the daily report.
- **UI:** calendar heatmap, sparkline, item lists and ILIKE search.

**running.**
- **Data:** activities (~1/day); briefs (~52/year).
- **Written by:** Kafka, fed by the `strava` tool's sync and the
  `running-coach` agent's fenced brief JSON.
- **Code to move:** stats (heatmap, weekly bars, streaks, PRs, pace),
  `coach-context` and `sync_after`, brief cleaning, and the weekly report (the
  app loop writes it under its key).
- **UI:** calendar heatmap, bars, totals, PR board and lists.

**stockmarket.**
- **Data:**
  - symbols;
  - bars, 10⁵–10⁶ rows, with `day` stored as a string today;
  - watchlist and briefs;
  - five backtest tables, including `rows_gz` dataset blobs of tens of MB and
    about 20k series plus about 120k events per experiment.
- **Written by:**
  - the `prices` tool (upsert) and the `backtest` tool, both binding the App's
    database;
  - Kafka, for briefs;
  - the App key, which starts runs for watchlist backfills and reruns.

  Pending symbols are retried by the daily sync.
- **Code to move:** brief parsing, report rendering (343 lines, 2 MB cap), and
  backtest read shaping (thinning to 400–500 points, monthly rollups).
- **UI:**
  - a normalized multi-line index chart anchored on the archive's newest day;
  - stat tiles and an add-symbol form;
  - backtest list, detail (value/contributed and drawdown charts) and compare.

**tcms.**
- **Data:**
  - cases, about 1k, whose `steps`, `automation`, `tags` and `tickets` are JSON
    lists, some of them lists of objects;
  - runs;
  - results, 10⁵–10⁶ rows with 90-day retention;
  - coverage.
- **Written by:** the `tcms` tool, which records results and coverage in one
  transaction, de-duplicated by run. A 30-second reconciler publishes runs,
  posts to `#qa` and prunes.
- **Code to move:** windowed aggregates over the last 20–30 runs (flaky
  same-commit flips, slowest, PERCENT_RANK prune candidates).
- **UI:** pyramid, stat rows, sparklines, filtered tables and health tables.

**judgment.**
- **Data:** beliefs, versions, predictions, links, feedback and requests.
- **Written by:** the `judgment` tool (Kai only), whose code enforces the guards;
  and Kyle's page.
- **Code to move:** resolution, flags, review counts, version-level delete
  cascades and strict session auth.

**ttrpg** (external repo `claude-ttrpg`).
- **Engine:** a CLI over a world directory on a volume, with about 400 tests on
  that persistence.
- **Coordinator:** a 3-second loop that summons players through Relay, polls
  runs and quota, and pauses on budget, all under the App's operator key.
- **UI:** map renders, GM lens, table controls, and live updates over SSE.

**Not actually live:** the Discord "post" formatters in news, running and
stockmarket are called only from tests, and nothing consumes the
`*.brief.posted` / `*.item.ingested` events. Neither is carried over.

**Plumbing to retire:**
- App images, Helm `apps.enabled`, and `app-<name>-db` / `-key` secrets;
- `app.<name>.*` topics and the `result_topic` wiring for news, running and
  stockmarket;
- `AppProvisioner` and `import_legacy_apps`;
- `query_app` and the ten live-view read adapters;
- the `app:<name>` report-writing keys.

### Unchanged from revision 3
- **Live pages:** typed/v1, intent → call → receipt actions, and client-side
  preview.
- **Authority gaps:** `agents_grant` and `agents_edit` can reach any agent,
  including the caller.
- **Delivery:** skills ship in attested releases. `entrypoints.topics` has no
  consumer.
- **Credentials:** the executor receives identity, never credentials. Run JWTs
  are bound to the agent's ServiceAccount and live for the run's timeout plus 10
  minutes (`runjwt.py`).
- **Artifacts:** stored in Postgres, 8 MiB per artifact, 2 GiB total
  (`config.py`). The byte route checks artifact and run visibility only.

## Principles

1. **Apps are state; code is tools.** No App has an image, a schema or a deploy.
2. **Construction is not authority.** Widening needs Kyle's approval of exact
   content.
3. **Every writer is a named principal.** A tool writes either as its caller,
   through a per-call credential, or as a service principal whose App facts Kyle
   approved. Nothing writes an App with a standing, unnamed key.
4. **Guards hold on every path.** If a tool's code enforces an App's rules, the
   App can require that collection to be written only through that tool.
5. **Typed, not scripted.** Nothing an agent writes executes.
6. **An App is consistent or unpublished.**
7. **Data outlives definitions.**
8. **Builds and handovers are resumable.** An agent that didn't build an App can
   take it over from what the platform records.

## Vocabulary

- **App:** an immutable id, a never-reused name, an owner (an agent or Kyle),
  versioned definitions and build notes. All rows; no code.
- **Collection, view, page, action template, bundle, approved state:** as in
  revision 3.
- **Tool view:** a view backed by a reviewed tool read action.
- **Tool action:** a page button backed by a reviewed tool write action.
- **Tool-call credential:** a token minted for one tool call (see below).
- **Service principal:** `tool:<name>`, a tool's own identity for work with no
  calling run. Its App facts are approved by Kyle.
- **`kyle`:** a browser session (`X-AP-Auth: session`) with role `admin`.
- **`login:qa`:** the QA agent's read-only browser login, a principal Apps may
  name for reading.
- **Builder:** holds `apps`. **Maintainer:** an App's owning agent.
- **Connector, App tool, platform tool:** the tool categories. "Domain
  capability" is retired, and `prices` becomes the Yahoo Finance connector.

## Apps as state

### Lifecycle

- **Existence:** an App is only rows in `app_data`; there's no `app.yaml`,
  provisioner or Helm toggle.
- **Fresh install:** zero Apps. `apps/` is deleted after the migration.
- **Restore:** `pg_dump` covers `app_data` and artifacts. Both are
  platform-owned, so restore needs no per-App roles, grants or secrets.
- **Restore is a database point in time, not a platform point in time.** Tool
  code, Tasks already run and external systems have moved on, so restore runs
  in **maintenance mode**:
  1. The scheduler, Tasks, service principals and tool actions start paused.
  2. The platform reconciles every App's tool facts against the current tool
     catalog. Bindings to tools or actions that no longer exist are disabled
     and reported.
  3. It checks each App's outbox and Task watermarks.
  4. Kyle resumes automation from the restore report.

  A drill restores into a fresh cluster and checks that every App renders and
  that nothing runs before resume.
- **Retired Apps:** restoring an older backup returns them, with automation
  paused, which is the point-in-time meaning. There are no tombstones.
- **Export and import** are deferred. No migrated App needs them, and they
  arrive through the request path if wanted.

### Tool-call credentials

Today the executor receives identity, never a credential. Revision 6 adds one,
and keeps it out of tool code entirely:

- **Exchange.** For a tool with App access, the broker asks the API for a
  **tool-call credential**, presenting the caller's bearer and run JWT. Claims:
  - `call_id`, `run_id`, `agent`, `tool`, `action`;
  - the App and collection scope from the App's **App tool fact** (below);
  - for view actions, the declared sources;
  - `exp` set to the tool's timeout.
- **A ceiling, not a grant.** The credential limits what the call may attempt,
  and the API still checks every write against current facts.
- **Deletes stay in scope.** A delete that would unlink refs in another
  collection needs `update` scope there, or the whole delete is refused;
  cascade (R2) must follow the same rule with `delete` scope.
- **Executor-mediated.** The credential goes to the executor, never to the
  tool process. The tool calls `app_data` through a per-call local endpoint the
  executor exposes for that subprocess. The executor attaches the credential,
  checks that the request belongs to the live `call_id`, and drops the endpoint
  when the call returns. A credential copied out of the executor pod is
  useless: the API verifies it with a distinct verifier, `cnf` set to the
  executor's ServiceAccount, single `call_id`, and it's revoked at return.
- **Recognition.** `authenticate` gains this credential kind and stamps
  writes `author = agent:<name>, via = tool:<name>`.
- **No run, no App access.** Admin API-key callers get no credential.
- **Kyle's page actions** use a separate claim set: `principal = kyle`,
  `intent_id`, the exact action, targets and budget, and no `run_id`. It's
  minted from a confirmed intent and executor-mediated the same way.

### Service principals

Only the TTRPG coordinator works with no calling run, so service principals
ship with M6, not before. A service principal `tool:<name>`:
- has a dedicated ServiceAccount and workload identity;
- holds **App facts** (collections and verbs) inside the App's authority,
  where granting them is a proposal;
- holds **platform grants** separately, as a Kyle-approved grant on
  `tool:<name>` itself: post to one named Relay channel, schedule Tasks for
  named agents, read quota. Each platform system enforces its own grant, and
  Tasks (design 37) gains a service-principal creator;
- runs under **durable budgets**: every Task, Relay post and record write is
  charged atomically to a persistent counter *before* the effect, with a
  stated reset period (hourly and daily). A restarted coordinator can't
  exceed its budget. Kyle can revoke the grant, which takes effect on the
  next charge.

Reports (news, running, stockmarket) are generated by maintainer crons calling
the tool's `report` action. They are agent calls, not service work.

### Tool-only collections

A collection may declare `writers: {create: [tool:judgment], update:
[tool:judgment]}`. It can then be written only through that tool's credential,
and the owner's direct `app_data` writes are refused. Code-enforced guards
(judgment's confirmation rules, TCMS's recording transaction) can't be
bypassed. Kyle's page writes to such a collection go through that tool's
actions (tool actions on pages, Release 2).

Adding the constraint self-publishes only when the tool is already an **App
tool** of that App; otherwise it's part of the proposal that adds the tool.
Removing it is a proposal.

### Tool views

A **tool view** binds a page or agent query to a tool read action.

- **Eligibility.** The action is `view_eligible`:
  - catalog effects are only `reads_sensitive`;
  - `tool.yaml` declares an output JSON Schema, max rows and bytes, and
    **source collection roles** that the App's App tool fact maps to real
    collections.
- **Execution.** On demand, the action runs with a credential built from the
  *viewer's* facts, limited to the declared sources. It runs in a no-egress
  executor pool, so it can't reach anything but the platform API.
- **Bulk reads.** View actions read through `app_data scan`, a stream with
  viewer facts applied. Scans are bounded:
  - per execution: 2,000,000 rows and 60 seconds;
  - per App: scan rows per hour and two concurrent scans;
  - per owner: a total. Breaches fail closed.
- **Materialized views** cover heavy views, such as TCMS's flaky, slowest and
  prune, and stockmarket's backtest rollups.
  - **Runs as:** the read-only principal `system:materializer`, limited to the
    declared sources.
  - **When:** on a schedule (`every: 10m`), plus refresh requests a tool makes
    when it commits a batch job. Those are coalesced to at most once a minute.
  - **Parameters:** a materialized view either declares a finite parameter
    domain, such as "every experiment id", maintained as records are written,
    or isn't materializable and uses the cache only.
  - **Readers:** the intersection of the readers of every source field it read
    or filtered on, recomputed on each access, so an access change takes
    effect immediately.
  - **Freshness:** results carry `as_of`, and pages show it.
- **Cache.** Non-materialized results are cached, keyed by:
  - the viewer;
  - the App's approved version and authority generation;
  - the parameters;
  - the source collections' write counters.

  `cache: none` turns it off (TTRPG).
- **Binding.** Binding a tool view self-publishes for an existing App tool's
  view action; otherwise it's a proposal.

### What leaves the platform
After the catalogue migrates:
- App images and `apps/*`;
- Helm `apps.enabled`;
- `AppProvisioner` and `import_legacy_apps`;
- `app-<name>-*` secrets and keys;
- `app.<name>.*` topics and `result_topic` wiring;
- `query_app`, which agents replace with `app_data query`;
- the hardcoded read adapters, with every `typed/v1` page migrated to v2.

## The authority model

As in revision 3, with these fact types:

| fact | tuple |
|---|---|
| field access | `(principal, collection, field, verb)` |
| record delete | `(principal, collection, delete)` |
| delete reach | `(collection → collection, on_delete)` closure |
| retention | `(collection, max_age \| max_records)` |
| rules | each rule canonical, including tool-only writers |
| action templates | `(template, kind, collection, presets, editable_fields)` |
| **App tools** | `(tool, role → collection, verbs)`: which tools may read or write which collections. This is the binding a tool's manifest roles resolve through |
| tool views and actions | `(tool, action, sources, verbs, budget)` on pages |
| service principals | `(tool:<name>, collections, verbs)`; platform grants live outside the App |
| outbound links | `(collection, field)` |

- **Self-publish:** a bundle that is consistent with every fact present or
  narrower. Unmapped fields mean proposal.
- **New fields** start private.
- **Rules:** adding self-publishes (tool-only writers only for existing App
  tools); relaxing is a proposal.
- **Always proposals:**
  - action templates;
  - adding an App tool or widening its verbs;
  - tool actions;
  - service principal facts;
  - shorter retention;
  - outbound links;
  - wider delete reach;
  - data-dropping changes;
  - wider rollbacks;
  - ownership transfers.

**Proposals:** a frozen digest plus a computed delta. Approval happens from
Kyle's session, re-validates and compare-and-swaps atomically; anything that
moved makes the proposal stale. A Relay card links to the review. Sharing
never grants tools.

**Phase 0:**
- Kyle-only tools (`apps`, `app_data`, `agents_grant`, `agents_edit`);
- protected agents, editable only by Kyle or themselves;
- no self-edits;
- an audit and a test matrix;
- narrow agent paths;
- `owner_agent`, with transfer to Kyle when the agent is deleted.

## Collections

- **Field types:**
  - `string`, `text`, `int`, `number`, `bool`, `date`, `datetime`, `enum`;
  - `ref` (same App);
  - `url` (text unless `link: true`);
  - `artifact`;
  - `list` of a scalar (Release 2);
  - **`list` of `object`** (Release 2): one level of declared sub-fields, each a
    scalar type, at most 50 items. This is for TCMS's steps and automation.
  - `ref` with `pin_version` (Release 2).

  There is no free-form JSON.
- **System fields:** `id`, `created_at`, `updated_at`, `author`, `via`,
  `version`, `collection_version`.
- **Write modes:** `editable` and `immutable` (Release 1); `versioned`
  (Release 2).
- **Access:** collection defaults with per-field overrides, checked per field
  for the actual caller on every path (results, predicates, `expand`, history,
  previews, tool views, scans).
- **Tool-only writers:** see above.
- **References:** same App only. `restrict` and `unlink` in Release 1, `cascade`
  in Release 2, all with server-computed delete plans.
- **Indexed fields:** up to four per collection, copied into typed side columns
  `ix_text1`, `ix_text2`, `ix_num1`, `ix_time1`. Fixed composite indexes:
  - `(app_id, collection, ix_text1, ix_time1)`
  - `(app_id, collection, ix_text1, ix_text2, ix_time1)`
  - `(app_id, collection, ix_text1, ix_num1)`
  - `(app_id, collection, ix_time1)`

  These serve bars (symbol, day), results (run, ref) and backtest series
  (experiment, strategy, day).
- **Retention:** `max_age` or `max_records`, pruned daily by a platform job
  through the delete plan, respecting `restrict`. It's an authority fact.
- **Artifact fields:**
  - An artifact written into an App field becomes **App-owned**: the byte route,
    metadata reads and the artifact event feed all authorize it through the
    referencing field's access, so the `artifacts` grant alone doesn't reach it.
  - App-owned artifacts may be up to 64 MiB each, counted against the App's byte
    quota (the platform total rises accordingly).
  - They are deleted when their last referencing record is.
  - **Single owning field.** An App-owned artifact has exactly one owning
    field, the first one that references it. Another field may reference it
    only if its readers are a subset of the owning field's readers, so a second
    reference never widens access. The event feed is filtered per recipient
    through the owning field.

### Batch writes

`app_data batch`:
- **Limits:** up to 5,000 records or 5 MiB per call, in one transaction, with
  per-record errors.
- **Modes:** `insert`, `upsert` (keyed on a `unique` rule; bars) or
  `skip_existing` (TCMS runs). Upserts into `immutable` collections replace a
  record only when its values differ, and nothing goes to `record_versions`.

**Batch jobs** handle larger writes, such as TCMS results and backtest events:
- `batch_job` opens a **staging set** bound to its creator (principal, tool,
  call) and the collection versions.
- Batches go into the set; staged records are invisible.
- **Commit** re-validates creator authority, schema versions and quotas, then
  publishes all of it in one transaction, or refuses.
- Sets expire after 24 hours.

**Release 1a performance gate:** load 10⁶ bars and 10⁶ results, then measure
the stockmarket, TCMS and backtest read paths, a 140k-record backtest write,
and one TCMS materialization. Timeouts are set from the numbers.

### Rules

Release 1: `writer`, `immutable_after_create`, `unique` and tool-only
writers. New rules are checked against existing records.

`required_when`, `lock` and schema migrations are **deferred to the request
path**: no migrated App needs them.

### Schema evolution

Additive changes publish normally. A destructive change means a new
collection, a copy and retiring the old one. Migration verbs arrive through
App Builder requests if Apps need them.

### Storage

- **One platform schema, `app_data`:**
  - `records` (side columns plus JSONB `doc`);
  - `record_versions`;
  - `definitions`;
  - `bundles` and `proposals`;
  - `build_ops`;
  - `staging_sets`;
  - `budgets` (service principals and page actions);
  - `outbox` (migration only).
- **Indexes:** fixed only, plus one GIN `jsonb_path_ops`.
- **Quotas:** platform-owned. Migrated Apps get measured sizes:
  - stockmarket ~2M records and 1 GiB;
  - TCMS ~1.5M and 600 MiB;
  - news ~50k;
  - running ~10k;
  - judgment ~10k;
  - TTRPG measured at M6.

  Backup size and time are checked with these volumes, plus the TTRPG
  snapshots.

## Views

As in revision 4, plus:
- **`contains` moves to Release 1a,** escaped and case-insensitive, for news
  search over about 10k items a year.
- **`within_last` takes `anchor`:** `now` (the default) or `max(<field>)`, for
  windows anchored on the newest data (stockmarket's index chart).
- **Window functions (Release 3):** `normalize: first` and `downsample: N`.
- **Time and `fill_missing`:** as in revision 4 (App timezone, Monday weeks,
  `12w` = current partial week plus 11 full weeks).

## Pages (`typed/v2`)

- **Release 1:** table, detail, metric and text; `create`, `update` and
  `delete` templates with server-generated confirmations. Intents bind
  version, payload, target version and delete plan. Writes go through
  `app_data.write@1` and need Kyle's session.
- **Release 2 — tool actions on pages** (moved from 3, because judgment needs
  them):
  - **Credential:** each one mints a page-intent credential with the exact
    action, targets and a budget. The credential carries the budget; the
    store, Tasks and the `budgets` table enforce it.
  - **Idempotency:** enforced at dispatch.
  - **Starting work:** an action that starts agent work schedules a Task for
    a named maintainer, with a `request_id` derived from the intent, so a
    retry never schedules twice.
- **Release 3:** chart, calendar, sparkline, stat_row, list_filter, image and
  `refresh`.
- **TTRPG:** its table drops from SSE to a 3–5 s poll. A `live` mode is an App
  Builder request if needed.

## Tools

### `apps` (builder) and `app_data` (records)
As in revision 4, plus:
- `app_data` adds `scan` (view actions only), `batch` modes and `batch_job`.
- `apps` adds `authority`, which prints the App's current facts in plain words.
- Export and import are deferred (see "Lifecycle").

### Tool changes for the migration

| tool | becomes |
|---|---|
| `prices` | **Yahoo Finance connector.** Upserts bars and symbols. Its daily sync also processes `pending` symbols. |
| `strava` | Connector. Writes activities and a `sync_state` record (cursor and `completed_at`) to the Running App. |
| `backtest` | App tool. Writes experiments through a `batch_job`. Datasets become App-owned artifacts (64 MiB parts). Materialized `series` view (parameter domain: experiment ids). `rerun` action (a Task for `stockmarket-data`, with a budget). |
| new `stockmarket` | App tool. `add_symbol` (watchlist record plus `pending` symbol plus a backfill Task), `brief` (parse, clean, write), and `report`. |
| `tcms` | App tool. `record_results` runs a `batch_job` (results and coverage atomically, `skip_existing` on the run), then marks the run `committed`. The `#qa` post is recorded as `notified_at` on the run. Every `record_results` and the nightly start by posting any committed-but-unnotified runs, so a crash never loses a notice. Materialized flaky, slowest and prune views. Tool-only collections. |
| `judgment` | App tool. Tool-only collections. Guards, the version-level delete cascade, resolution and flags stay in code, as write actions and tool views. Kyle's page uses its actions. |
| new `news` | App tool. `ingest` (parsing, freshness gates, 7-day dedup through `app_data`, topics) writes items **and a period receipt** (accepted and rejected counts per digest). `report` too. |
| new `running` | App tool. Tool views `stats` and `coach_context` (including the sync freshness from `sync_state`). A `brief` action, keyed by completed week, refuses if the last sync completed before that week ended. `report` too. |
| `ttrpg` | App tool, plus `tool:ttrpg` at M6. |

**A safety net replaces `result_topic`.** Producing agents' prompts require the
tool call. Each maintainer's cron checks the period's **receipt** (a news
receipt, a running brief, a stockmarket brief), not the presence of records.
So "zero items accepted" never looks like a failure. A missing receipt raises a
ticket in the App's home channel.

## TTRPG

The engine stays in `claude-ttrpg` (recommended), and the **App is
authoritative**:
- **World state lives in the App:**
  - a **command ledger** (immutable, totally ordered by sequence);
  - periodic **snapshots** (App-owned artifacts);
  - small collections for pages.
- **Ledger entries** record each command *and its resolved outcome* (dice,
  random draws, engine decisions). Replay from a snapshot is therefore
  deterministic without a deterministic engine.
- **Writes:** the coordinator appends with a compare-and-swap on the next
  sequence; concurrent writers lose and retry.
- **Exactly-once effects:**
  - each command has an id and two checkpoints: `dispatched` (Tasks and Relay
    posts issued, each with a `request_id` from the command id) and `applied`;
  - on restart, the engine rebuilds from the latest snapshot plus the ledger,
    then finishes any command that is `dispatched` but not `applied`;
  - the `request_id`s make re-dispatch a no-op.
- **Volume:** a cache, rebuilt at start and after any restore.
- **Engine work:** this needs a storage adapter in `claude-ttrpg`. **It is not
  sized in this design.** Sizing it is the first M6 task, and M6's estimate
  stays open until then.
- **Coordinator:** runs as `tool:ttrpg`, with App facts on the TTRPG App and
  platform grants for `#ttrpg-table`, Tasks for the four players and
  `ttrpg-gm`, and quota reads, all under durable budgets. Pause and resume and
  quota pauses keep today's semantics.
- **Pages:** player-safe and GM-only views are separated by field access. GM
  tool views are readable by `ttrpg-gm` and Kyle. Table controls are tool
  actions.

## The catalogue migration

**Order, by dependency:** judgment and TCMS need Release 2, which now includes
tool actions on pages. Running, news and stockmarket need Release 3.

| step | needs |
|---|---|
| R0 → R1a → R1b → R2 | — |
| **M1 Judgment** | R2 (versioned, pin_version, tool actions on pages) and tool-only collections |
| **M2 TCMS** | R2 (lists of objects), batch jobs, materialized views |
| R3 | — |
| **M3 Running** | calendar, bars, tool views, `brief` |
| **M4 News** | `contains`, calendar, sparkline, new tool |
| **M5 Stockmarket** | charts, normalize, anchor, artifacts, tool actions |
| **M6 TTRPG** | the engine storage adapter (sized first); service principals |

**Per App:**
1. **Definitions.**
   - A migration bundle, reviewed in its PR and published through Kyle's
     approval, with these owners:
     - news: `news-librarian`; its producer `news` holds a tool-only ingest
       grant;
     - running: `running-coach`;
     - stockmarket: `stockmarket-data`;
     - tcms: `qa`;
     - judgment: `kai`;
     - ttrpg: `ttrpg-gm`.
   - A per-agent **authority matrix** (tools, App facts, skill) is approved with
     it.
2. **Data copy.**
   - A checkpointed batch from `app_<name>` (or the TTRPG volume) into
     `app_data`.
   - Serial integer ids get new ids, recorded in a mapping table, and refs are
     rewritten.
   - Blobs become artifacts.
   - It dry-runs first.
3. **Two-way sync.**
   - **Before cutover,** writes still land in the old App, including Kyle's page
     writes such as judgment confirmations and watchlist adds. The copy job keeps
     running incrementally from the old store to the new one by watermark, so
     the new store doesn't drift.
   - **At cutover,** the direction flips. Tools and pages write the new store,
     and an **outbox** with a sequence watermark writes the old one.
   - Writing back to the old store lasts **until removal**, so a rollback after
     cutover loses nothing.
4. **Parity.**
   - Row counts.
   - Sampled API-versus-view comparisons.
   - Screenshots side by side (both themes, 390 and 1280).
   - The agents' real flows.
   - Query latency on the large collections.
5. **Cutover.**
   - Readers and pages switch.
   - Prompts are updated: `query_app` becomes `app_data query`, result topics
     become tool calls, and the new safety nets are added.
   - TCMS cases and web e2e tests that assert old behaviour are rewritten.
   - Old links redirect.
6. **Takeover gate.** A fresh run of the maintainer, with no prior conversation
   and only `apps get`, `apps schema`, `apps authority`, `app_data describe`,
   tool help and the App's build notes, must complete the App's routine: the
   brief, nightly, backtest, curation or turn. The build notes must hold:
   - purpose and invariants;
   - schemas and view parameters;
   - dependencies and authority;
   - a **runbook** covering freshness, failed receipts, retries, backfills,
     restore and escalation.
7. **Removal**, after a week clean.
   - Old writes are disabled atomically at the watermark.
   - Then the code, image, Helm entry, schema (after a final backup), secrets,
     key, topics and `result_topic` wiring are deleted.
8. **Restore drill.**

**Done** when all six are migrated, `apps/` is empty, the provisioner,
`query_app` and the adapters are gone, and a fresh install shows no Apps.

## Automation, request path and skill

- **Automation:** Tasks and crons, as today. Data triggers are **deferred to
  the request path**, since no migrated App needs them.
- **Request path:** as in revision 4. Migration gaps become App Builder
  requests.
- **Skill:** as in revision 4, plus:
  - taking over an App: read `authority`, the build notes and the runbook
    first; never write a tool-only collection directly; check health before
    acting;
  - writing build notes that pass the takeover gate.

## Trust boundaries and guards

Revision 3's guards, plus:
- **Tool-call credentials:**
  - per call;
  - executor-bound;
  - limited to declared Apps, verbs and sources;
  - revoked at return.

  There are no reusable credentials in tool code.
- **Executor-mediated credentials:** the tool process never holds a credential.
  A credential copied out of the executor is bound to one live call and is
  revoked when the call returns.
- **Service principals:** a named identity. Its App facts are approved by
  proposal and its platform grants are approved separately. Durable,
  pre-charged budgets survive restarts.
- **Tool views:**
  - the viewer's authority, limited to declared sources;
  - a no-egress executor pool;
  - schema- and size-bounded output;
  - an authority-versioned cache key;
  - materialized summaries readable only by the intersection of their sources'
    readers (output and filter fields), recomputed on each access;
  - scan budgets per execution, per App, per owner and by concurrency.
- **Tool actions:** an intent-bound credential with an exact action, targets
  and budget, and idempotency.
- **Tool-only collections:** code-enforced guards can't be bypassed through
  direct writes.
- **App-owned artifacts:** authorized through their referencing field on every
  route, including the event feed.
- **Retention** is an authority fact. Imports are deferred.
- **Batch jobs** re-validate their creator's authority, schema and quotas at
  commit.
- **Restore** runs in maintenance mode: automation stays paused until the tool
  catalog and authority are reconciled and Kyle resumes.
- **Migration:**
  - incremental copy before cutover;
  - outbox after cutover, until removal;
  - atomic disable of old writes.

## Rollout and effort

Revision 6 cuts what no migrated App needs:
- data triggers;
- `required_when` and `lock`;
- schema migrations;
- export and import;
- on-write materialization;
- tombstones.

Each comes back only through an App Builder request. Revision 6 also adds the
hidden work the reviews found.

| step | contents | rough size |
|---|---|---|
| **R0** | Authority fixes and their test matrix. | ~6 tasks, 1 week |
| **R1a** | The store (indexed fields, retention, artifacts with owning fields, batch and batch jobs, `contains`). Executor-mediated tool-call credentials and their verifier. The `app_data` and `apps` tools. v2 table, detail and metric. The authority engine with golden tests. Quotas and scan budgets. The performance gate. Skill v1. Plus: broker, toolregistry and agentspec changes; SDK and facade regeneration; operation-catalog entries. | ~36 tasks, 8 weeks |
| **R1b** | Proposals and the review page. Sharing. Action templates and `app_data.write@1`. Tool views: the no-egress executor pool (infrastructure), scan, cache, materialization. App tool facts. Tool-only collections. App-owned artifact auth. Links. Restore maintenance mode. | ~24 tasks, 5 weeks |
| **R2** | versioned, pin_version, cascade, lists (including objects), exists, `new_version`, **tool actions on pages** (page-intent credentials, budgets, Task scheduling). | ~16 tasks, 4 weeks |
| **M1, M2** | Judgment and TCMS, each with the copy job, outbox, parity, the takeover-gate run, and TCMS case and e2e rewrites. | ~12 and ~16 tasks, 2 + 3 weeks |
| **R3** | Grouping and aggregates, windows, anchor, chart, calendar, sparkline, stat_row, list_filter, image, refresh. | ~16 tasks, 4 weeks |
| **M3–M5** | Running, news and stockmarket, each with the new App tools, safety-net receipts, prompt rewrites, takeover gate, and e2e and screenshot parity. | ~14, 14 and 20 tasks, 3 + 3 + 4 weeks |
| **M6** | TTRPG: service principals, durable budgets, ledger checkpoints, plus the `claude-ttrpg` storage adapter. | ~16 tasks here, plus an **unsized** engine project |
| **Cleanup** | Delete the provisioner, `query_app`, adapters, `apps/` and the Helm templates. Prompt sweeps. Backup-size check. Fresh-install and restore drills. | ~10 tasks, 2 weeks |

**Total:** about 9–10 months of build here, **excluding the TTRPG engine
adapter**, which is sized as M6's first task. **Milestones:**
- the first agent-built App after R1a, about 9 weeks in;
- the first migrated App (judgment) after R2, about 4½ months in.

## Alternatives considered

- **Keep coded Apps alongside agent-built ones.** Rejected by Kyle.
- **Per-App tables.** Rejected: schema churn and broken restores.
- **Tools keep binding App databases.** Rejected: they would bypass access and
  rules, and break restores.
- **Hand tools the run JWT.** Rejected: it's reusable for the whole run and
  bound to the agent's identity. A per-call, executor-bound credential is the
  minimum.
- **Tool views computed purely through paged `app_data` reads.** Rejected:
  TCMS-scale aggregates would need hundreds of calls. Use scan and
  materialization instead.
- **Author HTML/JavaScript for rich UIs.** Rejected for private data.
- **Author-declared guards and collection-only access.** Rejected in revisions
  2 and 3.

## Open decisions (Kyle)

1. **Kai's `agents_grant` and `agents_edit`:** keep them under the new limits
   (recommended), or remove them? Under the limits they become grants only Kyle
   can make, and they can't touch agents that hold builder tools.
2. **TTRPG engine:** keep `claude-ttrpg` separate, with only its state moving
   into the App and a storage adapter written there (recommended), or bring the
   engine into this repo?
3. **Pace:** about 9–10 months, plus the TTRPG engine work. Proceed in this
   order, with check-ins after R1a (the first agent-built App) and after M1
   (the first migration)?
