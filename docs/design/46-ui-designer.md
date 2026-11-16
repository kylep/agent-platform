# 46 — UI Designer

Status: ready to build, revision 3 after three review passes (2026-11-16). Author: Claude (design
partner, Kyle's session). Plan only; nothing implemented yet.

## Problem

Kyle wants a visual design tool the platform owns, so the personas can design
a game's spectator UI without Figma's free-tier walls (three files, no Dev
Mode, no variable modes, 30-day history, and on the Starter plan no MCP
allowance at all for a Full seat — see Appendix A). His words: it "should let
me as a human visually review things and interact, but its primary user
should be agents, especially codex and claude based ones. It should be easy
for them to control, it should serve them images they can use their image
parsing capabilities to look at, it should be performant. When I do a backup
of agent platform state, its content should be backed up too."

First users: Pai (Claude Opus), Olu (Claude Fable) and Kai (Codex) designing
the game's spectator UI. Acceptance test after the build: migrate Kyle's
existing "desk-buddy" Figma designs into it through a legitimate route (the
Figma REST API with Kyle's token, or files he exported).

## Decision in one paragraph

Build our own, small. A **UI document** is a JSON tree of frames, text,
shapes, images, components and tokens, stored in platform Postgres with a
full-snapshot version per write. Agents edit it with **batched structural
ops** through one broker tool, `ui_designer`, and every edit can answer with
**PNG snapshots inline** (MCP image content blocks, the way `artifacts get`
already shows a picture) — with a fallback that needs no image block at all.
The server owns the single **layout engine** (constrained layout, auto
layout, hug/fill/fixed, tokens, modes) and the single **painter**: it
converts text to glyph outlines, writes SVG and rasterizes in a **renderer
subprocess** with **resvg** (a Rust renderer with a Python wheel, no
Chromium) and bundled fonts, so output is deterministic. The web UI at
`/ui-designer` shows the server's SVG or PNG with an interaction overlay
built from the resolved layout, for pan, zoom, select, comment, light edits
and version compare. Figma import is an **internal executor tool** that
calls Figma's REST API with a platform secret and converts the node tree.
Everything lives in Postgres and the existing backups are whole-database
`pg_dump`s, so documents, versions, comments and image assets are covered
once the render cache is excluded from the dump and version growth is
budgeted (§10). Penpot, tldraw and Excalidraw were considered and rejected
(§11).

## Goals / non-goals

Goals
- G1 Agent-first control: a document an agent can read as structured JSON
  and change with a handful of coarse, batched, validated operations;
  never "move node to x=413".
- G2 Agents see what they made: server-rendered PNGs of frames or regions,
  sized for the model (≤ 1,568 px long edge by default), returned inline in
  the same tool result as the edit, with a non-image fallback.
- G3 Human review and light interaction: list, view, pan/zoom, select a
  node and read its properties, comment on a node, edit text/props/position,
  compare two versions, restore.
- G4 Performant on the NUC: one layout pass is milliseconds for the sizes a
  spectator UI needs; a 1080p render is tens of milliseconds in a process
  that cannot stall the API; renders are cached by content hash; no headless
  browser anywhere.
- G5 Everything in platform Postgres so `ap-cloud-backup` and `ap-pg-backup`
  cover it, with the derivable cache excluded and growth bounded.
- G6 Fences and grants follow the existing rungs: `ui_designer` is a
  participant-rung tool, dreams and rapid-chat turns stay fenced out.
- G7 Figma import of Kyle's desk-buddy files through the REST API, with a
  fidelity report that counts approximations as well as drops.

Non-goals (named so they stay out)
- Figma parity: no vector pen tool, boolean operations editing, prototyping
  animations, multiplayer cursors, plugins, FigJam, Sites or Make.
- Design-to-code generation in v1 (an `inspect` action gives resolved
  geometry and tokens; HTML/CSS export is §12 deferred).
- Per-document ACLs in v1: a document belongs to the platform like a wiki
  page; attribution and restore are the guard.
- Reading `.fig` binaries: undocumented format, unofficial parsers only.

## 1. Shape

Eight tables, one module `services/backend/agentplatform/ui_designer/`
(schema, ops, store, import), one router `api/ui_designer.py`, one broker
tool `ui_designer` in `services/mcp-broker/broker.py`, one **renderer
service** of its own (`services/ui-renderer/`, Phase 0) that does layout,
painting and rasterizing over HTTP so the API stays I/O-bound, one Kafka
topic `ui_designer.events` (`events.py`, Phase 1), one internal executor
tool `tools/figma_import/`, and two web pages
(`services/web/src/pages/UiDesigner.tsx`, `UiDesignerDocument.tsx`, routes
`/ui-designer`, `/ui-designer/:id`).

```
agent ──MCP──▶ broker `ui_designer` ──▶ /api/ui-designer/*  ──▶ store (Postgres)
Kyle / Codex ──MCP──▶ facade (OpenAPI, text only)   │
                                                    ▼  HTTP {doc, mode, nodes, scale}
                                 ui-renderer: expand → layout → SVG → resvg → PNG / SVG / resolved tree
                                                    │
web /ui-designer ◀── server SVG/PNG + resolved layout overlay + SSE (Phase 1)
```

Images deployed: a new `agent-platform-ui-renderer` image (`python:3.12-
slim` + `resvg_py`, `uharfbuzz`, `fonttools`, `/app/fonts`, the
`ui_designer/layout.py`, `text.py`, `svg.py` modules); the backend (api +
dispatcher) gains only the schema/ops/store code; the **mcp-broker image**
gains the tool (its own image and deploy); the executor image is unchanged
(the Figma tool is stdlib `urllib`).

## 2. Data model

### 2.1 Tables (`db.py`, additive, `create_all`)

`ui_designer_documents`
- `id` hex32 pk, `slug` (`^[a-z0-9][a-z0-9-]{0,63}$`; a **partial unique
  index** `WHERE deleted_at IS NULL`, so a deleted slug can be reused and
  the tombstone keeps its name), `title` (120), `description` (Text),
  `owner` (participant string), `version` int, `doc` JSONB (the current
  document), `doc_sha256`, `node_count`, `expanded_count`, `bytes`,
  `created_at`, `updated_at`, `deleted_at` (soft delete; the dispatcher
  hard-deletes after `ui_designer_prune_days` = 30, a pruner beside the
  `ArtifactPruner` in `pruning.py`).

`ui_designer_versions` (the history)
- `id` pk, `document_id` (fk, index), `version` int (unique per document;
  the number of the **last** write the row holds), `doc` JSONB (full
  snapshot), `chunks` JSONB (`[{"version": n, "ops": [...], "reason": …,
  "at": …}]` — one chunk per accepted write folded into the row, so every
  version number is resolvable, §10), `first_version` int (the first chunk's
  number; `first_version == version` for an unfolded row), `author` (128),
  `run_id` (nullable), `label` (64, nullable; a named version),
  `created_at`, `last_write_at`. A lookup by a folded number finds the row
  with `first_version ≤ n ≤ version`.

`ui_designer_imports` (Figma import jobs, §8)
- `id` pk, `slug` (unique while `state ∈ {queued, running}` — the
  idempotency key), `file_key`, `state` (`queued|running|done|failed`),
  `pages` JSONB (per page: `pending|done|failed`, counts), `staged` JSONB
  (per completed page: its converted subtree, asset sha256s and
  warnings — the durable checkpoint a restart resumes from), `report`
  JSONB, `error`, `initiated_by`, `created_at`, `updated_at`.

`ui_designer_assets` (image bytes the documents own, content-addressed)
- `sha256` pk, `data` LargeBinary, `mime`, `width`, `height`, `bytes`,
  `created_at`. ≤ 8 MiB per asset (the artifacts bound), ≤ 16 MiB of
  assets referenced by one document version.

`ui_designer_document_assets` (the placement record)
- `document_id`, `sha256` (pk together), `placed_by` (principal),
  `placed_from` (the artifact id, or `import:<job id>`), `placed_at`.
  Written by `place` and by import; an `image` node may name only a
  sha256 this table holds for its document (§9), and GC keeps an asset
  while any row here or any retained version names it.

`ui_designer_comments`
- `id` pk, `document_id` (index), `node_id` (nullable), `x`, `y` (document
  coordinates of the pin, nullable), `parent_id` (thread), `author`,
  `run_id`, `body` (Text, ≤ 4 KiB), `resolved_by`, `resolved_at`,
  `created_at`.

`ui_designer_receipts` (request replay, modelled on `AppDataBuildOp`,
`appdata/models.py:182`)
- `id` pk, `scope` (160: the `run_id` for an agent, the session id for a
  human), `request_id` (128), `document_id`, `op`
  (`edit|create|restore|comment`), `args_hash` (64), `response` JSONB
  (**the final version number, warnings and text only — never a
  snapshot**), `created_at`; `UniqueConstraint(scope, request_id)`. A
  repeat with the same hash returns `response` with `replayed: true` and,
  if the original asked for snapshots, the line "replayed: call snapshot
  for the pictures"; a different hash is `UD-REQUEST-REUSED`. Rows older
  than 7 days are pruned.

`ui_designer_renders` (cache, derivable, bounded, **not backed up**)
- `key` pk = sha256(renderer_version · doc_sha256 · node_id · mode · scale ·
  annotate), where `renderer_version` = the painter's code version plus the
  sha256 of the font set, so a font or painter change invalidates every
  row; `document_id`, `png` LargeBinary, `width`, `height`, `bytes`,
  `created_at`, `last_used`. LRU-evicted above
  `ui_designer_render_cache_mb` (256). Both dump jobs exclude its rows:
  `--exclude-table-data=public.ui_designer_renders` added to the `pg_dump`
  lines in `charts/agent-platform/templates/backup-cloud.yaml:71` and
  `pg-backup.yaml:66` (the table definition still dumps, so a restore has
  an empty cache that refills).

### 2.2 The document (`ui_designer/schema.py`)

Closed JSON Schema, versioned (`"schema": 1`), unknown keys refused with a
JSON path (the app-data posture, `appdata/definitions.py`). Written to be
read by a model: short keys, human ids, tokens by reference.

```json
{
  "schema": 1,
  "title": "Spectator UI",
  "tokens": {
    "color": {"bg": {"dark": "#0b0f14", "light": "#ffffff"}, "fg": "#e6edf3", "accent": "#5ad1ff"},
    "space": {"xs": 4, "s": 8, "m": 16, "l": 32},
    "radius": {"card": 12},
    "type": {"body": {"family": "Inter", "size": 14, "weight": 400, "line": 1.4},
             "hud":  {"family": "JetBrains Mono", "size": 20, "weight": 600}}
  },
  "modes": ["dark", "light"],
  "components": {
    "score-pill": {"type": "frame", "layout": {"dir": "row", "gap": "$space.s", "pad": [4, 12]},
                   "fill": "$color.accent", "radius": 999,
                   "children": [{"id": "label", "type": "text", "text": "0", "style": "$type.hud", "color": "$color.bg"}]}
  },
  "pages": [
    {"id": "spectator", "title": "Spectator", "children": [
      {"id": "hud", "type": "frame", "title": "HUD 1080p", "w": 1920, "h": 1080, "fill": "$color.bg",
       "layout": {"dir": "column", "pad": "$space.l", "gap": "$space.m", "align": "stretch"},
       "children": [
         {"id": "topbar", "type": "frame", "h": 64, "layout": {"dir": "row", "justify": "space-between", "align": "center"},
          "children": [
            {"id": "match-title", "type": "text", "text": "Match 12 — Red vs Blue", "style": "$type.body", "color": "$color.fg", "w": "hug"},
            {"id": "red-score", "type": "instance", "of": "score-pill", "overrides": {"label.text": "7"}},
            {"id": "blue-score", "type": "instance", "of": "score-pill", "overrides": {"label.text": "5", "fill": "#ff5a5a"}}
          ]},
         {"id": "stage", "type": "frame", "w": "fill", "h": "fill", "fill": "#000000", "radius": "$radius.card"}
       ]}
    ]}
  ]
}
```

Node vocabulary (Phase 0, sized by the desk-buddy census, T0b): `frame`
(the only container; `layout` present means auto layout, absent means
children carry `x`,`y`), `text`, `rect`, `ellipse`, `line`, `path` (SVG
`d` string, for imported vectors and icons), `image` (`asset`: an asset
sha256, `fit`: `cover|contain|fill`), `instance` (`of`: a component id,
`overrides`: `{"<child-id>.<prop>": value}`). Shared props: `id` (unique
in the document, `^[a-z0-9][a-z0-9-]{0,47}$`, server-generated from
`title` when omitted), `title`, `w`/`h` (number | `"fill"` | `"hug"`),
`x`/`y` (under a frame with no layout, or with `absolute: true` under a
laid-out frame — Figma's "absolute position" children, which the HUD
overlays need), `rotate` (degrees about the node's centre), `fill`
(a paint or a list of up to 8 stacked bottom to top; a paint is a colour,
`{"color", "opacity"?}`, `{"gradient": "linear"|"radial"|"angular"|"diamond",
"stops": [[0, "#…"], [1, "#…"]], "angle"?: 90, "opacity"?}` or `{"image": sha256,
"fit"?, "opacity"?}`; `angular`/`diamond` are stepped approximations, since
resvg has no conic gradient), `stroke` `{color, width, align, dash?, cap?,
join?, sides?}` (`sides` = per-side widths on frames and rects), `radius`
(number | 4-tuple), `opacity`, `visible`, `clip`, `mask`
(`alpha|vector|luminance`: a frame's first child masks its siblings —
`alpha` by the child's alpha, `vector` by its fill and stroke regions
regardless of paint alpha, `luminance` by its luminance; Figma's three
`maskType`s), `shadow`
(one or a list of `{x, y, blur ≤ 64, spread, color, inset}`: drop shadows
under the node, inner shadows over it), `blur` (layer blur ≤ 64),
`backdrop_blur` (background blur: the painter re-paints what is already behind
the node, blurs it and clips it to the node's shape; resvg has no
BackgroundImage, so this is an approximation), `blend` (Figma's blend modes as
CSS `mix-blend-mode` names; `plus-lighter` paints as `screen`), `meta` (free JSON ≤ 2 KiB, where the Figma
importer keeps `figma.id`). `layout`: `dir` (`row|column`), `gap`, `pad`
(number | [v,h] | [t,r,b,l]), `align` (`start|center|end|stretch`),
`justify` (`start|center|end|space-between`), `wrap` (bool). `text`:
`text` (a string, or a list of **runs** `[{"text": "…", "style"?: …,
"color"?: …}]` for mixed styling), `style` (a `$type` token or inline
`{family, size, weight, line, letter}`), `color`, `align`
(`left|center|right`), `max_lines`. A `$path` anywhere a value goes
resolves against `tokens`; a token whose value is an object keyed by mode
resolves by the render's mode (the variable-modes feature Figma gates
behind Professional).

Limits, checked at validation: document ≤ 1 MiB, ≤ 5,000 authored nodes,
≤ 50 pages, ≤ 200 components, instance nesting depth ≤ 6 and **≤ 20,000
expanded nodes** after instance expansion (`UD-EXPANSION`; the validator
expands with a counter and stops at the cap, so a component that contains
itself or a 10×10×10 nesting is refused before layout), text ≤ 4 KiB,
`path.d` ≤ 32 KiB per node and ≤ 1 MiB per document, ≤ 32 shadows, ≤ 32
blurred nodes and ≤ 8 backdrop blurs per document, ≤ 8 paints per fill, blur
and spread ≤ 64 px, gradients ≤ 8 stops, font families from the
bundled allowlist only (§5), colours `#rgb|#rrggbb|#rrggbbaa`, numbers
finite and within ±100,000. Everything an agent writes is data: the
painter XML-escapes, the web shows the server's own output, and nothing in
the document is ever interpreted as markup, a URL or a script.

### 2.3 Ops (`ui_designer/ops.py`)

One `edit` call is one batch; the batch applies atomically or not at all
and produces exactly one version number (its row may be folded with the
same run's previous write, §10). The vocabulary is deliberately small:

| op | fields | notes |
|---|---|---|
| `insert` | `parent`, `node`, `index?` | `node` is a subtree; ids generated when missing |
| `update` | `id`, `set` (object) | shallow merge on that node's props; `null` unsets; `children` not allowed here |
| `remove` | `ids` | subtree; an instance's component stays |
| `move` | `id`, `parent`, `index?` | reparent/reorder; cycles refused |
| `replace` | `id`, `node` | swap a subtree (keeps the id when `node` omits one) |
| `set_text` | `id`, `text` | the common case, spelled out so a model never has to nest `set` |
| `tokens` | `set` (object) | deep merge into `tokens`; removing a token still in use is refused with the users listed |
| `define_component` | `id`, `from` | lifts a node into `components[id]` and replaces the original with an instance |
| `set_doc` | `doc` | whole replacement (import, bulk restructure) |

Every batch is validated as a whole document after application — schema,
limits, expansion budget, token references, and **image assets** (every
`image` node, however it arrived: `insert`, `replace`, `set_doc`,
`restore`, import) — and returns every error at once (`UD-INVALID` with
`{path, code, message, fix}`). Writes carry `expected_version` (CAS;
`UD-VERSION-CONFLICT` answers with the current version and the ops applied
since the expected one, so an agent can rebase without re-reading) and a
`request_id` (§2.1 receipts). The response is the new version, the
warnings (§3.3), and, when asked, the snapshots.

## 3. The agent tool: `ui_designer`

`mcp__platform__ui_designer`, a core broker tool beside `wiki` and
`artifacts` (`broker.py`, `@mcp.tool @_metered("ui_designer",
grant=True)`), forwarding the caller's own token to `/api/ui-designer/*`.
One `action` argument. Phase 0 ships `outline`, `edit`, `snapshot`, the
minimum around them (`list`, `create`, `get`) and the recovery pair
(`history` list, `restore`); `inspect`, `comments`, `history diff` and
`export` are Phase 1.

| action | arguments | returns |
|---|---|---|
| `list` | `q?`, `limit?` | slug · title · version · owner · updated, one line each |
| `create` | `slug`, `title`, `doc?`, `reason`, `request_id` | the document line |
| `get` | `slug`, `node?`, `depth?` (default 3), `after?`, `mode?`, `resolve?`, `version?` | the JSON of the document or a subtree at the current or a named version; cut by depth and by a **sibling cursor**: `… {n} more children after <id>; get node=<parent> after=<id>` |
| `outline` | `slug`, `page?`, `node?`, `after?` | one indented line per node: `id type title w×h [layout] [warnings]` — the cheap map an agent reads before editing; same cursor when a page outline exceeds the text cap |
| `edit` | `slug`, `ops[]`, `reason`, `expected_version`, `request_id`, `snapshot?: [node ids]`, `mode?` | the new version, warnings, and one PNG per snapshot id (≤ 4) |
| `snapshot` | `slug`, `nodes[]` (≤ 4; a page id renders the page), `mode?`, `scale?`, `annotate?`, `version?`, `as?: image\|boxes` | PNGs inline plus a text block of each node's resolved box (`id x y w h`) |
| `inspect` | `slug`, `node` | resolved geometry of the node and children, resolved colours/typography per mode, tokens used, text, the component it instances |
| `comments` | `slug`, `op: list\|add\|resolve`, … | the thread; agents and Kyle share one table |
| `history` | `slug`, `limit?`, `before?`; `op: diff` (`from`, `to`, Phase 1) | retained rows with the version numbers each holds, author, reason, label; a diff is the chunks between two numbers (§10) |
| `restore` | `slug`, `version`, `reason`, `request_id` | a new version whose snapshot is the named one (any number, folded or not) |
| `place` | `slug`, `artifact_id` | the asset `sha256` to use in an `image` node, after the §9 checks; Phase 1 with images |
| `export` | `slug`, `node`, `scale?`, `mode?`, `name?` | a PNG artifact (`[[artifact:<id>]]` card works everywhere), a card in `#design` |

Two properties make this usable by a model. First, **edit + see is one
round trip**: `edit … snapshot=["hud"]` returns the picture of the result,
so the loop is read outline → batch of ops → look → next batch. Second,
the tool talks in ids and tokens and the server turns that into pixels;
an agent that wants `x=413` can have it only inside a frame with no layout
or on an `absolute` child, and `outline` flags the rest.

### 3.1 Images for models

**T0 result (2026-11-16):** both runtimes see an `ImageContent` block
returned by a platform tool. In real in-cluster runs, Kai (codex,
gpt-6-astra, run 62db359e) and Pai (claude-code, claude-opus-5-5, run
6529f273) were each asked to `artifacts get` the paperclip-robot artifact
and describe the pixels while ignoring the metadata. Both reported details
that appear only in the image: inverted-U eyes, the right arm waving, the
left arm hanging, round joints, a black background. The image path needs
no fallback for Phase 0.

Snapshots are MCP **image content blocks** in the tool result
(`ImageContent`, as `_with_picture` builds for `artifacts get`,
`broker.py:2246`). That helper attaches exactly one image; the tool uses a
new `_with_pictures(lines, images: list[tuple[bytes, str]])` that emits one
`TextContent` followed by up to four `ImageContent` blocks, each preceded
by a one-line caption (`snapshot hud v12 dark 1456×819`).

Sizing: the node's box at `scale` 1 with the long edge capped at 1,568 px
(Claude's standard tier downsizes above that and charges by the downsized
size: a 1920×1080 frame becomes 1456×819 ≈ 1,590 tokens; models on the
high-resolution tier allow 2,576 px; Appendix A). `scale=2` on a region
(`nodes=["topbar"]`) is how an agent inspects a detail without paying for
the whole canvas. `annotate=true` draws each direct child's id and bounding
box in a contrasting outline, the only reliable way for a model to connect
what it sees with what it can name.

Byte bound: `ARTIFACT_VIEW_MAX` (1 MiB, `broker.py:2085`) per image. Over
it the renderer downscales to the next tier (long edge 1,280, then 1,024),
then re-encodes as WebP quality 85, then JPEG 85; still over → `UD-IMAGE-
TOO-LARGE` with the box so the agent can snapshot a region. A flat UI
frame at 1,456 px is ~100–300 KiB as PNG, so the fallbacks are for
photographic images.

**Timeouts.** The broker's `_API_TIMEOUT` is 20 s (`broker.py:68`), so
`ui_designer` carries its own, the way `image_gen` does
(`broker.py:2087`): `_UI_DESIGNER_TIMEOUT` = 45 s. Inside that, the API
commits the version first and then renders under an internal snapshot
deadline (`ui_designer.snapshot_deadline_s` = 15); past it the receipt
still carries the committed version and warnings plus "snapshot timed out;
call snapshot". The skill tells agents to retry a failed `edit` with the
**same `request_id`** — the receipt replays, nothing applies twice.

**Fallback without image blocks**: `snapshot as=boxes` returns only the
text block (resolved boxes, text content, fills per node, and the lint
warnings) — enough to check layout numerically — and `export` writes the
PNG as an artifact the agent can show in Relay or read with
`artifacts get`. Kai's and Pai's ability to see image blocks is proven by
the **T0 spike** before anything else is built.

Text bound: a core broker tool has no server-side output cap today (the
256 KiB `OUTPUT_CAP` is the executor's, `executor.py:61`, and
`ARTIFACT_SAVE_MAX` is an input bound). Claude Code limits an MCP result
to 25,000 tokens by default (`MAX_MCP_OUTPUT_TOKENS`, warning at 10,000,
Appendix A) and Codex's `tool_output_token_limit` is reported at 10,000
for Codex models. So the API caps every text answer at **32 KiB (~8k
tokens)** and cuts with a pointer (`… get node=<id>` / `outline page=`),
sized to stay under Codex's default; the broker's `_metered` audit row
records the response size.

### 3.2 Budgets (Helm values, `ui_designer.*`)

`_metered` gives the per-(agent, tool) token bucket and the audit row.
Server-side, per agent per rolling hour: 120 writes (`UD-BUDGET-WRITES`,
429 with `retry_after`) and a pixel budget **derived** from it —
`writes × 4 images × 1.25 MP` = 600 rendered megapixels
(`UD-BUDGET-PIXELS`), so a run that snapshots on every edit never trips
the second meter before the first, and only `snapshot`-only loops do; per
request at most 4 images and 10 megapixels of output; scale ∈ {0.5, 1, 2,
3}. Humans are not metered. All of these, the cache size and the renderer
limits (§5) are chart values with these defaults.

### 3.3 Warnings (lint, not errors)

Returned with `edit` and shown in `outline`: text overflows its box; a
non-`absolute` child of a laid-out frame carries `x`/`y`; a `fill` child in
a `hug` parent (resolved as `hug`); contrast below 3:1 between a text
colour and its nearest filled ancestor (per mode); a token used in one
mode but missing in another; a component instanced nowhere.

## 4. Layout engine (`ui_designer/layout.py`)

Instances are expanded first (component subtree deep-copied, overrides
applied by `<child-id>.<prop>`, ids prefixed `<instance-id>/`, the §2.2
budget enforced). Tokens resolve by the requested mode.

Layout is **constrained layout**, the Flutter/Yoga discipline: constraints
go down, sizes come up, and a node is measured **at the width it is
given**. `layout(node, BoxConstraints{min_w, max_w, min_h, max_h}) →
Size`, recursive: a laid-out frame resolves its padding and gap, hands
each child the constraints its sizing implies (`fill` → the remaining
main-axis space after fixed and `hug` children are measured, `hug` →
unbounded on the main axis and the parent's cross constraint, a number →
tight), measures `hug` children first, then `fill` children with what is
left, places them with `align`/`justify`, wraps when `wrap` is set, and
returns its own size; a free-position frame measures children with its
own box as the loose constraint. Text is measured by a callback: given a
max width it shapes with **uharfbuzz** against the bundled font files,
breaks lines greedily, applies `max_lines` with an ellipsis, and returns
the ink size plus the broken lines. Because measurement takes the
constraint, "fill-width wrapped text inside a hug parent" resolves: the
parent asks the text for its height at the width the parent can give,
and intrinsic (`hug`) text is measured unconstrained. Two passes at most
for a `fill` child inside a `hug` parent on the same axis (the circular
case): the parent measures once without that child, then gives it the
result; the warning in §3.3 says so. Rotation is applied after layout
(the rotated bounds are reported in `inspect`; they do not feed layout,
as in Figma).

Taffy through its Python binding `stretchable` (1.1.8, 2025-10-29; 46
stars; no documented measure callback) was checked and not adopted: the
measure function is the whole problem, and the subset we need is a few
hundred lines we can test. Yoga has no maintained Python binding.

Output is the **resolved tree**: every expanded node with `x, y, w, h` in
document coordinates, its resolved style, its rotation, and for text its
lines with per-glyph positions. Expansion, layout and painting are CPU
work and all of it runs in the renderer service (§5), which caches the
resolved tree per `(doc_sha256, mode)` in process (LRU 64 entries), so a
snapshot after an edit does one layout, one paint and one rasterize. T2
carries a benchmark gate: a 5,000-authored / 20,000-expanded-node fixture
must lay out in under 250 ms on the NUC's CPU class, or the limits in
§2.2 come down.

## 5. Rendering (`ui_designer/svg.py`, `ui_designer/render.py`)

**One painter.** The server converts every text run to glyph outlines
(uharfbuzz for shaping, `fontTools` for the outlines of the bundled fonts)
and emits `<path>` elements; the SVG contains no `<text>`. That is what
makes the web and the PNG agree: the browser paints the same outlines
resvg does, there is no second text engine, and no font needs to be served
to the browser. The painter walks the resolved tree and writes SVG: `rect`
with `rx`, `ellipse`, `line`, `path d`, glyph paths, `<linearGradient>`/
`<radialGradient>`, `clip` as `<clipPath>`, `mask` as `<mask>`, `rotate`
as a `transform`, `shadow` as `feDropShadow`, an `image` node as `<image
href="assets/<sha256>.png">` — **no inlined data URIs**: the renderer
keeps assets in a file-backed resources directory (`/var/cache/ui-
renderer/assets`, fetched once from `GET /api/ui-designer/assets/{sha}`
and pre-downscaled to ≤ 4 megapixels, rasters only through Pillow's
50-megapixel guard) and passes it as resvg's `resources_dir`; an asset
used by several nodes is one `<image>` in `<defs>` and `<use>` elsewhere.
The `annotate` overlay is drawn in the same pass.

**A renderer service, not a pool in the API.** `resvg_py.svg_to_bytes`
holds the GIL, and the API pod's limit is 1 Gi with a ~565 Mi baseline
(`values.yaml:108-116`): two rasterizing workers inside it would OOM-kill
the API. So `services/ui-renderer/` is its own small Deployment
(`agent-platform-ui-renderer`, FastAPI, requests 256 Mi / 200 m, limit
1 Gi / 2 CPU, `ui_renderer.workers` = 2 uvicorn workers, each a separate
process that imports `resvg_py` and the fonts once) doing **expand →
layout → paint → rasterize** for one HTTP call: `POST /render {doc, mode,
nodes, scale, annotate}` → multipart of PNGs plus the resolved boxes,
`POST /svg`, `POST /resolved`. The API stays I/O only: it validates,
stores, and forwards. A worker runs each job under `RLIMIT_DATA`
(`ui_renderer.job_memory_mb` = 512) and a wall clock
(`ui_renderer.job_timeout_s` = 10); over either, the worker exits and
uvicorn respawns it, and the API answers `UD-RENDER-FAILED`. The pod's
cgroup limit is the outer bound. Work caps are checked by the API before
the call: output ≤ 10 megapixels, blur ≤ 64 px, `path.d` ≤ 1 MiB per
document, ≤ 32 shadow filters, gradients ≤ 8 stops, the §2.2 expansion
budget. NetworkPolicy admits only the API; the service holds no
credentials and reaches only the API's asset route (SPIRE: the same
ghostunnel pattern as api → executor). The resize/re-encode ladder (§3.1)
runs in the renderer too.

`resvg_py.svg_to_bytes(svg_string=…, width=…, height=…,
resources_dir=…, skip_system_fonts=True, font_dirs=["/app/fonts"],
background=…)`; fonts are only needed for an SVG that still had `<text>`,
which ours never do, but the flags stay so a painter bug cannot fall back
to a system font.
resvg is a complete static-SVG renderer in Rust with manylinux wheels
(`resvg_py` 0.5.0, 2026-08-24, Python ≥ 3.10) and no system dependencies;
it is not a browser, so there is no JavaScript, no network, no CSS cascade.

Fonts bundled (SIL OFL): Inter, JetBrains Mono, Noto Sans; the allowlist is
`ui_designer/fonts.py`, and T2 ships a **font-parity test** that shapes a
fixed string, renders it through resvg, and compares the ink bounds with
the measured width (tolerance 1 px). Adding a family is a reviewed change
(a font file and one line) that bumps `renderer_version`.

Why not Chromium: the API image is `python:3.12-slim` and must stay small;
Chromium adds ~400 MB and hundreds of MB of RAM per render process on a
NUC that already runs k3s, Postgres, Kafka and run pods; its output is not
deterministic across versions; and it would mean HTML was the document
format (§11). Why not Skia (`skia-python` 144.1): capable, but needs
fontconfig/EGL on Linux and is a much bigger wheel for the same output.

Routes that serve renders: `GET /api/ui-designer/docs/{slug}/render?node=
&mode=&scale=&annotate=&version=` (PNG, `image/png`, `nosniff`; with an
explicit `version` the response is `Cache-Control: private, max-age=
31536000, immutable`; without one it carries an `ETag` of the cache key
and `no-cache`, so the browser revalidates) and `GET …/svg?node=&mode=
&version=` (the server's own SVG, `image/svg+xml`, same caching rules — the
only SVG the web ever shows is this one, generated from validated JSON and
containing paths and `<image href="/api/ui-designer/assets/<sha>">`
references only — the browser fetches assets through the session-
authenticated asset route, never a data URI; agent-written strings appear
only inside XML-escaped attributes and `<title>`). `GET …/resolved` gives
the resolved tree as JSON for the overlay. `export` is PNG only in v1:
the artifacts store sniffs SVG to `application/octet-stream` and serves it
as an attachment, so an SVG export would need a deliberate artifact type
first (§12).

## 6. API and facade (`api/ui_designer.py`)

Every route depends on `require_ui_designer_access(*roles)`, modelled on
`require_wiki_access` (`api/wiki.py:62`): humans by role, an agent only
when its frozen grants hold `ui_designer` (reads included — the messenger
grant must not open the designer), disabled and quarantined agents
refused, a run-less agent key refused on writes. The routes are added to
`local_persona.py` `ROUTES` (`local_persona.py:37`) with grant
`ui_designer`, so a local persona session reaches them under the same
rule. Schemas in `api/schemas.py`, SDK regenerated (`sdk/regenerate.py`).

| route | what |
|---|---|
| `GET /api/ui-designer/docs` | list (`q`, `limit`, `before`) |
| `POST /api/ui-designer/docs` | create |
| `GET /api/ui-designer/docs/{slug}` | metadata + `doc` (`node`, `depth`, `resolve`, `mode`, `version`) |
| `GET /api/ui-designer/docs/{slug}/outline` | the outline text |
| `POST /api/ui-designer/docs/{slug}/edit` | the batch (+ `snapshot` → PNGs base64 in the receipt) |
| `GET /api/ui-designer/docs/{slug}/render`, `…/svg`, `…/resolved` | §5 (forwarded to the renderer) |
| `GET /api/ui-designer/assets/{sha256}` | asset bytes (session, admin, a granted agent run, or the renderer's workload identity); `nosniff`, inline rasters only, `immutable` (content-addressed) |
| `POST /api/ui-designer/docs/{slug}/place` | `{artifact_id}` → `{sha256}`: the copy-on-place step (§9), separate from `edit` so the check has the `Request` |
| `GET /api/ui-designer/docs/{slug}/inspect?node=` | the inspect answer (Phase 1) |
| `GET/POST …/comments`, `POST …/comments/{id}/resolve` | comments (Phase 1) |
| `GET …/versions`, `GET …/versions/{v}`, `POST …/restore` | history (Phase 0); `GET …/diff?from=&to=` (Phase 1) |
| `POST …/export` | PNG artifact (Phase 1) |
| `DELETE /api/ui-designer/docs/{slug}` | soft delete (owner, `agents_edit` holder, admin) |
| `POST /api/ui-designer/imports` | start an import job `{file_key, slug, title, pages?}` → 202 with the job id; 409 while one is in flight for the slug — admin only (§8) |
| `GET /api/ui-designer/imports/{id}` | job status, per-page progress, the report when done |
| `GET /api/ui-designer/events` | SSE off `ui_designer.events` (Phase 1) |

Facade (`services/mcp-facade/facade.py`) lands **in T5 with the routes**,
because the facade generates its tools from the live OpenAPI at deploy
and an unlisted route would appear as a tool by default: `render`, `svg`,
`resolved`, `assets/{sha256}` and `events` join `EXCLUDED_PATHS` beside
the artifact byte routes and the SSE feeds (`facade.py:130-147`); the
pinned tool count in `test_facade.py:176` and the tier docstrings are
bumped in the same change. `edit`'s `snapshot` argument cannot be
excluded by path, so the **API** refuses it for any caller without a run
(the facade's admin bearer, any API key): `UD-NO-IMAGES-HERE`, "images go
to agent runs; use the web UI" — a T5 test. The text routes become
`list_ui_documents`, `create_ui_document`, `get_ui_document`,
`edit_ui_document`, `ui_document_outline`, `ui_document_versions`,
`restore_ui_document`, and from Phase 1 `inspect_ui_document`,
`export_ui_document`, `start_figma_import` and `get_figma_import`
(`GATED_ADMIN`, `facade.py:236`). Plainly: **Kyle and Codex over the
facade get text — `outline`, `get`, `inspect`, `edit` without snapshots —
plus the web UI**; there is no snapshot tool on the facade, `/resolved`
is excluded, and an export is an artifact Kyle opens at `/artifacts/<id>`.

Every write publishes `{type: version|comment|deleted|exported, slug,
version, author, run_id}` on `ui_designer.events` (`TOPIC_UI_DESIGNER_
EVENTS` in `events.py`), feeding the SSE route and a card in **`#design`**
(seeded like `ART_SEED_CHANNEL`, `db.py:1715`, topic "every exported
design frame, as a card") on `export` and on a labeled version — never on
every edit. Cards are `kind=event` rows so a frame titled "@all look"
summons nobody. A `[[ui:<slug>]]` / `[[ui:<slug>/<node>]]` chip in Relay
renders the latest render as a card through the chip rewrite (Phase 3).

## 7. Web UI (`/ui-designer`)

`UiDesigner.tsx`: the document list (title, slug, owner face, version,
updated, a thumb of the first frame), a search box, **New document**, and
**Import from Figma** (Phase 2). Follows `Artifacts.tsx` and `Wiki.tsx`
for `api<T>()`, `useTitle`, `@ap/ui` controls, filters as URL params.

`UiDesignerDocument.tsx`, three columns, stacked on a phone:
- **Outline** (left): page tabs and the node tree from `/resolved`; click
  selects; warnings as a dot with a tooltip.
- **Canvas** (centre): the server's SVG for the page (`/svg`), inside a
  pan/zoom viewport, with an **interaction overlay** built from
  `/resolved`: transparent hit rectangles per node for hover, select and
  comment pins, selection outlines, resize handles. The browser never
  lays out or paints a node itself, so what Kyle sees is what the agents
  see; a **PNG** toggle swaps in `/render` to confirm it. Mode toggle
  (dark/light).
- **Inspector** (right): the selected node's properties as a form (title,
  size, fill with the token picker, text, layout) and a **JSON** tab with
  the raw subtree. Save sends an `edit` batch with `expected_version`,
  author `user:kyle`, reason prefilled "edited in UI Designer". Direct
  manipulation (Phase 3): drag a free-positioned node, drag handles to
  resize a frame, reorder a child in a laid-out frame; each gesture ends as
  one `update`/`move` op.
- **Header**: version picker with labels, **Label this version**,
  **Compare** (two versions → side-by-side PNGs with a swipe divider and
  the op list between them; changed nodes outlined), **Restore**, **Export
  PNG**, **Ask an agent** (opens `#design` with `[[ui:<slug>]]`
  prefilled). SSE (Phase 1) keeps the page live while the personas edit.

Phase 0 ships only the list and a read-only document page (outline, SVG
canvas with select, version picker); it is small because the server does
the drawing. Sidebar entry **UI Designer** under Studio (`Layout.tsx`).

## 8. Figma import (`tools/figma_import/`, `ui_designer/figma_convert.py`)

Legitimate routes only: Figma's REST API with Kyle's personal access
token (a `figma-token` secret block, `secrets/figma-token/secret.yaml`,
scope `file_content:read` only; the probe is `GET /v1/files/:key?depth=1`
against `ui_designer.figma_probe_file_key`, a chart value Kyle sets to
one desk-buddy key, because `GET /v1/me` would need `current_user:read`
as well — with no key configured the probe reports "unverified" rather
than failing). The saved-JSON upload route is dropped: a saved
`GET /v1/files` body carries `imageRef`s whose bytes come only from the
image-fills endpoint, so a file without a token imports without its
images; one route with the token is simpler and complete. No `.fig`
parsing (undocumented, reverse-engineered only).

An import is a **job**, not a request: `POST /api/ui-designer/imports`
inserts an `ui_designer_imports` row (the slug is the idempotency key; a
second start for a slug with a job `queued`/`running` is 409) and returns
202 at once — the facade's upstream timeout is 60 s (`facade.py:67`) and a
file takes minutes. The dispatcher drives the job page by page (the
`task_scheduler` pattern: pick the oldest `queued`, mark `running`,
advance one page, persist), and each completed page's **converted
subtree, asset sha256s and warnings are written to `staged`** in the same
transaction that marks the page `done`, so a dispatcher restart resumes
at the next pending page with nothing re-fetched. The document is written
**once, at the end**, assembled from the staged pages (or, with failed
pages, a partial document that lists them); the report lands on the row,
a `#design` card is posted ("imported <slug>: n frames, m approximations,
k dropped — report") and `imported` is published on `ui_designer.events`.
`GET /api/ui-designer/imports/{id}` is the status route the web page and
the facade poll.

The dispatcher cannot call the tool-executor: the executor's
NetworkPolicy admits only the api and mcp-broker pods
(`charts/agent-platform/templates/networkpolicy.yaml:133-146`), and only
those carry `AP_EXECUTOR_URL` and the ghostunnel client. So the job calls
an **internal API route**, `POST /api/internal/ui-designer/figma-fetch`
(dispatcher workload identity only, the shape of the quota internal
route), and the API runs the executor tool the way `/api/artifacts/
generate` runs `image_gen`, streaming the sink files back as the
response. The fetch itself is an **internal executor tool** (`internal:
true`, `tools.md` "Internal tools"), because the executor is the single
egress point and the token must never enter the API process or a pod.
`run.py` (stdlib) takes `{file_key, page_ids?, node_ids?, what:
tree|images|references}` and the job drives it, through that route, in
calls shaped to the executor's bounds (stdout 256 KiB, 8 sink files × 8
MiB, 300 s):
- `tree`: `GET /v1/files/:key?depth=1` for the page list, then one call
  per page with `ids=<page>&geometry=paths`, the JSON written to
  `TOOL_OUT_DIR/tree.json` (a file, not stdout); a page whose JSON exceeds
  8 MiB is refused with `UD-IMPORT-PAGE-TOO-BIG` naming its top-level
  frames, and the job re-calls per frame with `node_ids`;
- `images`: `GET /v1/files/:key/images` (the `imageRef` → URL map; URLs
  expire after 30 days), then up to 8 refs per call into the sink; the job
  loops over the refs the tree uses and stores each as a `ui_designer_
  assets` row;
- `references`: `GET /v1/images/:key?ids=…&format=png&scale=1` for the
  top-level frames, Figma's own render, kept as artifacts tagged
  `figma-reference` so the report shows ours beside theirs.

Rate limits are honoured (`Retry-After` on 429; a Starter Full seat gets
about 10 Tier-1 requests per minute), and the route is re-runnable with
`pages`/`node_ids` so a partial import can be completed.

`figma_convert.py` maps the tree: `CANVAS` → page; `FRAME`/`GROUP`/
`SECTION` → `frame` (`layoutMode` HORIZONTAL/VERTICAL → `layout.dir`,
`itemSpacing` → `gap`, the four paddings → `pad`,
`layoutSizingHorizontal/Vertical` FIXED/HUG/FILL → `w`/`h`,
`primaryAxisAlignItems`/`counterAxisAlignItems` → `justify`/`align`,
`layoutPositioning: ABSOLUTE` → `absolute: true`, `clipsContent` → `clip`,
`cornerRadius`/`rectangleCornerRadii` → `radius`, `GRID` layout → a
column frame, approximated); `RECTANGLE`/`ELLIPSE`/`LINE` → their nodes;
`VECTOR`/`BOOLEAN_OPERATION`/`STAR`/`POLYGON`/`REGULAR_POLYGON` → `path`
from `fillGeometry`/`strokeGeometry`; `TEXT` → `text` with `characters`
split into runs by `characterStyleOverrides`/`styleOverrideTable` (a
family outside the allowlist → Inter, approximated); `COMPONENT`/
`COMPONENT_SET` → `components[...]` (one component per variant, named
`set/variant`); `INSTANCE` → `instance` when the component is in the
file, else a flattened frame with `meta.figma.instance_of`
(approximated); image fills → `image` nodes over the fetched assets;
`GRADIENT_LINEAR`/`_RADIAL` → gradients, other gradient kinds → the first
two stops (approximated); stacked `fills`/`strokes` → the topmost visible
paint (approximated when more than one is visible); `DROP_SHADOW` →
`shadow`, other effects dropped. **Styles**: Figma's `styles` map carries
names and keys only, so each token's value is read from the first node
that references the style (`node.styles.fill` / `.text`), and a style no
node uses is not imported. Every node keeps `meta.figma.id`. Variables
are not imported: the local-variables endpoint is Enterprise only
(Appendix A).

**Geometry.** Position and rotation come from `size` and
`relativeTransform` — "the top two rows of a matrix that represents the
2D transform of this node relative to its parent", `[[m00, m01, m02],
[m10, m11, m12]]`, present with `geometry=paths` — never from
`absoluteBoundingBox` subtraction, which is the axis-aligned bounds of a
rotated node. Figma's `rotation` is `atan2(-m10, m00)` in degrees,
measured about the node's **top-left** (the plugin docs, Appendix A);
our `rotate` is clockwise-positive about the node's **centre**, so:
`rotate = atan2(m10, m00)`, `R = [[m00, m01], [m10, m11]]`, `c = (w/2,
h/2)`, and `(x, y) = (m02, m12) + R·c − c`. Worked example, in the
fixture tests: a 100×50 node with `relativeTransform [[0, -1, 200],
[1, 0, 100]]` has Figma `rotation = atan2(-1, 0) = -90°`; ours is
`rotate = 90`, `R·c = (−25, 50)`, so `x = 200 − 25 − 50 = 125`, `y = 100
+ 50 − 25 = 125`, and the rendered box matches Figma's
`absoluteBoundingBox` of `(150, 100, 50, 100)` — the test asserts that
equality for 0°, ±90°, 180° and 37°. `fillGeometry`/`strokeGeometry`
paths are in the node's own untransformed space, so they get the same
`rotate` and no further transform; a non-uniform scale or skew in
`relativeTransform` (rare, from flipped layers) is baked into the path
data and reported as an approximation. Auto-layout children ignore the
transform's translation (the layout engine places them) and keep only
the rotation.

**Groups.** A `GROUP` or `BOOLEAN_OPERATION` has no layout of its own and
its children's `relativeTransform` is relative to the nearest real
container above it, not to the group. The importer gives the group's
`frame` the group's own `size` and transform (converted as above) and
**rebases each child**: `child' = inverse(T_group) · T_child` before the
child's own conversion, so the tree agents see nests correctly and
renders land where Figma put them; nested groups compose the inverses.

**Masks.** In Figma a node with `isMask: true` "masks sibling nodes in
front of it", up to the next mask or the end of the parent; `maskType` is
`ALPHA` (the mask's alpha channel sets each pixel's opacity), `VECTOR`
(every pixel inside the fill — and visible stroke — regions is fully
visible, whatever the paint's alpha) or `LUMINANCE`. The importer
regroups: the mask node and every following sibling become the children
of a new `frame` with `mask` set to the matching enum value (first child
masks the rest), inserted at the mask node's index. The painter maps
`alpha` to an SVG `<mask>` of the child, `luminance` to the same with
`mask-type: luminance`, and `vector` to a `<clipPath>` built from the
child's fill and stroke geometry (paint alpha ignored). The regrouping is
listed as an approximation in the report because it changes the tree
shape agents will see.

The **report** (`UiImportReport`) counts, per node type, **converted**,
**approximated** (with the approximation named: font substituted, gradient
simplified, grid flattened, instance flattened, effect dropped) and
**dropped**, each with the Figma node id and path, lists the reference
artifacts, and for each top-level frame a comparison artifact (ours beside
Figma's, built with Pillow). The acceptance bar is in §10.

**T0b** runs once before the schema is frozen: with Kyle's token, pull the
desk-buddy files' trees and produce a node-type and feature census (layout
modes, sizing, absolute children, rotations, masks, gradient kinds, mixed
text runs, effects, fonts, component/instance depth, image fills, the
largest page in bytes), so the Phase 0 vocabulary and limits are sized by
the real files.

## 9. Permissions, images, fences

- **Grant.** `TOOL_UI_DESIGNER = "mcp__platform__ui_designer"` joins
  `PLATFORM_MCP_RELAY_TOOLS` (`agentspec.py:111`): the participant rung,
  reaching `/api/ui-designer/*` as the agent and nothing else; holding it
  does not promote a run to `annotator`. A `TOOL_HELP` entry
  (`agentspec.py:165`) documents it. Not default-granted (an admin adds it
  to Pai, Olu, Kai and `coder`); not Kyle-only (it steers no agent).
- **Documents are platform-shared**, like wiki pages: any holder reads
  and edits any document; every version is attributed; restore is a new
  version; delete is soft. Per-document editors are deferred (§12).
- **Images: copy on place.** An image enters a document through one
  door, `POST …/place {artifact_id}` (API layer, T5, because the check
  needs the `Request`): the API requires the caller to hold
  `TOOL_ARTIFACTS` in `_caller_platform_tools` itself (`_authorize_read`
  alone is not enough — `artifacts_granted` defaults to `True`,
  `api/artifacts.py:217`), then runs the real `_authorize_read`
  (`api/artifacts.py:213`: run visibility, the grant; Kyle's session too),
  and on top of it **refuses an App-owned artifact outright**
  (`app_artifacts.ownership(...) is not None` → 404, the avatar rule at
  `api/agent_self.py:212`) and **refuses any artifact whose run carries
  a read restriction of any kind** — the room-visibility, startup-pilot
  and local-persona-session cases `assert_readable_run` checks
  (`authority.py:116-124`), not only the room one. What may be placed is
  therefore: an artifact with a run that has no restriction, placed by a
  caller who can read it; or Kyle's own uploads with no run. An admin
  placing anything else is refused too: a document is readable by every
  holder of the tool and nobody's view may widen to them. On success the
  bytes are copied into `ui_designer_assets` by sha256 and a
  `ui_designer_document_assets` row `(document, sha256, placed_by,
  placed_from)` is written; the sha256 is what the node stores. Assets
  are authorized **once, at placement**: `insert`/`replace`/`set_doc`
  validate that every `image.asset` has a placement row for this document
  (`UD-ASSET-UNKNOWN` otherwise), `restore` re-uses the document's
  placement rows, and import writes its assets and placement rows
  directly (`placed_from: import:<job>`; they were Kyle's Figma content).
  From then on the document is self-contained: deleting or pruning the
  artifact never breaks a render, a restore of an old version renders
  exactly what it rendered, and nobody reads an artifact through a
  document who could not read it at placement. An asset is
  garbage-collected only when **no placement row and no retained version
  of any document** names it; a placement row is removed when its
  document is hard-deleted, and the sweep runs weekly and on demand after
  a prune (§10).
- **Dream fence**: `fenced()` in `api/auth.py:687` returns only
  `MEMORY_TOOL` for a dreaming run — nothing to add. **Rapid-chat fence**:
  turns get `{TOOL_WIKI, TOOL_QUOTA}` from `fenced()` and the broker
  denies anything else at `broker.py:426`; `ui_designer` is excluded by
  construction. The Phase 3 plan for design sprints therefore changes
  three things together: `fenced()` adds `TOOL_UI_DESIGNER` to a rapid
  turn's set, `RAPID_READ_PATHS` (`auth.py:318`) admits `GET
  /api/ui-designer/*` so turns can `outline`/`snapshot` while writes stay
  the controller's, and `rapid_chat.py` gains a `ui_designer` sink kind
  ("Sink writing is one function… a second kind is one branch") — open
  question 2.
- **Untrusted content**: every document is data, the design-08 posture.
  Text, titles, reasons and comments are stored as strings; the painter
  escapes; fonts, colours and numbers are validated; the web shows the
  server's SVG and React text nodes only.
- **Facade**: imports are `GATED_ADMIN`; the token lives only in the
  executor subprocess for the call; a file key is not a secret.
- **Audit**: `platform.tool.audit` rows from `_metered`; `ops`, `reason`
  and `run_id` on every version; receipts; comments attributed.

## 10. Versioning, backups, acceptance

**Versions.** The version counter **increments on every accepted write**;
`expected_version`, receipts (which return the final number), `immutable`
URLs and `restore` all speak in those numbers. What is folded is the
**row**, not the number: when the same run writes again within **120 s of
the row's first write** (`last_write_at` does not extend the window) and
no other principal has read or written the document since (the store
keeps `last_reader` per document from `get`/`outline`/`snapshot`), the
new snapshot replaces the row's `doc`, the write is appended to `chunks`
as `{version: n, ops, reason, at}` and the row's `version` becomes `n`.
A row with a `label`, another principal's row, Kyle's run-less writes
(never folded), or a window that has passed starts a new row. Every
number stays **exactly** resolvable: a chunk's `ops` are stored as
applied — every server-generated id and default written back into the
op — so the document at a folded `n` is the previous row's snapshot with
the chunks up to `n` replayed, deterministic by construction, and
`get version=n`, `restore version=n` and `snapshot version=n` all
reconstruct it that way (a T3 test replays every chunk of a fixture and
compares with snapshots taken at each write). `history` lists rows as
`v7`, `v8–v12`, `v13`; `history diff from=7 to=12` returns the chunks
`8…12` in order. A row is **open** while it can still fold — the window
has not passed and no other principal has written — and **closed**
afterwards; `immutable` caching is sent only for a number in a closed
row, every other number answers `no-cache` with an ETag. A CAS against any
number is answered with the current number and the chunks after the
caller's `expected_version`, so a rebase is exact. Nothing is thinned
automatically: a retained row lives until a prune (below) or the
document's deletion. (The wiki keeps every write; the difference here is
only that one agent's burst of `edit … snapshot` calls is one row, which
is what a human would call one edit.)

**Size.** A spectator-UI document is ~50–200 KiB of JSON; a three-persona
session is ~60–150 edits, coalescing to ~20–40 versions; JSONB TOAST
compresses UI JSON about 5:1, so a heavy day is ~2 MB of version rows and a
year of regular use is on the order of 100–500 MB. Assets add their bytes
once per distinct image. Those numbers matter because the backup path has
limits: `backup_export.py:161` refuses a `database.sql.gz` over **512
MiB** — but only at inspect/extract time, so T5 adds the same check at
export time in `_export_locked` (`backup_export.py:230`), failing the job
loudly instead of producing an archive that can never be restored; the
cloud job's work volume is a **1 Gi** emptyDir (`backup-cloud.yaml:118`);
and **both** jobs write to the same `ap-pg-backups` PVC
(`backup-cloud.yaml:120`, `pg-backup.yaml:104`), **5 Gi**
(`values.yaml:313`), each keeping **14** copies — so the dump must stay
under ~170 MiB compressed for 28 copies to fit, and today's dump is the
number to measure before T5 sets the values. The designer's share is
sized from that ~170 MiB budget, not from the 512 MiB check: a **total
cap** `ui_designer.max_total_bytes` of **48 MiB** (versions and documents
as compressed JSONB on disk, assets at raw size — rasters do not compress
in a dump) and a per-document cap `ui_designer.max_version_bytes_per_doc`
of **12 MiB**; past either, an `edit`, `place` or import is refused with
`UD-BUDGET-STORAGE`, naming what to reclaim; both are chart values and
the Dashboard shows the total beside the artifacts meter. **Reclaim paths**
(Kyle's session only, each a route and a button on the document page):
`DELETE …/docs/{slug}?hard=true` removes the document, its versions,
comments and placement rows now and runs asset GC; `POST …/docs/{slug}/
prune {older_than_days, keep_last}` removes unlabeled version rows older
than N days beyond the newest `keep_last` (default 20), re-basing the
first surviving row's replay on its own snapshot so every surviving number
stays exact, then runs asset GC; and asset GC itself is also a button.
The weekly sweep only collects; it never prunes versions on its own.

**Backups.** The cloud backup job runs `pg_dump --no-owner --no-acl` of the
whole `agentplatform` database (`backup-cloud.yaml:71`) and `ap-pg-backup`
the same (`pg-backup.yaml:66`); both gain `--exclude-table-data=public.
ui_designer_renders`. Documents, versions, assets, comments and receipts
are then in every archive. Fonts and the allowlist are in git.
`figma-token` is a user-managed connector secret, captured in
`secrets.json` like the others (`backups.md` step 4). After a restore the
render cache is empty and refills; the restore report needs no new
section.

**Acceptance (desk-buddy, and the backup).** Kyle runs `import_figma_design`
for each desk-buddy file. Pass when: every page and top-level frame exists
under its Figma name; the report drops no `FRAME`, `TEXT`, `RECTANGLE`,
`ELLIPSE` or image-fill node and lists every approximation; each
top-level frame's comparison artifact is judged "recognisably the same" by
Kyle on the Compare view (text in the right boxes, colours and radii right,
layout within a few pixels); Pai can `outline` the document, change one
text and `snapshot` the changed frame in one `edit` call; Kai (Codex) does
the same and describes what it sees in the returned image. Backup: take a
cloud backup after the import, extract it with `scripts/backup_restore.py
extract`, restore the dump into a scratch Postgres (a throwaway container
on the laptop), and `SELECT count(*)` on `ui_designer_documents`,
`ui_designer_versions` and `ui_designer_assets` matches the live counts,
while `ui_designer_renders` is empty.

## 11. Alternatives considered

- **Embed Penpot** (MPL-2.0, Clojure/ClojureScript, Postgres 15 + Valkey +
  a Chromium exporter + its own MCP that drives a *live browser tab*
  through the plugin API, 2.19.2 released today). Rejected: a second
  stack with its own database, auth and backup on the NUC; the official
  agent path needs an open, awake Penpot tab and acts on the focused
  page, the opposite of a server-side tool three agents call
  concurrently; ~16 GB RAM guidance for its server. Penpot is the right
  answer for humans who want Figma without Figma; it is not agent-first.
- **Embed tldraw** (source-available since SDK 4.0; production use needs
  a licence or shows a watermark; export is browser-only, "use headless
  Playwright"; an official Agent starter kit exists). Rejected: a
  whiteboard, not a UI tool — no auto layout, components, tokens or
  modes; server rendering means Chromium; the licence is a cloud over a
  tool Kyle wants to own.
- **Excalidraw** (MIT, JSON, hand-drawn style): a whiteboard; same
  objections minus the licence. Its one-shot diagram MCP is a different
  product.
- **HTML/CSS as the document, Chromium as the renderer.** Tempting because
  models write HTML fluently. Rejected: no stable node identity for ops or
  comments, no structured diff, non-deterministic renders, Chromium in the
  API, and a human editor becomes a code editor. HTML export is a later
  `export` format (§12).
- **SVG as the document** (Penpot's approach): parsing agent-written SVG
  is a security surface and still needs a model layer for layout, tokens
  and components. JSON with a server-side SVG painter keeps the markup on
  our side.
- **A second painter in the browser** (React SVG from the resolved tree):
  rejected after review because two text engines disagree; the server's
  glyph outlines are the one painter and the browser only overlays.
- **Documents as artifacts (JSON files)**: no versions, CAS, comments or
  queries; would re-grow a store.
- **Documents as an App (app-data)**: records, versions, quotas and
  proposals exist there, but a document is one structured blob with its
  own validator and renderer, not a collection of records. The idioms are
  reused: `request_id` receipts, `expected_version`, closed schemas, `UD-*`
  error codes shaped like `AD-*`.
- **Keep Figma, use its MCP.** The official remote server can now write
  (`use_figma`), but MCP reads need a Full seat on a paid plan (Starter:
  none), seats per agent, and the platform's agents have no Figma
  identity; it is the dependency Kyle wants out of.

## 12. Deferred

- Per-document editors/readers; a `design` team concept.
- Rapid-chat `ui_designer` sink, turn fence and `fenced()` change (Phase 3,
  open question 2).
- Blend modes, image masks beyond the first-child rule, grid layout,
  constraints on resize, prototyping links (`link_to`) with a click-through
  viewer, component variants as a first-class set.
- SVG export as a deliberate artifact type; HTML/CSS and React-snippet
  export ("Dev Mode" output); CSS custom properties from tokens.
- Cross-document libraries (`uses: ["design-system"]`).
- Real-time multi-cursor editing; today concurrent edits are CAS + rebase.
- Stacked fills and strokes as arrays.

## 13. Risks

| risk | mitigation |
|---|---|
| Agents cannot see MCP image blocks in some runtime path | T0 spike with real Kai and Pai runs before T1; `as=boxes` text fallback; `export` + artifact card |
| Glyph outlines differ from the measured width | same shaping (uharfbuzz) feeds both; font-parity test in T2; 1 px tolerance, 2 px slack on `hug` |
| Agents still write coordinate soup | auto layout is the default on `frame`; `x`/`y` without `absolute` under a laid-out frame is a warning; the skill teaches "frames with layout, tokens, then look" |
| Render CPU/memory on the NUC during a three-persona session | content-hash cache; derived megapixel budget; 10 MP per image; a renderer Deployment with its own cgroup limit, `RLIMIT_DATA` and a job timeout, so a bad render restarts a worker and never the API; path/filter caps; the T2 benchmark gate |
| Document dumps blow the transcript | `get` has `depth` and `node`; `outline` is the default read; 32 KiB text cap with a pointer |
| Figma rate limits during import (Starter Full seat: ~10 Tier-1 requests/min) | one tree fetch per page; images in batches of 8; `Retry-After` honoured; re-runnable with `pages`/`node_ids` |
| Figma plan changes (REST limits moved to seat × plan on 2025-11-17) | the importer is one tool behind one secret |
| Version rows outgrow the backup limits | coalescing, per-document version budget, Dashboard meter, cache excluded from dumps |
| Expansion bomb from nested instances | depth ≤ 6 and ≤ 20,000 expanded nodes checked at validation |
| Scope creep toward Figma | the non-goals list; every new node type or prop is a reviewed change to `schema.py` with a painter, a layout rule and a test |
| A persona wrecks the shared document | full-snapshot versions, `history diff`, `restore`; a labeled version per milestone |

## 14. Open questions for Kyle

1. Grants: `ui_designer` to Pai, Olu, Kai and `coder`, no default grant — right?
2. Rapid-chat: build the `ui_designer` sink, read fence and `fenced()` change in Phase 3, or leave design sprints to ordinary runs in `#design` for now?
3. Fonts: Inter, JetBrains Mono, Noto Sans to start. T0b will list what the desk-buddy files use; any family not under an open licence stays substituted.
4. `#design` channel: platform-only, or bridged to Discord like `#art`?
5. Figma plan: is Kyle's account still Starter with a Full seat? The importer assumes ~10 Tier-1 requests/min and a personal access token with `file_content:read`.

## 15. Task breakdown (dependency order; grouped by file)

| # | unit | files | depends on | check |
|---|---|---|---|---|
| T0 | **Image spike (one day):** a throwaway broker action returns a fixed PNG as `ImageContent`; broker image rebuilt and deployed; real in-cluster runs of Kai (codex 0.159) and Pai are asked to describe the picture; result recorded in this doc's §3.1 | `broker.py` (temporary), deploy | — | both runs describe the image correctly |
| T0b | **Desk-buddy census:** pull the files' trees with Kyle's token once (laptop script, token never stored), emit the node/feature census; adjust §2.2 before T1 | `scripts/figma_census.py` (laptop-only) | — | census table in the PR description |
| T1 | Schema + validator + ops + grant constant: closed JSON Schema, id generation, token resolution, expansion budget, op application with CAS, `UD-*` errors; `TOOL_UI_DESIGNER` in `PLATFORM_MCP_RELAY_TOOLS` + its `TOOL_HELP` entry (lands first so T5's access check, the local-persona grant and the routes can name it) | `ui_designer/schema.py`, `ops.py`, `errors.py`, `agentspec.py`, `tests/test_ui_designer_schema.py`, `tests/test_agentspec*.py` | T0, T0b | `pytest tests/test_ui_designer_schema.py tests/test_agentspec*.py -q` |
| T2 | Constrained layout + text: measure/place with constraints, instances, modes, `absolute`, rotation bounds, uharfbuzz shaping + fontTools outlines, warnings; fonts and the font-parity test; the benchmark gate. Two Sonnet units (measure/place; text) or one Opus unit | `ui_designer/layout.py`, `text.py`, `fonts.py`, `fonts/*.ttf`, tests with golden resolved trees | T1 | `pytest tests/test_ui_designer_layout.py tests/test_ui_designer_fonts.py -q`; benchmark under 250 ms |
| T3 | Store + tables + pruners: documents, folded version rows with chunks, exact replay to any number (replay-vs-snapshot test), open/closed rows, assets + placement rows + GC, receipts (text only, `scope`), imports table with `staged`, soft and hard delete, prune, render-cache table, the two storage caps | `db.py`, `ui_designer/store.py`, `pruning.py` | T1 | `pytest tests/test_ui_designer_store.py -q` |
| T4 | **Renderer service:** painter (SVG with glyph paths, gradients, masks, rotation, annotate overlay, `<defs>`/`<use>` assets, resources dir), resvg, the downscale/re-encode ladder, `RLIMIT_DATA` + job timeout per worker, work caps; its image, chart Deployment/Service/NetworkPolicy and resources; the API-side client with cache rows keyed by `renderer_version` | `services/ui-renderer/{app.py,Dockerfile,requirements.txt}`, `ui_designer/svg.py`, `ui_designer/render_client.py`, `charts/agent-platform/templates/ui-renderer.yaml`, `values.yaml`, tests | T2, T3 | `pytest services/ui-renderer tests/test_ui_designer_render.py -q` (golden PNG hashes; a job-timeout test); `helm lint` |
| T5 | API + facade + helm: router with `require_ui_designer_access`, schemas, `include_router` in `api/app.py`, `place` with the artifact and run-restriction checks + placement rows, assets route, hard delete + prune + GC routes, budgets, 32 KiB text cap, cursors, caching headers (immutable only for closed rows), snapshot deadline, `snapshot` refused for run-less callers (test), `local_persona.py` ROUTES; facade `EXCLUDED_PATHS` + `test_facade.py:176` count and tier docstrings; chart values for budgets/caps; backup `--exclude-table-data` in both CronJobs and the export-time size check in `_export_locked`; SDK regen | `api/ui_designer.py`, `api/schemas.py`, `api/app.py`, `local_persona.py`, `services/mcp-facade/facade.py` + `test_facade.py`, `backup_export.py`, `charts/agent-platform/values.yaml`, `templates/backup-cloud.yaml`, `templates/pg-backup.yaml`, `sdk/` | T3, T4 | `pytest tests/test_ui_designer_api.py tests/test_auth*.py tests/test_local_persona*.py tests/test_backup*.py -q`; `pytest services/mcp-facade -q`; `python sdk/regenerate.py && git diff --exit-code sdk/`; `helm lint` |
| T6 | Broker tool: `ui_designer` with `list/create/get/outline/edit/snapshot/history/restore` (Phase 0) and `place` (with images, Phase 1), `_with_pictures`, `_UI_DESIGNER_TIMEOUT`; broker image rebuild + deploy | `services/mcp-broker/broker.py`, `services/mcp-broker/test_ui_designer_tool.py` | T5 | `pytest services/mcp-broker -q -k ui_designer` |
| T7 | Web, read-only: list page; document page with outline, server-SVG canvas + overlay select, version picker; nav | `pages/UiDesigner.tsx`, `pages/UiDesignerDocument.tsx`, `components/ui-designer/*`, `App.tsx`, `Layout.tsx` | T5 | `npm run build -w web \| tail -5`; Playwright smoke |
| T8 | Minimal skill + docs: `ui-designer` skill (outline → ops → look; retry with the same `request_id`), `docs/building-blocks/ui-designer.md`, README row | `skills/ui-designer/`, docs | T6 | review |
| T9 | **Phase 0 live:** grants to the four agents; Pai builds the spectator HUD; Kai edits and describes a snapshot; Kyle views it; a restore after a deliberate bad edit | — | T6, T7, T8 | §10 evidence (first half) |
| T10 | Phase 1: `inspect`, `comments`, `history diff`, `export` (PNG artifact), `ui_designer.events` + SSE + `#design` seed and cards, the Phase 1 facade pins, web comments/compare/label/export | `broker.py`, `api/ui_designer.py`, `events.py`, `db.py` (seed), `facade.py` + `test_facade.py`, web | T9 | the tests above extended; facade operationId test |
| T11 | Phase 2: `figma-token` secret block with the file-key probe, internal tool with the three modes and executor-bound batching, the internal API route `POST /api/internal/ui-designer/figma-fetch` that runs it (dispatcher identity only), the import **job** in the dispatcher (staged pages, resumable, slug idempotency, status route, `#design` card, document written once at the end), converter (transform geometry with the rotation worked example, group rebasing, mask enum regrouping, styles from nodes), report with approximations, comparison artifacts, web button + status | `secrets/figma-token/secret.yaml`, `tools/figma_import/{tool.yaml,run.py,test_run.py}`, `ui_designer/figma_convert.py`, `ui_designer/figma_import_job.py`, `dispatcher_main.py`, `api/ui_designer.py`, `api/internal.py` (or where the quota internal route lives), web | T10 | `pytest tests/test_figma_convert.py tests/test_figma_import_job.py -q` on a recorded fixture incl. rotation/group/mask cases; tools CI job |
| T12 | Phase 2 live: desk-buddy import, Compare, the scratch-Postgres backup check | — | T11 | §10 evidence (second half) |
| T13 | Phase 3: inspector edits and direct manipulation, Relay `[[ui:]]` chips, rapid-chat sink + fence + `fenced()` | web, `relay` chip rewrite, `rapid_chat.py`, `auth.py` | T12, question 2 | tests per piece |

Phase 0 = T0–T9: schema, ops, layout, the renderer service, store, the
broker tool with `outline`/`edit`/`snapshot`/`history`/`restore`, a
read-only web view and a minimal skill — enough for the three personas to
design the spectator HUD, for Kyle to look, and for anyone to undo. Deploy
per phase: backend image (api + dispatcher), the renderer image, broker
image, web, and a facade restart from T5 on (the exclusions must be live
before the routes are); T11 also runs the tools CI job (no new executor
deps).

## Appendix A — sources (verified 2026-11-16)

Figma product and plans
- Pricing: Professional Full $16, Dev $12, Collab $3; Organization Full $55, Dev $25, Collab $5; Enterprise Full $90, Dev $35, Collab $5; variable modes 10 (Pro) / 20 (Org) / unlimited (Enterprise). https://www.figma.com/pricing/
- Starter: one team, three files, 30-day version history, no Dev Mode, no variable modes. https://help.figma.com/hc/en-us/articles/360040328273-Figma-plans-and-features
- Figma Make (prompt-to-app) and Sites: https://help.figma.com/hc/en-us/categories/31304285531543-Figma-Make

Figma REST API
- File and image endpoints (`GET /v1/files/:key` with `geometry=paths`, `depth`, `ids`; `GET /v1/files/:key/images` for image fills; `GET /v1/images/:key` with `format jpg|png|svg|pdf`, `scale 0.01–4`, 32-megapixel cap, URLs expire in 30 days): https://developers.figma.com/docs/rest-api/file-endpoints/
- Node types and auto-layout properties (`layoutMode`, `itemSpacing`, paddings, `layoutSizingHorizontal/Vertical`, `layoutPositioning`, `primaryAxisAlignItems`, `isMask` "masks sibling nodes in front of it", `maskType` ALPHA/VECTOR/LUMINANCE, `relativeTransform` "top two rows of a matrix … relative to its parent" with `geometry=paths`, `componentId`, `overrides`, `characterStyleOverrides`): https://developers.figma.com/docs/rest-api/file-node-types/
- `rotation`: degrees, −180…180, "identical to `Math.atan2(-m10, m00)` in the relativeTransform matrix", "with respect to the top-left of the object" (plugin API; the REST node carries the same matrix): https://developers.figma.com/docs/plugins/api/properties/nodes-rotation/
- Rate limits since 2025-11-17 by seat × plan; Tier 1 (files, nodes, images) Dev/Full seats: Starter 10/min, Pro 15/min, Org 20/min; View/Collab 20/month; `Retry-After`: https://developers.figma.com/docs/rest-api/rate-limits/
- Personal access tokens (scoped, shown once): https://help.figma.com/hc/en-us/articles/8085703771159-Manage-personal-access-tokens and https://developers.figma.com/docs/rest-api/authentication/
- Local variables endpoint (`GET /v1/files/:key/variables/local`, scope `file_variables:read`) "is available to full members of Enterprise orgs": https://developers.figma.com/docs/rest-api/variables-endpoints/
- `.fig` is undocumented (ZIP + "fig-kiwi" binary; unofficial parsers): https://grida.co/docs/wg/feat-fig/

Figma MCP server
- Remote `https://mcp.figma.com/mcp` and Desktop variants; writing needs the remote server and a Full seat: https://developers.figma.com/docs/figma-mcp-server/
- Rate limits: View/Collab 20/month; Full/Dev seats Starter none, Pro 200/day + 10/min, Org 200/day + 15/min, Enterprise 600/day + 20/min; write tools exempt: https://developers.figma.com/docs/figma-mcp-server/rate-limits-access/
- Tools: read `get_design_context` (replaces `get_code`), `get_screenshot`, `get_metadata`, `get_variable_defs`, `get_code_connect_map`, `get_libraries`, `search_design_system`, `download_assets`…; write `use_figma`, `generate_figma_design`, `html_to_figma`, `create_new_file`, `generate_image`…; prompt `create_design_system_rules`: https://developers.figma.com/docs/figma-mcp-server/tools-and-prompts/

Alternatives
- Penpot (MPL-2.0; Clojure/ClojureScript; Postgres + Valkey + exporter; mcp/ in-repo): https://github.com/penpot/penpot ; release 2.19.2 on 2026-11-16: https://api.github.com/repos/penpot/penpot/releases/latest ; compose services and 16 GB guidance: https://raw.githubusercontent.com/penpot/penpot/develop/docker/images/docker-compose.yaml , https://help.penpot.app/technical-guide/getting-started/recommended-settings/ ; Penpot MCP (live tab, `execute_code`, `export_shape`, `import_image`): https://help.penpot.app/mcp/
- tldraw licence (SDK 4.0+, watermark without a licence) and browser-only export: https://tldraw.dev/faq , https://raw.githubusercontent.com/tldraw/tldraw/main/LICENSE.md , https://tldraw.dev/sdk-features/image-export ; Agent starter kit: https://tldraw.dev/starter-kits/agent
- Excalidraw (MIT, `exportToBlob`/`exportToSvg`): https://github.com/excalidraw/excalidraw , https://plus.excalidraw.com/docs/sdk/excalidraw/api/utils/export ; MCP: https://github.com/excalidraw/excalidraw-mcp

Layout and rendering
- `stretchable` 1.1.8 (Taffy bindings, 2025-10-29; no measure callback documented): https://pypi.org/project/stretchable/ , https://github.com/mortencombat/stretchable
- `resvg_py` 0.5.0 (2026-08-24), Python ≥ 3.10, manylinux x86-64/aarch64 wheels, `svg_to_bytes(svg_string, width, height, skip_system_fonts, font_dirs, font_files, font_family, …, background)`: https://pypi.org/project/resvg-py/ , https://resvg-py.readthedocs.io/en/latest/api.html
- resvg-js (Node alternative, not chosen): https://github.com/yisibl/resvg-js
- skia-python 144.1 (2026-10-20; needs fontconfig/EGL on Linux): https://pypi.org/project/skia-python/
- node-canvas (Cairo): https://github.com/Automattic/node-canvas

Model and client limits
- Claude vision: standard tier long edge 1,568 px / 1,568 visual tokens, high-resolution tier (Claude 4.7+) 2,576 px / 4,784 tokens; 1920×1080 → 1456×819 on standard (≈ 1,590 tokens by the w×h/750 rule; the doc's table says 1,560); 8000×8000 max; stricter per-image limit above 20 images per request: https://platform.claude.com/docs/en/build-with-claude/vision
- Claude Code MCP output: warning at 10,000 tokens, limit 25,000 by default, `MAX_MCP_OUTPUT_TOKENS` to change it; text results over 50,000 characters are saved to a file: https://code.claude.com/docs/en/mcp
- Codex `tool_output_token_limit` (per-tool-output token budget; the sample config shows 12000 as an example, and the default for Codex models was raised to 10k per openai/codex#7867): https://developers.openai.com/codex/config-sample.md , https://github.com/openai/codex/issues/7867
- Codex renders MCP `ImageContent` tool results to the model (openai/codex change #5600; TUI fix #9815): evidenced from the repository history and a third-party write-up, not an OpenAI doc, which is why T0 exists: https://codex.danielvaughan.com/2026/06/04/codex-cli-visual-workflows-image-input-gpt-image-2-generation-asset-pipelines-v0137/

Platform code this design builds on
- Artifacts block, image content for models, serving rules, read authorization: `docs/building-blocks/artifacts.md`, `services/mcp-broker/broker.py` (`_API_TIMEOUT` 68, `_GENERATE_TIMEOUT` ~2087, `_with_picture` ~2246, `ARTIFACT_VIEW_MAX` ~2085, grant denial ~426), `services/backend/agentplatform/api/artifacts.py` (`_authorize_read` 213, `artifacts_granted` default 217), `api/agent_self.py` (App-owned refusal 212), `artifact_store.py`
- API pod resources: `charts/agent-platform/values.yaml` (108–116: 1 Gi limit, ~565 Mi baseline)
- Facade upstream timeout and tool-count pin: `services/mcp-facade/facade.py` (`_UPSTREAM_TIMEOUT` 67), `test_facade.py` (176)
- Tool rungs and Kyle-only set: `services/backend/agentplatform/agentspec.py` (`PLATFORM_MCP_RELAY_TOOLS` ~111, `KYLE_ONLY_TOOLS` ~70, `TOOL_HELP` ~165)
- Access dependency precedent: `services/backend/agentplatform/api/wiki.py` (`require_wiki_access` 62); local persona routes: `local_persona.py` (`ROUTES` 37)
- Fences: `services/backend/agentplatform/api/auth.py` (`rapid_turn_allows` ~315, `_dream_fence` ~329, `fenced()` ~687)
- Receipts precedent: `services/backend/agentplatform/appdata/models.py` (`AppDataBuildOp` 182)
- Internal executor tools and the file sink: `docs/building-blocks/tools.md`, `tools/image_gen/`, `services/tool-executor/executor.py` (`OUTPUT_CAP` 61), `services/tool-executor/Dockerfile`; executor ingress is api + mcp-broker only: `charts/agent-platform/templates/networkpolicy.yaml` (133–146)
- Run read restrictions (room, startup pilot, local persona session): `services/backend/agentplatform/authority.py` (`assert_readable_run` ~116–124)
- Backups: `docs/building-blocks/backups.md`, `charts/agent-platform/templates/backup-cloud.yaml` (71, 118, the shared PVC at 120), `templates/pg-backup.yaml` (66, PVC at 104), `charts/agent-platform/values.yaml` (312–313), `services/backend/agentplatform/backup_export.py` (size check 161, `_export_locked` 230)
- Seeded channel precedent: `services/backend/agentplatform/db.py` (`ART_SEED_CHANNEL` 1715)
- Kafka topics: `services/backend/agentplatform/events.py`
- Facade rules: `services/mcp-facade/facade.py` (`EXCLUDED_PATHS` 91–147, `GATED_ADMIN` 236, `MCP_NAMES` 302)
- Web: `services/web/src/App.tsx`, `pages/Artifacts.tsx`, `pages/Studio.tsx`, `pages/Wiki.tsx`
- Design-doc precedent for format and the rapid-chat fence: `docs/design/45-rapid-chat.md`
