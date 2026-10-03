# Agent-built Apps: efficient delivery plan

**Status:** planning only; implementation remains paused at Kyle's request.
**Source of truth for behavior:** [design 39](../../design/39-agent-built-apps.md).
**Detailed backlog and live evidence:** [original build plan](2026-10-02-agent-built-apps.md).
**Current working tree:** [pause checkpoint](2026-10-02-agent-built-apps-checkpoint.md).

## Delivery contract

The finish line has not changed: six Apps migrated into database-owned state,
maintainers operating them through the new interfaces, old App machinery
removed, fresh install empty, restore working, and final images live on pai.
Today the **foundation is deployed; 0/6 Apps are migrated**. The uncommitted
versioned-history work is neither shipped nor a completed user journey. Do not
forecast completion from a percentage until at least one migration has
provided a measured cost and exposed the reusable work.

This plan changes the *order and process*, not the required correctness
properties. A platform primitive is built when a named App's acceptance
scenario needs it. A milestone ends in a visible working App, not an API
capability version. The first agent-created App smoke check is the opening
gate of the Judgment milestone, with no separate review or deployment cycle.

## Dependency and gap map

This is a planning map from design 39's inventory; verify each row against
current code before implementation. `Required` means needed for existing
behavior, not that the entire generalized feature is already specified.

| App / order | Existing behavior to preserve | Likely missing capability | Data / operational gate |
|---|---|---|---|
| **Judgment, first** | Beliefs, versions, predictions, links, feedback, requests; guarded resolution and version-level deletion; Kyle's actions | Versioned history, pinned refs, tool-only writes and reviewed page tool actions; bounded cascade if actual delete graph needs it | First agent-built App proof, parity, one maintainer takeover |
| **TCMS, second** | Cases with nested lists; atomic results and coverage, deduplicated runs, reconciliation, retention and health views | Lists are shipped; only the aggregates/history/ref behavior the current UI and tool actually use | Resolve PostgreSQL capacity first; 10⁵–10⁶ results; restore drill |
| **Running, third** | Activity sync, stats, weekly brief, coach context and report | Specific calendar/bar/stat views and tool actions consumed by its page | Freshness receipt, parity and takeover |
| **News, fourth** | Freshness/dedup/topics, digest and report, search | Ingest tool, bounded search and the calendar/sparkline used by its page | Keep gatherer/projector/connector separation; verify downstream effects |
| **Stockmarket, fifth** | Prices, watchlist, briefs, backtests, large datasets and charts | Required chart/normalize/anchor views, artifacts, action-driven backfills and reruns | Full-volume capacity, query budgets and worst-case result handling |
| **TTRPG, sixth** | World/turn persistence, player/GM orchestration, maps and table controls | A *sized* adapter in `claude-ttrpg`, service principals and durable budgets | Cross-repo contract, ledger checkpoints, restore and turn proof |

Avoid implementing `exists`, generic cascade, every chart type, or an
optional ref-history index solely because they appear in R2/R3. Do implement
them if code inspection or a parity test shows a real dependency. No feature
is silently deleted from the accepted design: an unused one stays deferred
with the App and scenario that would justify it.

## Milestones and acceptance gates

### 0. Short preparation, no release

Preserve the current uncommitted history diff on a named branch or exact-file
patch after inspecting it for secrets; do not deploy it alone. Produce a
six-row *verified* gap matrix from current code, with source data tables,
writer, reader, UI and side effects. Size the TTRPG adapter separately and
measure PostgreSQL backup, free space and growth. Make the Kyle-session grant
of `apps` and `app_data` to Pai, Kai and Olu a scheduled dependency, rather
than a footnote; their admin API keys cannot do it. No new feature code starts
until a scenario names its consumer.

**Gate:** every proposed R2/R3 addition has a specific App behavior and a
testable reason; the first migration's acceptance script and storage path are
written. Preparation is a short inventory, not a research phase.

### 1. Judgment and the first live agent-built App

First use the shipped foundation to have an agent create and publish a small
collection, view and page, propose a sharing change, then let Kyle approve
and exercise one reviewed action. This is a smoke gate *within* Judgment, not
a standalone release. Then build only Judgment's missing R2 behavior, a
reusable migration path, its App definitions, data copy, parity check,
controlled cutover and takeover. Include the uncommitted history slice when
the Judgment scenario needs it; review it together with history UI and its
consumer, not as a background-only release.

**Gate:** one scripted evidence file shows the agent build, approval and
action; record counts and stable content checks agree; the version/history
and guard flows match the old App; unreviewed actions, changed target or
authority generation, maintenance writes and wrong callers are denied; one
fresh maintainer run succeeds with only documented App interfaces. Remove
Judgment's old path only after cutover and rollback evidence.

### 2. TCMS and storage capacity

