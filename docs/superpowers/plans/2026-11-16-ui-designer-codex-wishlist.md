# UI Designer: feature wishlist and Codex prompt

Written 2026-11-16, after Phase 1 shipped. What exists today is in
`docs/building-blocks/ui-designer.md`; the reasoning is in
`docs/design/46-ui-designer.md`.

## Where it stands

Live:

- **Documents:** JSON documents in Postgres, edited through batched ops with a
  compare-and-swap version check. Versions fold, and `restore` brings one back.
- **Rendering:** a separate ui-renderer service (resvg) turns a document into
  PNGs, which go back to agents as MCP images.
- **Agent tool:** `mcp__platform__ui_designer` covers create, edit, get,
  outline, render, history, history diff, restore, inspect, comments, label,
  place and export. Export makes a PNG artifact and posts a `#design` card.
- **Web:** `/ui-designer` is read-only, with comments, side-by-side compare,
  labels, export and a live SSE refresh.
- **Paint and effects:**
  - fills: up to 8 stacked; colour, linear, radial, angular and diamond
    gradients, and images;
  - shadows: several per node, drop or inset, with spread;
  - blur: layer blur, and an approximated backdrop blur;
  - 17 blend modes;
  - strokes: dash, cap, join and per-side widths;
  - also masks, rotation, clipping, auto layout, tokens with modes, and
    components with instance overrides.
- **Fonts:** Inter, JetBrains Mono, Noto Sans, Nunito and Fredoka.
- **Figma converter:** `ui_designer/figma_convert.py` turns a Figma node dump
  into a document. Its report lists every approximated or dropped node.
  `desk-buddy` was imported this way, from a replay of the scripts that
  built it, because the Figma Starter plan's MCP read limit ran out.

## Wishlist (most valuable first)

### P1: completes our core design workflow

1. **Figma REST importer (design 46 T11).** Use the `figma-token` secret,
   the internal executor tool, a resumable dispatcher job, staged pages, a
   status route, a web "Import from Figma" button and a `#design` card. The
   converter already exists. This adds fetching, images and the job. The
   Figma MCP on Starter allows only a few calls, so REST with a PAT is the
   route to rely on.
2. **Editing in the web inspector (T13, first half).**
   - Select a node on the canvas, edit its props in a side panel, drag to
     move or resize, and nudge with the arrow keys.
   - Every change is one `edit` batch with `expected_version`, so agents and
     Kyle never overwrite each other.
   - Add undo and redo in the browser.
3. **Component variants and properties.** A component set with variant axes
   (state=hover/pressed, size=s/m/l) and typed props (text, boolean, instance
   swap). Instances pick a variant and set props. Agents currently copy whole
   frames for each state.
4. **Prototyping.**
   - `link_to` on any node: target frame, trigger (tap, hover, after-delay),
     transition (instant, dissolve, slide).
   - A click-through viewer at `/ui-designer/<slug>/play`.
   - The spectator HUD and desk-buddy flows need this to be reviewable.
5. **Export formats.** SVG as a deliberate artifact type, PDF, and an
   HTML/CSS or React snippet export from `inspect` ("Dev Mode").
   - Tokens become CSS custom properties.
   - That is the path from design to the real desk-buddy and game code.

### P2: better results from agents

6. **Visual diff.** An overlay of two renders with changed boxes outlined,
   available both in Compare and as a tool action. Agents should see what
   their edit changed, not only the new picture.
7. **Design lint as a tool action.** Contrast (WCAG), text overflow and
   clipping, off-grid spacing, uses of raw colours where a token exists, and
   unused tokens. The results come back as warnings with node ids.
8. **Cross-document libraries.** A `design-system` document whose components
   and tokens other documents `use`, with an update flow when the library
   changes.
9. **Templates and starter kits.** Device frames (480×320 desk-buddy, phone,
   desktop), and a HUD kit for the persona game.
10. **Layout gaps.**
    - grid layout;
    - constraints on resize (pin left/right/center/scale);
    - min/max sizes;
    - wrap in auto layout;
    - vertical text alignment;
    - justified text;
    - text decoration and case.
