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
  The branch-by-branch inventory and generated 93-entry catalog are recorded
  in [33-operation-inventory](../../design/33-operation-inventory.md).
  Eleven operations have bounded human adapters with JSON input/output schemas,
  target scopes, caller kinds and enforced read limits. The remaining broker
  and custom actions have explicit null contracts and are ineligible. Their
  schemas, target checks and budgets remain open; each further admission
  requires separate policy work.
  A fixed-target Relay channel read checks current membership on every
  request, returns ten bounded text rows, and cannot be snapshotted. A second
  effectful adapter can post human-reviewed text to a fixed internal room,
  with membership/bridge rechecks, no mentions, and a durable receipt.
- [ ] A4. Enforce viewer + View policy + object/target access on every call,
  including at dispatch. Add idempotent receipts and uncertain-outcome state.
- [x] A5. Add private Resource list/read with conservative object ACLs, safe
  cache headers and revocation. Keep the existing artifact browser working.
- [x] Running canary deployed to pai: four live metrics, ticket action with
  durable receipt, private snapshot and MCP Resource read. Browser checked at
  desktop and 390px mobile; ENG-7 was created then cancelled as test cleanup.
- [x] A6. Implement typed page drafts, fixture preview, publish, rollback,
  trusted action chrome, readable errors, refresh and snapshot capture.
  The editor now previews metrics and table rows from synthetic values derived
  from the reviewed output schemas; it performs no live calls. Publication,
  rollback, refresh, trusted Ticket action and snapshot capture are deployed.
- [x] A7. Migrate Running collection to a live page backed by its existing
  projection. Keep the current route available during shadow/canary. Prove
  owner/denied read, issue Ticket, receipt, refresh and snapshot/Resource.
- [ ] A8. Build verified skills-only `agent-platform-coding` package;
  remove implicit skill-secret authority across launcher/readiness/registry;
  test exact pinned runner images and local install/update/rollback.
  The platform build now pins the approved release-manifest digest, so a
  modified skill plus recomputed file hashes cannot enter the runtime catalog.
  This is source-reviewed approval for the current private deployment; CI
  signer/build attestation, local update/rollback and behavior evaluation
  remain open.
- [x] A9. Migrate News, Stockmarket and TCMS collection/pages where the typed
  renderer fits. Preserve their ingestion and domain logic. Compare outputs
  against existing UIs and retain specialized screens when needed.
  All three published DB pages are primary entry points from Apps. Their
  summary fields matched the existing domain APIs on pai, and the detailed
  screens remain linked for domain controls that typed pages do not yet cover.
- [ ] A10. Migrate TTRPG collection/navigation while preserving its specialized
  game interface; test spectator/player flows and route rollback.
- [ ] A11. Integrate Chat Identity metadata/credential bindings and decide MCP
  Apps export from actual pinned-client tests. Record any narrowly deferred
  item with a concrete compatibility reason.
  MCP Apps export is deferred with a concrete host/auth/intent matrix in
  [33-mcp-apps-compatibility](../../design/33-mcp-apps-compatibility.md):
  the first-party browser is the verified interactive host, while the pinned
  CLI runners have no tested MCP Apps UI path. Chat Identity migration remains
  open; the existing Discord transport and bindings are unchanged.
- [ ] A12. Complete docs/help, security and migration tests, NUC canary,
  one-week observation, deployment evidence and push. Remove legacy view code
  only after explicit retirement review; keep data protections monotonic.

## Current checkpoint

Main through `3b13447` is pushed and deployed; CI for that commit passed every
job. All five Apps now open their
published DB pages from the Apps directory, with explicit detailed-app links
to the existing specialist screens. A 390px Chromium pass found the five
expected live-page and specialist routes, no browser errors and no horizontal
overflow. Running, News, Stockmarket and TCMS live summaries matched their
existing domain APIs field for field. TTRPG's published page shows ten recent
`#ttrpg-table` text messages through a fixed, membership-checked read; its
specialized game UI remains linked. See the [live baseline](../../design/33-live-baseline.md)
for measured values and presentation limits. The backend suite passed 1819
tests before the later isolated chat presentation change; 22 focused chat
tests and the web build/token gate passed after it.

Still open: catalog contracts for additional broker/custom actions; stronger retained plugin release/update
evidence; TTRPG interaction parity; Chat Identity metadata; MCP Apps host
compatibility; one-week observation and eventual specialist retirement review.
The previous checkpoint below records the earlier canary steps.

Commit `cb0150e` closes an MCP discovery leak: the facade now checks every
bearer with the platform's `/api/whoami` before serving initialization or Tool
metadata, forwarding only the bearer and no cookie. The API still authorizes
the actual Tool/Resource call. On pai, an invalid bearer returned 401 and the
owner's valid bearer initialized with 200. The facade suite passed 28 tests;
the web suite reached 286/288 before two stale smoke fixtures were corrected,
and the three affected tests then passed. The next CI run passed its backend,
facade and guard jobs, but found a separate one-pixel-range mobile overflow
on the Memories page (288/289 web tests passed). Its filter row now wraps and
its search input can shrink; the failing test passed ten consecutive local
runs. The new full CI run passed every job.

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
The first mobile screenshot exposed narrow table wrapping; the next commit
changed it to cards.

The mobile layout shipped in `7d269e7`; the final 390px browser check showed
ten readable activity cards with no horizontal overflow. News v2 now adds ten
recent story rows (`72ad3eb`), and Stockmarket/TCMS v2 add user watchlist and
recent test-run rows (`bbb0474`, nullable verification fix `2d98920`). All
three were read and browser-checked live at desktop and 390px. The Stockmarket
principal's watchlist is empty, which is rendered as an empty state; TCMS
returned four runs. The specialized domain views remain working and linked.

Commit `4464732` makes the page-level snapshot button capture every published
read instead of silently saving only the first alias. It is deployed and
live-verified: one Running snapshot contained the summary and ten recent
activities, then was deleted. The focused live-view suite passed (18 tests)
and the web production build passed. At this checkpoint all five App collections
have a published page, with data-rich tables on four; TTRPG keeps its game UI.

Quota reading at 2026-09-25 15:36 UTC: Codex weekly utilization 84%, reset
2026-09-25 17:18:35 UTC. It was observed less than a minute earlier.
Check the current reading before another expensive phase; pause at a clean
commit if weekly headroom approaches 10%, then resume after reset.