Resolve pai's non-expandable 2 GiB PostgreSQL PVC with an explicit capacity
and restore plan **before** moving the large result set. Reuse Judgment's
migration machinery, add only TCMS-consumed history, aggregates and ref
behavior, and preserve the result/coverage transaction and deduplication.

**Gate:** full-volume count and content parity, reconciler effects exactly
once, measured query limits and backup size, a scratch restore of migrated
state, and a fresh QA maintainer run. No high-volume copy or cutover without
capacity margin and a demonstrated restore path.

### 3. Running and news

Implement as two independently reversible App cutovers. They may share a
single integration review, broad test run and deployment if both are ready;
do not hold one indefinitely for the other. Introduce only the R3 components
their current pages use. Preserve running's sync and weekly-brief receipt;
preserve news freshness, 7-day dedup, actual notifications and the separate
gatherer/projector/connector privileges.

**Gate:** each App independently passes data and user-flow parity, its safety
receipt, permission denial and a fresh maintainer takeover.

### 4. Stockmarket

Reuse the migration and presentation work. Move bars, briefs, watchlist and
backtests with the necessary artifact handling and bounded chart queries.

**Gate:** full-volume reconciliation, measured slow paths and storage margin,
worst-case backtest output handled within limits, receipt/retry behavior and
maintainer takeover.

### 5. TTRPG and removal

Only after its adapter contract and effort are measured, implement the
`claude-ttrpg` storage adapter in that repository and the minimum principals,
budget and checkpoint behavior here. Preserve real player/GM turn behavior.
Then remove the provisioner, `query_app`, App read adapters, `apps/`, images,
Helm entries, old secrets and topic wiring after checking for live callers.

**Gate:** all six takeovers recorded; no legacy App callers remain; a fresh
install creates no Apps; a final encrypted backup restores every App and its
operational state; relevant CI checks pass and final images are live. TTRPG's
cross-repo size is an uncertainty to resolve early, not an excuse to redefine
the six-App finish line without Kyle's decision.

## Token and iteration budget

| Cost center | Default | Escalate only when |
|---|---|---|
| Implementation | One continuing primary agent, with a compact state note and exact files; optionally one cheaper model for a bounded mechanical task | Parallel work has disjoint files and actually shortens the milestone |
| Review | One independent, bounded diff review per user-visible milestone | A specific unresolved authority, credential, outbox, migration or budget risk needs a specialist second look |
| Tests | Focused affected tests while editing; broad relevant suites **once** after integration; save logs to file and read summaries | A fix changes a cross-cutting contract or a broad test fails |
| Browser | One scripted Playwright journey per UI milestone, plus targeted visual states | A visible regression or new responsive behavior warrants more |
| Deployment | One build/import/rollout and scripted live check per milestone | A production defect requires an immediate fix |
| Research/context | Open the compact state note, affected design section and files; no repeated full-plan read or six-agent research fan-out | New facts invalidate the design assumption |

The cost model is qualitative: subscription usage grows with fresh agent
contexts, repeated large file reads, full-suite output in model context,
review/repair rounds and model-driven polling. Before each milestone, state
the expected **number of agent calls, review calls, full test runs and
deployments**. Record the before/after five-hour and weekly quota readings
as observations; do not invent a linear token-to-quota conversion. Use a
script to collect test and live evidence, and read its short result once.

The historical memory's **20% headroom** is the admission threshold for a
new milestone. Kyle's requested **10% left** is the hard floor for active
work: below 20%, finish an already-open milestone only if its remaining
steps plausibly fit above 10%; otherwise checkpoint it. Never start a new
large phase that is likely to run into the reserve. Recalibrate the budget
after Judgment using measured usage and actual remaining scope.

## What not to trade away

Keep Kyle-only grants and approvals, current-field redaction on old record
versions, frozen tool/intent credentials, authorization over a complete
delete plan, idempotent receipts, maintenance refusal, source/destination
parity, a single writer at takeover, news privilege separation, and exact
staged-diff secret inspection. Keep a rollback path through each cutover.
These are correctness gates, not process ceremony.

Drop the old loop's per-primitive PR, dual-model review, repeated full-suite
and deploy cadence, repeated percentage estimates and model-driven live
polling. Direct-to-main commits remain subject to local tests, secret review
and the milestone's live evidence. CI remains a final release signal, not a
reason to run `gh run` after every local change.

## Review reconciliation

This plan was challenged by Codex Astra and Luna and by read-only Claude
Sonnet and Opus sessions. All four preferred migration-driven capabilities,
fewer review/deploy boundaries and scripted evidence. Sonnet proposed a
separate foundation proof; Opus argued Judgment itself should be that proof.
The chosen approach makes proof the first Judgment gate, so failure is found
early without a separate release. Astra warned that the earlier 30–35%
forecast was unsound with 0/6 migrations and an unsized TTRPG adapter; this
plan reports deliverables instead. The two historical quota rules are
reconciled above as admission threshold and hard reserve.