11. **Vector editing.** Boolean ops (union, subtract, intersect, exclude),
    pen and path editing in the web inspector, and image fills on paths.
12. **Icon set.** Bundle an open icon library (Lucide or Tabler) as built-in
    components, so agents stop drawing icons by hand.

### P3: nice to have

13. Relay `[[ui:<slug>#node]]` chips that render a live thumbnail, and the
    rapid-chat `ui_designer` sink (T13, second half).
14. Real conic gradients and backdrop blur. This needs a renderer that
    supports them (Skia) or a resvg patch. Today both are approximated.
15. Motion: smart-animate between variants, exported as a short MP4 or GIF
    through the claude-video toolkit.
16. More fonts on request: per-document font upload, OFL only, with
    validation.
17. Multi-cursor presence in the web view (who's looking, who's editing).
18. Per-document access (readers and editors) and a `design` team.
19. A comment @mention that wakes the named agent with the node in context.

## Prompt for Codex

Paste this into Codex in `~/gh/agent-platform`:

```
You are extending UI Designer, the agent-first visual design tool in this repo
(Agent Platform). Read, in order: AGENTS.md; docs/building-blocks/ui-designer.md;
docs/design/46-ui-designer.md (all of it — §2 schema, §5 rendering, §6 API,
§8 Figma import, §9 permissions, §10 versioning/backups, §15 tasks);
docs/superpowers/plans/2026-11-16-ui-designer-codex-wishlist.md (this list).

Code map: services/backend/agentplatform/ui_designer/ (schema.py, ops.py,
layout.py, svg.py, text.py, fonts.py, store.py, views.py, figma_convert.py),
services/backend/agentplatform/api/ui_designer.py, services/ui-renderer/,
services/mcp-broker/broker.py (`ui_designer` tool + UI_DESIGNER_CHEATSHEET
copied from agentplatform/agentspec.py — keep them identical),
services/mcp-facade/facade.py, services/web/src/pages/UiDesigner*.tsx,
skills/ui-designer/SKILL.md.

Goal: take the wishlist P1 items 1–5 from idea to deployed, in order. For each:
1. Write a short design addendum (append a section to design 46: data model,
   API, tool surface, permissions, budgets, tests). Keep the schema closed:
   every new node type or prop gets a validator, a painter path, a layout
   rule, budgets, a cheat-sheet line, and tests.
2. Implement with tests. Backwards compatibility is mandatory: existing
   documents must validate and render byte-identically (golden tests in
   tests/test_ui_designer_render.py and tests/test_ui_designer_effects.py).
3. Run what CI runs (read .github/workflows/): backend pytest for touched
   files then the full suite once, broker and facade tests, SDK regeneration
   with no drift, `npm run build` for web, `helm lint`.
4. Ask for review before merging; deploy per docs (images built with
   docker buildx --platform linux/amd64, imported into k3s, helm upgrade with
   pinned tags in charts/agent-platform/values-memory-pai.yaml).

Rules: never `git add -A` (add files by name; never secrets). Agent-facing
answers stay compact (outline first, renders as images). Every write goes
through ops with expected_version. Security seams to treat carefully:
require_ui_designer_access, run fences, local persona routes, asset/image
references (no external hrefs ever reach the SVG), the Figma token (read only
by the executor tool, never logged). When done with each item, update the
building-block doc and the cheat sheet, and append a "Status" line to the
wishlist file.

Then propose (don't build) designs for P2 items 6–8 in the same file.
```

## Implementation status — Codex, 2026-11-16

