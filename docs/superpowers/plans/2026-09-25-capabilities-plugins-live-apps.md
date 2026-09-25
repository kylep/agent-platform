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
- [x] A1. Baseline current operations, principals, Apps, client versions,
  storage/caching and running data. Evidence:
  [33-live-baseline](../../design/33-live-baseline.md) and
  [33-operation-inventory](../../design/33-operation-inventory.md).
- [x] A2. Add additive App/View/version, ACL, intent and invocation schemas.
  Preserve legacy readers and old binary compatibility until cutover.
- [ ] A3. Compile operation contracts for a narrow Running read and Ticket
  write first, then inventory every core/custom Tool action. Unknown effects
  deny new live-page admission; catalog labels grant nothing.
  The branch-by-branch admission inventory is now recorded in
  [33-operation-inventory](../../design/33-operation-inventory.md); the
  generated/compiled catalog and additional admitted adapters remain open.
- [ ] A4. Enforce viewer + View policy + object/target access on every call,
  including at dispatch. Add idempotent receipts and uncertain-outcome state.
- [x] A5. Add private Resource list/read with conservative object ACLs, safe
  cache headers and revocation. Keep the existing artifact browser working.
- [x] Running canary deployed to pai: four live metrics, ticket action with
  durable receipt, private snapshot and MCP Resource read. Browser checked at
  desktop and 390px mobile; ENG-7 was created then cancelled as test cleanup.
- [ ] A6. Implement typed page drafts, fixture preview, publish, rollback,
  trusted action chrome, readable errors, refresh and snapshot capture.
- [x] A7. Migrate Running collection to a live page backed by its existing
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

Main through `19c9ebb` is pushed and deployed. The Running canary passed API,
browser and MCP Resource checks. The plain-HTTP `crypto.randomUUID` failure
was fixed with `crypto.getRandomValues`; a real ticket action then passed.
The skills-only plugin passed both manifest validators, 115 focused backend
tests, 72 runner tests, and exact pinned lean/Workbench runner image smoke
tests for Claude and Codex skill paths. It is live in the skill catalog;
`coder` has orientation/change and `qa` has orientation/regression. Their
existing DB definitions round-tripped without unrelated changes.
The repo now has Codex and Claude marketplace manifests. Both were registered
and the package installed on Kyle's laptop (Codex 0.156.1, Claude Code
2.1.282); CLI inventories and cached SKILL.md hashes matched. Local
update/rollback and model-driven behavior samples are still open.

Commit `bcd60ff` adds a typed JSON source/preview editor,
publication history, restricted domain-interface links, and bounded summary
reads for News, Stockmarket and TCMS. It is deployed as a canary. Published
DB pages: Running `e653369d2d814705a2104afb272815c7` (v2), News
`b753850e165544309b9701d5c29c3a1e`, Stockmarket
`838ff69d3f9c4a8984e660c063f0e349`, TCMS
`e21cdc52364b4db1b5ad73634b1c04a0`, and TTRPG
`4fa2c80ea71c4184a570779dbde123f0` (the latter four v1).
News/Stockmarket/TCMS live reads returned 200 with only approved fields;
the News page and editor passed desktop/390px Playwright checks. Specialized
domain screens remain the linked interface for richer controls. This is a
collection/page migration, not deletion of those services. The general Tool
operation inventory, additional action adapters, Chat Identity metadata and
MCP Apps compatibility gate remain open. Existing untracked `.claude/` and
`codex-second-quota-pool.html` predate this project and must remain unstaged.

Commit `a325a2a` adds `ap://artifact/<id>` for Studio images
and other binary artifacts. Its dedicated API route rechecks owner/admin access
and deletion on every read, with `private, no-store`; the facade forwards only
the caller bearer and does not expose the byte route as a generated Tool.
Artifact and facade tests passed locally. The backend and facade were deployed;
the live MCP template list includes `ap://artifact/{artifact_id}`, and a
`user:admin` image read returned a binary blob through the MCP Resource API.
The local denied-reader and deletion tests cover revocation without creating
or deleting a production artifact.

Commit `6c4c6e5` pins the assigned SKILL.md hashes into each runner Job;
the runner now reports a failed run if a synced skill changed or vanished
between launch and install. Focused launcher and runner suites passed
(24 and 72 tests). Backend and both runner images were imported to pai;
API, dispatcher, recorder and facade rolled out healthy.

Commit `19c9ebb` adds a bounded, owner-only recent-activities adapter and
typed table block. Running's published page is version 3 with ten live rows;
the adapter strips unapproved fields. The backend and web images are live;
the API returned exactly ten rows and desktop/mobile browser smoke passed.
The first mobile screenshot exposed narrow table wrapping, so a card-style
mobile layout is being finished before this phase closes.

Quota reading at 2026-09-25 15:36 UTC: Codex weekly utilization 84%, reset
2026-09-25 17:18:35 UTC. It was observed less than a minute earlier.
Check the current reading before another expensive phase; pause at a clean
commit if weekly headroom approaches 10%, then resume after reset.
