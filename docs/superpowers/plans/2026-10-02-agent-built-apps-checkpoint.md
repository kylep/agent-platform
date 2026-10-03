# Agent-built Apps: pause checkpoint

**Paused:** 2026-10-02, at Kyle's request. **Resume source:**
[`2026-10-02-agent-built-apps.md`](2026-10-02-agent-built-apps.md) and
[`39-agent-built-apps.md`](../../design/39-agent-built-apps.md). This page is
the short, current handoff; the build plan is the detailed specification.
The revised delivery rhythm is in
[`2026-10-02-agent-built-apps-efficient-delivery.md`](2026-10-02-agent-built-apps-efficient-delivery.md).

## What the project is for

Replace the six Apps defined in platform code with Apps built and owned as
database state. Agents should be able to create collections, views and pages,
share them through reviewed authority changes, and maintain them through
platform tools. Migrate each existing App with parity and a takeover gate,
then remove the old provisioner, read adapters and hardcoded App definitions.
The separate `claude-ttrpg` repository gets an adapter during the final
migration, rather than being absorbed into this repository.

## Honest status

The earlier **30–35%** estimate was not a reliable forecast: none of the six
Apps has migrated, and the TTRPG adapter remains unsized. The useful progress
measure is **foundation deployed; 0/6 Apps migrated; first full agent journey
pending**. R0 (authority limits),
R1a (state-backed App foundation) and R1b (proposals, templates, tool views,
sharing and restore behavior) are implemented on `main` and deployed to pai.
Typed list fields, the first R2 increment, are also deployed. A backup restore
drill found and fixed the maintenance-marker problem.

The essential outcome is **not yet delivered**: none of the six legacy Apps
has been migrated, the code-defined App machinery still exists, and an agent
has not yet completed the full live build/share/action flow. The latter needs
Kyle's browser-session grant of `apps` and `app_data` to Pai, Kai and Olu.
The admin API key correctly cannot make that grant or approve proposals.

Current deployed commits: `cece7c2` (backup restore fix), `af83213` (typed
lists), with plan note `4ec072b`; `origin/main` pointed to `4ec072b` at this
checkpoint. R1b deployment was Helm revision 91; later direct workload
rollouts brought the backup and list changes live. All referenced API,
dispatcher, recorder, broker, facade and web workloads were healthy at their
last live check. Do not infer their current state without checking.

## Work in the local tree, not shipped

There is an **uncommitted R2 versioned-history slice** touching App data
definitions, authority, records, API, MCP broker, docs and tests. It enables
`write_mode: versioned`, redacted record history and exact-version reads. A
new authority fact requires approval when an existing collection begins
exposing past versions. It bumps capabilities to v5. Focused checks passed:
426 backend tests, 83 broker tests and `git diff --check`. It has **not** had
a final review, commit, push, deployment or live check. `new_version` page
actions and `detail.history` are **not** in this slice. Preserve or explicitly
discard this work when resuming; do not mistake it for deployed behavior.

The untracked `.claude/`, `codex-second-quota-pool.html` and
`codex-session-id.tmp` are unrelated. Never stage them or use `git add -A`.
Before any commit, inspect the exact staged diff for credentials and secrets.

## Remaining work in useful order

1. **Finish R2 coherently:** review and ship versioned history; complete
   `new_version` and history display, pinned refs, safe cascade deletion,
   `exists` filters and page tool actions. Test those as one user journey,
   rather than a rollout after every small backend addition.
2. **Prove an actual agent-built App:** from a granted agent, create and
   publish a collection, view and page; exercise a proposal and a reviewed
   action in the UI. Record what is verified and what needs Kyle's session.
3. **Migrate by vertical slice:** judgment, then TCMS, then running, news and
   stockmarket. For each, copy data, match existing reads and writes, prove
   the outbox and takeover gate, cut over, and only then remove its old path.
   These migrations are the bulk of the remaining product work.
4. **Add R3 presentation primitives** as the first migrations demonstrate
   concrete needs: grouping, charts, calendar, image and refresh. Avoid
   implementing the whole abstract palette before a real App uses it.
5. **TTRPG and cleanup:** add the external storage adapter and budgeted
   principals, remove the provisioner and hardcoded Apps, and perform a fresh
   install plus backup/restore drill.

Two operational prerequisites remain: pai's PostgreSQL PVC advertises 2 GiB
and its `local-path` StorageClass cannot expand it, despite substantial host
free space; resolve sizing and restore mechanics before the high-volume TCMS
and stockmarket copies. Kyle-session App grants and approvals cannot be
substituted with an admin API key.

## Why progress felt inefficient, and a better resume rhythm

The plan was written as a Claude multi-agent loop, with PRs, CI, phase reviews
and a deployment for every slice. I followed too much of that machinery while
working alone, and reported small platform primitives as progress on a much
larger migration. A full 2,981-test backend suite after typed lists was useful
once; repeating it for each small change would waste time. Deploying history
before its page action and UI would create another technically live but
user-invisible increment.

On resume, use **one end-to-end milestone at a time**: define the user-visible
behavior and one live acceptance journey; implement its backend, broker and
web together; run focused local checks while editing; run broad checks once
at the milestone boundary; then deploy and verify once. Keep a short log of
the exact commit, test result, live result and blockers. Reassess the overall
percentage only after a migration or comparable user-visible milestone,
rather than after each capability. Respect Kyle's weekly quota floor of 10%
left, and check it at milestone boundaries.

**Immediate resume decision:** either finish the uncommitted history slice as
part of the complete R2 versioned-record journey, or set it aside and prove
the simpler agent-built App journey first. The latter provides the fastest
evidence that the shipped foundation delivers the project's purpose.