- **P1.1 Status:** implemented and pushed in `e00134b`; images
  `ud-import-20261116` deployed to pai (Helm revision 146). Live admin create/status and the
  dispatcher identity → executor bridge verified; the smoke job failed with
  the expected missing Figma connection. Affected deployments are ready. Resumable page,
  image and reference checkpoints; Figma retry delays; administrator API/web;
  typed SDK; REST shared styles; bounded reports; `#design` completion card.
  Existing documents remain unchanged. A real Figma acceptance import still
  needs a configured `figma-token` connection. Reference artifacts are provided;
  automatic side-by-side comparison artifacts are not generated in this chunk.
  Checks: backend full run 3,657 passed / 20 skipped plus its corrected catalog
  expectation passing separately; 378 browser, 465 broker, 39 facade, 91 executor
  tests; 29 fetch tests; render/effects goldens; build, Storybook, tokens,
  SDK regeneration, Helm lint and secret checks.
- **Quota:** 26% weekly Codex used at final export acceptance (74% left). Stop opening
  work at 75%; finish and record the current chunk without consuming the final
  20% reserved for other work.
- **P1.2 Status:** implemented inspector edits, canvas gestures and safe browser
  undo/redo, including CAS conflicts, uncertain-save receipt recovery and
  navigation/SSE races. All 388 browser tests, web build, Storybook, token
  checks and focused lint pass. LAN-origin receipt fix adds an 11th editor
  regression test. Pushed through `96fcd86`; deployed web image
  `ud-editor-fix-20261116` in Helm revision 149. Live browser save/undo/redo
  verified persisted versions 2/3/4; temporary smoke document removed.
- **P1.3 Status:** implemented component sets, exhaustive variant axes, typed
  text/boolean/swap properties, bounded graph validation, atomic metadata ops,
  inspection and SDK support. Independent review plus 314 malformed probes;
  3,701 backend tests passed, with the only three socket-denied checks passing
  in the permitted render/effects rerun (53 passed / 1 platform skip).
  Broker 465, facade 39, renderer service 14 tests; SDK drift, web build and
  Helm lint pass. Pushed `0aa846d`, deployed `ud-components-20261116`
  in Helm revision 150. Live create/edit/inspect/render verified a state change,
  text property and icon swap; temporary smoke document removed.
- **P1.4 Status:** implemented prototype interactions, inspector controls and a
  version-pinned click-through viewer. Backend 3,744 passed / 20 skipped;
  browser 397, renderer 14, broker 465, facade 39 tests pass, plus builds,
  Storybook, tokens, SDK drift and Helm lint. Pushed `f7191db`, deployed
  `ud-prototype-20261116` in Helm revision 151. Live browser verified v1
  remains pinned across a v2 edit, tap/back navigation and delayed v2 playback.
- **P1.5 Status:** implemented SVG/PDF/HTML exports across API/storage,
  renderer/generators and browser controls. Backend 3,763 passed / 20 skipped;
  browser 404, renderer 32, broker 466, facade 39 tests pass, plus web build,
  Storybook, tokens, SDK drift and Helm lint. Attachments/nosniff, byte/pixel
  budgets and literal HTML/CSS handling verified. PDF is rasterized; HTML
  is static and bounded to 200 expanded nodes / 1 MiB. Pushed `64bb712`;
  deployed `ud-export-20261116` in Helm revision 152. Live PNG/SVG/PDF/HTML
  downloads verified MIME, nosniff and attachment delivery on both byte routes.
  PDF opened/rasterized successfully; HTML displayed without external requests.
  Smoke documents/artifacts were removed. Generic font fallbacks were then
  verified in 22 focused export tests and live HTML; renderer
  `ud-export-font-20261116` deployed in Helm revision 153.
- **Final checkpoint:** P1.1–P1.5 are implemented, committed and deployed.
  P2.6–P2.8 remain proposals only, below. All affected deployments are ready.
  Claude’s merged backup fixes are included. The only external acceptance
  prerequisite is configuring `figma-token` for a real Figma source import.
  Native features use UI Designer/neutral naming; Figma names identify only
  the real import/conversion integration. No UI Designer work remains uncommitted.
- **Standing authorization:** Kyle explicitly authorized unattended deployments,
  frequent commits and direct pushes to main after the importer checkpoint.
  Continue chunk by chunk without asking again; quota stop remains 75%.

## P2 proposals (design only)

### 6. Visual diff

