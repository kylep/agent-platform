# Implementation tracker — capabilities, plugins, and live Apps

Design: [33-capabilities-plugins-and-live-apps](../../design/33-capabilities-plugins-and-live-apps.md).
Started 2026-09-25. This is a work checkpoint, not a command to run an agent loop.

## Done means

- Apps are database-owned collections of versioned pages and actions. The five
  existing Apps are represented in the collection model, and their domain
  services continue to own ingestion, calculations and specialized controls.
- An authorized person can draft, preview, publish and roll back a typed page
  without rebuilding the web image. Running proves private reads, a trusted
  Ticket action, refresh, a snapshot and a Resource read end to end.
- The operation catalog covers callable actions, not only top-level Tool names.
  Caller grants, object ACLs, budgets, effects and dispatch-time rechecks are
  enforced by the server. Denials and receipts are inspectable.
- MCP Resources expose authorized artifacts/snapshots without leaking private
  names, counts, thumbnails or bytes. Revocation works on new reads.
- `agent-platform-coding` is a reviewed, pinned, skills-only release that the
  exact platform Claude/Codex images and developer hosts can discover. An
  assignment does not add secrets, Tools, hooks or shell authority.
- Every existing App has a deliberate collection/page migration decision and
  verified UI route: News, Stockmarket, TCMS, Running, TTRPG. A domain's engine
  or specialized player interface stays in code where that serves users better.
- Chat Identities and MCP Apps export are completed or recorded as explicit,
  evidence-backed follow-ups if they cannot meet the design's compatibility
  and security gates. No silent claim of full migration.
- Focused and integration suites pass; browser tests cover desktop/mobile,
  permissions, action confirmation, receipts and rollback. Deploy to pai,
  verify live, then push main.

## Work packages

- [x] A0. Add DB-owned App collection identity and one-way legacy import.
  Keep domain manifests for infrastructure; make catalog categories visible.
  Commit `88a24b9`; 43 focused backend tests and web build pass locally.
- [ ] A1. Baseline current operations, principals, Apps, client versions,
  storage/caching and running data. Record a concise evidence matrix.
- [x] A2. Add additive App/View/version, ACL, intent and invocation schemas.
  Preserve legacy readers and old binary compatibility until cutover.
- [ ] A3. Compile operation contracts for a narrow Running read and Ticket
  write first, then inventory every core/custom Tool action. Unknown effects
  deny new live-page admission; catalog labels grant nothing.
- [ ] A4. Enforce viewer + View policy + object/target access on every call,
  including at dispatch. Add idempotent receipts and uncertain-outcome state.
- [ ] A5. Add private Resource list/read with conservative object ACLs, safe
  cache headers and revocation. Keep the existing artifact browser working.
- [ ] A6. Implement typed page drafts, fixture preview, publish, rollback,
  trusted action chrome, readable errors, refresh and snapshot capture.
- [ ] A7. Migrate Running collection to a live page backed by its existing
  projection. Keep the current route available during shadow/canary. Prove
  owner/denied read, issue Ticket, receipt, refresh and snapshot/Resource.
- [ ] A8. Build verified skills-only `agent-platform-coding` package;
  remove implicit skill-secret authority across launcher/readiness/registry;
  test exact pinned runner images and local install/update/rollback.
- [ ] A9. Migrate News, Stockmarket and TCMS collection/pages where the typed
  renderer fits. Preserve their ingestion and domain logic. Compare outputs
  against existing UIs and retain specialized screens when needed.
- [ ] A10. Migrate TTRPG collection/navigation while preserving its specialized
  game interface; test spectator/player flows and route rollback.
- [ ] A11. Integrate Chat Identity metadata/credential bindings and decide MCP
  Apps export from actual pinned-client tests. Record any narrowly deferred
  item with a concrete compatibility reason.
- [ ] A12. Complete docs/help, security and migration tests, NUC canary,
  one-week observation, deployment evidence and push. Remove legacy view code
  only after explicit retirement review; keep data protections monotonic.

## Current checkpoint

Local `main` contains commits `88a24b9` and `ca67446`, not yet pushed/deployed.
The App collection, catalog category, and typed page slices are additive. The
current write-action slice adds an explicit `tickets.create@1` grant, a browser
session-only short intent, immutable invocation receipt, dispatch-time policy
recheck, and trusted UI confirmation. Focused tests cover one-ticket replay and
revocation. It still needs snapshots, MCP Resources, a usable authoring editor,
and live canary evidence; do not represent it as a complete Live App. Existing untracked `.claude/` and
`codex-second-quota-pool.html` predate this project and must remain unstaged.

Quota reading at 2026-09-25 13:23 UTC: Codex weekly utilization 79%, reset
2026-09-25 17:18:35 UTC. It was observed at 12:57 UTC, so it may be stale.
Check the current reading before another expensive phase; pause at a clean
commit if weekly headroom approaches 10%, then resume after reset.