Compare two explicit immutable document versions with the same node, mode,
scale and renderer version. The renderer produces a bounded heatmap and changed
bounding boxes from the pixel difference; the server joins those boxes to
resolved node IDs. Return the image plus a compact count/box summary from
`history op=visual_diff`, and add an opacity slider to Compare. Reuse render
cache and pixel budgets; refuse incompatible or oversized renders. Keep glyph
and effect differences visible, with an optional user-selected tolerance rather
than silently ignoring them. Test unchanged renders, moved nodes, text edits,
mode mismatches and transparent edges. No new document properties are needed.

### 7. Design lint

Add a read-only `lint` action with node/mode filters and pagination. Reuse the
existing layout warnings, then add contrast with alpha compositing, clipping,
spacing-grid checks, duplicate raw colours matching a token and unused tokens.
Each finding has a stable code, severity, node ID, mode and a suggested change.
Distinguish definite findings from approximations where images/gradients make
contrast uncertain; do not claim WCAG compliance from a single heuristic.
Keep lint advisory, cache by document hash/rules version, and run it in the
renderer for layout-dependent checks. Test concrete positive and negative
fixtures, with no document mutation or new grants.

### 8. Cross-document libraries

Use explicit pinned library imports: a consumer records a source document ID,
retained version, selected component/token IDs and a local namespace. On import,
copy the resolved definitions and required assets into the consumer so it renders
without a network dependency and backups remain self-contained. A read-only
update preview compares the pin to a new source version; accepting it is one
CAS edit batch with a receipt. Never follow a moving library head implicitly.
Reject dependency cycles, namespace collisions and expansion/asset-budget
violations. Apply both source-read and destination-write checks server-side;
future per-document access must not widen through library reuse. Tests cover
source deletion, offline rendering, safe upgrades, conflicting local overrides,
cyclic components and retained-version restore.

## P2 execution — authorized 2026-11-16

Kyle authorized implementing the P2 work after P1 completion. Scope is the full
P2 wishlist (6–12), in order: visual diff, lint, libraries, templates, layout
improvements, vector editing and icons. Reuse three Sol implementation agents;
finish, verify, commit, push and deploy bounded chunks before starting another.
Standing unattended deployment authorization and the 75%-used quota stop remain.
Fresh starting Codex weekly usage: 26% (74% left).

- **P2.6 Status:** implemented aligned visual comparison, bounded change regions,
  cached renderer results, image-native broker output and the Compare overlay.
  Checks: backend 3,782 passed / 20 skipped; browser 409, renderer 54, broker
  467, facade 40; builds, Storybook, tokens, SDK drift and Helm lint pass.
  The raw image-bearing route is excluded from the text-only facade.
  Pushed `4b4f268`, deployed `ud-diff-20261116` in Helm revision 154.
  Live API and browser verified 2,400 changed pixels after a move, zero for
  identical versions and local-only opacity adjustments; smoke document removed.
  Fresh weekly quota: 29% used. P2.7 implementation is underway.

- **P2.7 Status:** implemented advisory lint in the isolated renderer, pinned
  API/tool pages and Findings panel. Contrast thresholds/alpha/uncertainty,
  overflow/clipping, spacing and token hygiene are covered. Backend 3,790 passed /
  20 skipped; browser 415, renderer 77, broker 468, facade 40 pass, plus web
  build, Storybook, tokens, SDK drift and Helm lint. Pushed `066519d`; deployed
  `ud-lint-20261116` in Helm revision 155. Live API/browser verified findings,
  pagination, a contrast fix in v2 and unchanged v1 findings. Smoke document
  removed. Fresh weekly quota: 31% used. P2.8 implementation is underway.

- **P2.8 Status:** implemented pinned library copies, semantic dependency/ID/token
  remapping, source-bound assets, explicit update previews, local-edit conflicts,
  CAS/receipt recovery and the Libraries panel. Full backend: 3,817 passed /
  21 skipped; the sole help-length failure was repaired and 33 focused guidance/
  broker/catalog checks pass. Browser 422 passed, followed by 8 final focused
  library checks; renderer 77, broker 469, facade 40 pass. PostgreSQL CI checks
  20 passed; the final deterministic GC/copy regression also passed separately.
  Builds, Storybook, tokens, SDK drift and Helm lint pass. Pushed `a0d89d4`,
  deployed `ud-library-20261116` in Helm revision 156. Live import v2 and browser
  preview/apply v4 passed; receipt replay and consumer rendering still worked
  after source deletion. Smoke documents were removed. Fresh weekly quota: 34%.
  P2.9 implementation is underway.

- **P2.9 Status:** implemented four editable starter scenes, catalog discovery,
  version-pinned template creation and the browser creation form. Backend 3,842
  passed / 21 skipped, plus 10 focused API checks including real catalog creation;
  browser 432, broker 470, facade 40 pass. All four scenes validate and render in
  light/dark with no overflow/contrast warnings; 15 focused scene checks pass.
  Previews reviewed. Web/Storybook/token checks, SDK drift and Helm lint pass.
  Pushed `c868f15`, deployed `ud-templates-20261116` in Helm revision 157.
  Live browser creation, all four dimensions, eight light/dark renders and
  receipt replay passed; smoke documents removed. Fresh weekly quota: 35%.
  P2.10a typography is underway; grid/min-max/constraints follow as P2.10b.

- **P2.10a Status:** implemented text justification, vertical alignment, Unicode
  case and decorations with inspector CAS/undo and faithful HTML SVG fallback.
  Legacy SVG/PNG goldens remain unchanged; visual sample reviewed. Backend 3,886
  passed / 21 skipped; browser 434, renderer 81, broker 470, facade 40 pass, plus
  builds, Storybook, tokens, SDK drift and Helm lint. Pushed `38c8045`,
  deployed `ud-type-20261116` in Helm revision 158. Live browser save/undo/redo
  versions 2/3/4 preserved original text and rendered case/alignment/decoration;
  smoke document removed. Fresh weekly quota: 37%. P2.10b is underway.

- **P2.10b Status:** implemented grid layout, numeric min/max limits, row/column
  wrap controls and resize constraints. Resize stores literal forward history and
  exact bounded undo operations, including receipt replay; instance preflight
  shares the renderer's expansion rules. Visual sample reviewed; legacy goldens
  unchanged. Backend 3,933 passed / 21 skipped; browser 438, renderer 81, broker
  470, facade 40 pass. Focused resize/schema/store checks also pass, including
  folded-history replay without rerunning resize math. Builds, Storybook, tokens,
  SDK drift and Helm lint pass. Pushed `96764de`, deployed
  `ud-layout-20261116` in Helm revision 159. Live browser resize/undo/redo, grid
  placement and receipt replay passed at versions 2–6; smoke documents removed.
  Fresh weekly quota: 40%. P2.11 vector editing is next.

- **P2.11 Status:** implemented native union/subtract/intersect/exclude in the
  isolated renderer, a pen/point/cubic editor with raw SVG fallback, and image
  fills clipped to paths. Exact undo, original-input receipts, concurrent CAS,
  strict new path edits and legacy restore compatibility are covered. Visual
  sample reviewed; old SVG/PNG goldens unchanged. Backend full run: 3,955 passed /
  21 skipped, plus the former image-path rejection test updated for this feature;
  final effects/API checks: 42 passed. Browser 446, renderer 105 plus 26 final
  focused geometry checks, broker 470, facade 40 pass. Builds, Storybook, tokens,
  SDK drift and Helm lint pass. Pushed `1dc7fa0`, deployed
  `ud-vector-20261116` in Helm revision 160. Live all-four Boolean areas matched
  exact pixel counts, with exact undo/redo and receipt replay. Browser cubic pen,
  Boolean edit and path image fill passed through versions 2–8; image clipping
  checked and temporary documents/artifacts removed. Fresh weekly quota: 44%.
  P2.12 icons are underway; this is the last P2 item.

- **P2.12 Status:** implemented 2,053 bundled Lucide icons with reproducible
  pinned conversion, packaged licenses, native component insertion/reuse, actual
  path/stroke scaling, catalog discovery and the browser picker. Open contours
  and filled details are preserved; locally edited definitions are protected.
  Canonical catalog regeneration is byte-identical and the visual sample was
  reviewed. Backend 3,990 passed / 21 skipped; browser 451, broker 471, facade 40
  pass. Builds, Storybook, tokens, SDK drift and Helm lint pass. The existing
  renderer is unchanged. Pushed `828460c`, deployed `ud-icons-20261116`
  in Helm revision 161 (renderer remains `ud-vector-20261116`). Live API
  insertion/reuse/exact undo/receipt replay/local-edit protection passed; browser
  48-pixel insertion, undo and redo passed at versions 2/3/4. Native render
  inspected; temporary documents removed. Fresh weekly quota: 45% used / 55% left.

## P2 completion checkpoint — 2026-11-16

P2.6–P2.12 are implemented, locally verified, committed, pushed to main and
deployed to pai. Three Sol agents handled disjoint implementation work; each
chunk reached live acceptance before the next implementation chunk opened.
Visual diff, design lint, pinned libraries, templates, typography/layout, vector
editing and 2,053 native icons are available. All UI Designer work is committed.
The last measured weekly Codex usage is 45%, leaving 55% for other work; the
75% stop threshold was never reached.

Material bounds remain explicit in the feature docs: visual path editing uses
a bounded M/L/C/Z subset with raw-data fallback; Boolean operands require fixed
numeric sibling geometry; icon size is chosen at insertion and scales native
path coordinates. Legacy document renders and restore compatibility are kept.

## P3 execution — authorized 2026-11-17

Kyle requested all remaining P3 items, 13–19, unattended. Figma import work is
out of scope. Continue with the three Sol implementers and complete each chunk
through local verification, commit/push and live acceptance before opening the
next. Fresh weekly quota at start: 47%; stop opening work at 75% and preserve the
final 20%. Sequence: 13 Relay/rapid-chat, 14 rendering effects, 15 motion export,
16 fonts, then 18 document access before 17 presence and 19 comment wake-ups.

P3.13 uses native design proposals: bounded JSON frame subtrees inside rapid-chat
Options/Proposals, composed and saved only by the controller. The sink creates a
new document, pins its UUID/version and uses stable receipts. An external edit
stops sink writes. Rapid turns can read only the pinned sink document with their
existing UI Designer grant; they cannot write or browse unrelated documents.
Relay chips support the requested `[[ui:slug#node]]` syntax and legacy slash node
syntax, with authorized, bounded live thumbnails.

- **P3.13 Status:** implemented live Relay node previews and the native rapid-chat
  sink, including bounded frame validation, UUID/CAS fences, crash receipt
  recovery and post-commit refresh events. Backend 4,006 passed / 21 skipped;
  browser 455, broker 472, facade 40 pass. Web build, Storybook, tokens, SDK
  drift and Helm lint pass. Pushed `fc2c8bb`, deployed `ud-relay-20261117` in Helm revision 162.
  Live Relay preview refreshed v1→v2 through the real API/event stream and
  opened the selected node; the deployed controller passed isolated database
  receipt-recovery and external-edit checks. The test message existed only in
  the browser fixture. Temporary documents were removed.
  Fresh weekly quota: 49% used. No paid agent sprint was started for testing.

- **P3.14 Status:** implemented continuous native conic gradients, composited
  backdrop blur, shared intermediate-work budgets, verified image negotiation
  and friendly inspector controls with exact undo. Unaffected SVG/PNG goldens
  remain unchanged; the visual sample and Linux/amd64 native render passed.
  Backend 4,021 passed / 21 skipped; browser 457, renderer 117, broker 472,
  facade 40 pass. Web build, Storybook, tokens, SDK drift and Helm lint pass.
  Pushed `ffa7067`, deployed `ud-effects-20261117` in Helm revision 163.
  Live nested clipped backdrop and cropped pixels matched (123 grey), conic
  continuity and generated SVG passed, and browser save/undo/redo passed at
  versions 2/3/4. Temporary documents removed. Fresh weekly quota: 51%.

- **P3.15 Status:** implemented shared native smart-animation manifests,
  prototype playback, and pinned Remotion MP4/GIF exports with atomic artifact
  receipts, resource limits and process-group cancellation cleanup.
  Backend 4,028 passed / 21 skipped; final focused cancellation/API/client
  checks passed. Browser 465, renderer 135, broker 473 and facade 40 pass.
  Web build, Storybook, tokens, SDK drift (829 files) and Helm lint pass.
  Linux/amd64 Node 22 acceptance passed: 120-frame MP4 (720²) and GIF
  (480²) with concurrent static renders; peak 759 MiB under a 1 GiB cgroup,
  no OOM events. The test caught and removed an incompatible Node flag.
  Pushed `f59f1a9`, deployed `ud-motion-20261117` in Helm revision 164.
  Live MP4/GIF each decoded 12 frames with exact endpoint positions and
  receipt replay. Browser smart animation interpolated the matched card,
  reached the target frame and downloaded a 19-frame GIF. Fixtures removed.
  Fresh weekly quota: 55% used.

- **P3.16 Status:** implemented isolated static OFL font validation, scoped native
  rendering, atomic uploads and replay receipts, font selection/license UI,
  history retention and library copies with collision checks. Fonts never
  widen another document's family registry; all byte negotiation is bounded
  to declared hashes. HTML preserves uploaded typography as outlined SVG.
  Backend suite: 4,039 passed / 22 skipped; original font and new upload
  coverage was also verified together (35 passed), plus final client checks.
  Browser 471, renderer 150, broker 474 and facade 40 pass. Two actual
  PostgreSQL copy/GC races pass. Web build, Storybook, tokens, SDK drift
  (839 files), Helm lint and final image/source hash checks pass.
  Linux/amd64 accepted the unmodified Atkinson Hyperlegible OFL font and
  rendered native PNG and outlined HTML. Pushed `188fb4d`, deployed
  `ud-fonts-20261117` in Helm revision 165. Live upload/replay/history, native
  SVG and library pixels after source hard deletion passed. Browser license
  display and save/undo/redo pixels passed at versions 3/4/5. Fixtures removed.
  Fresh weekly quota: 58% used.

- **P3.18 Status:** implemented per-document readers/editors/team sharing, separate
  sharing CAS/receipts, server-derived capabilities and a seeded Design team.
  Document, render, asset, library and SSE access is checked against current
  permissions. Source UUID provenance follows exports and derivatives through
  artifact routes, lists and feeds; private designs do not post public cards.
  SQLite and real PostgreSQL upgrade/idempotency checks pass. Browser 482,
  broker 475, facade 40 and renderer 150 checks pass; SDK regeneration is stable.
  Full backend run: 4,095 passed / 23 skipped; three startup/seed fixture
  assumptions were corrected and all 92 affected checks passed. Pushed `17745f9`;
  deployed `ud-access-20261117` in Helm revision 166 (renderer remains fonts).
  Live temporary accounts verified role/ACL intersections, sharing replay,
  history restore, private exports without public cards, derivative revocation
  and old-export isolation after slug reuse. Browser owner sharing controls
  triggered real SSE revocation that cleared a reader's canvas; only the
  cookie-only authentication bootstrap was mocked for the temporary API keys.
  All temporary documents, artifacts and keys were cleaned up. Codex quota: 63%.

- **P3.17 Status:** implemented multi-cursor presence with viewing/editing labels,
  page/version-aware world coordinates, TTL expiry, own-tab leave and current
  permission checks. Shared SQL leases enforce a 32-session cap and per-actor
  cooldown across API replicas; scoped Kafka/SSE avoids global thumbnail churn.
  Backend 4,117 passed / 23 skipped; browser 490, broker 475, facade 40 and
  renderer 150 passed, plus build, Storybook, tokens, stable SDK and Helm lint.
  Twelve focused service checks and an actual two-engine PostgreSQL race test
  prove capacity, cooldown and revocation ordering. Deploy/live acceptance next.
