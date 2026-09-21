# 23 — Artifacts, image generation, and the Image Studio

Status: **shipped 2026-09-18** (helm `ap` rev 58 on pai; live-verified: one
image from each provider through the Studio route — BFL in 5 s, Gemini in
3 s, OpenAI in 27 s — each with its `#art` card and `artifacts.event`;
`@artist` answered a brief in 35 s with a `[[artifact:…]]` card and iterated
"make it blue" with the first result as its reference; `pai` saved a note
artifact from Relay; `pai` wears a generated robot on the agents grid and in
every room; a markup saved from the Studio is a `derived` artifact naming its
parent; bytes serve with nosniff, inline only for rasters) — plan at
`docs/superpowers/plans/2026-09-17-artifacts-and-image-studio.md`. Builds on
Relay [19](19-relay-agent-messenger.md) (cards, participants), the tools
building block [12](12-executable-capabilities.md) (the executor runs the
provider code), and DB-first agents [15](15-db-first-agents.md) (the artist
is a row).

**Codex allowance addendum (2026-09-20).** Studio also offers **Codex
ImageGen · GPT Image 2** whenever the stored Codex login is healthy. This
dispatches a normal run to the seeded `codex-artist` instead of calling
`tools/image_gen`, so it spends Codex subscription quota and never enters the
API-dollar reservation ledger. The runner uses Codex's first-party image tool,
uploads the raster through a run-scoped endpoint, and the API stores it under
the same artifact, provenance, event, and `#art` card contract as provider
images. The original `artist` and every API-priced model remain available.

One of the numbered design records under `docs/design/`. The series index is
`docs/design/00-overview.md`; component names are defined in
`docs/building-blocks/glossary.md`, and Kyle is the project owner.

## The problem

Agents can only ever hand back text. A run's result is a string, a Relay
message is markdown, a report is a sanitised HTML fragment. Nothing on the
platform can hold a file — so an agent that wants to make a picture, keep a
CSV, or show its work has nowhere to put it, and the humans have nowhere to
look. Four asks arrived together and they are one feature:

1. **Artifacts** — files agents (and humans) can save and get back later.
2. **An artist** — an agent whose job is to make images, which means the real
   work is an image-generation capability the platform owns (Kyle already has
   working, tested provider code in `claude-ttrpg/tools/imagegen.py` for
   OpenAI, Gemini and Black Forest Labs).
3. **Agent images** — a face for every agent that is a picture, not just an
   emoji: upload one, or have the platform generate one from a prompt; and a
   card-grid Agents page that shows them.
4. **An Image Studio** — a deterministic page (no agent in the loop) to pick a
   configured model, write a prompt, optionally hand in a reference image,
   generate, browse what has been made, and do light markup on a result.

The bar is the ecosystem one: open the page and want to watch.

## The decision in one paragraph

An **artifact** is a named blob with a mime type, a size, a hash, an owner
(the Relay participant string), an optional run, a **source** (`upload`,
`generated`, `derived`, `tool`) and a JSON `meta` (for a generated image: the
provider, model, prompt, parameters, seed, cost). Bytes live in the platform's
Postgres (`bytea`, an 8 MiB per-artifact cap and a 2 GiB total cap), served by
one route that streams with `nosniff` and renders inline only for raster
images. Every write is a Kafka event on `artifacts.events`. Agents reach
artifacts through one default-granted broker tool, `artifacts`, whose `get`
returns an image **as an image content block**, so a model can look at a
picture. The **tool-executor gains a file sink**: a tool may drop files in
`TOOL_OUT_DIR` and read reference files from `TOOL_IN_DIR`; the caller (the
API) turns them into artifacts — so the executor stays the platform's only
third-party egress point and tools still hold no platform token.
**Image generation** is `tools/image_gen`, a port of Kyle's `imagegen.py`
marked `internal: true` (runnable only by the API, never exposed raw to
agents), bound to three secret blocks (`openai-api-key`, `gemini-api-key`,
`bfl-api-key`); the API's `POST /api/artifacts/generate` is the one place a
generation happens — it enforces the per-agent budget and the daily spend
cap, fetches reference artifacts, runs the executor, stores the result with
its cost, posts a **card into `#art`**, and publishes the event. The broker's
core `image_gen` tool and the Studio both call that route. An agent's
**image** is an unversioned presentation attribute (`image_artifact_id`, the
same seam as `icon`) that flows through `RelayFace` into every `Face` in the
UI. The **Agents page becomes a card grid** with a toggle back to the table.
The **Studio** is a page in the web SPA (`/studio`): configured models only,
prompt, size/quality, a reference strip, a result stage with provenance and
"use as agent image" / "iterate" / "mark up" actions, a recent-generations
strip, and a hand-rolled canvas markup layer (pen, arrow, rectangle, text,
crop) that saves a **derived** artifact linked to its parent. An **`artist`**
agent is seeded as a row with `image_gen + artifacts + relay` and the house
styles from Kyle's RPG and blog work.

## Naming

The block is **Artifacts**. An artifact has a **name**, a **mime**, a
**size**, a **sha256**, an **owner** (participant string, as in Relay), a
**source**, **meta**, **tags**, and for images a **width/height** and a
**thumb**. `[[artifact:<id>]]` in a Relay message is an **artifact card**.
A generated image's **provenance** is its meta (provider, model, prompt,
params, seed, cost, references). An artifact made from another (markup, crop)
is **derived** and names its **parent**. The page is the **Studio**; the
agent is the **artist**; the channel is **`#art`**. ORM classes are
`Artifact` and `ArtifactBlob`; the tool, topic, API and UI say artifacts.

## Data model (platform Postgres, additive via `db.py`)

```
artifacts
  id            str(32) pk   uuid4 hex (the runs convention)
  name          str(120)     sanitised filename or given name
  mime          str(80)      sniffed from bytes, never trusted from the client
  size          int          bytes of the original
  sha256        str(64)
  kind          str(8)       image | file   (image = png/jpeg/webp/gif by sniff)
  width, height int null     images only
  thumb         bytea null   images only: ≤ 512 px longest side, JPEG (PNG when alpha), ≤ 150 KiB
  owner         str(160)     participant: agent:<name> | user:<principal>
  run_id        str(32) null
  source        str(12)      upload | generated | derived | tool
  meta          json         see below
  tags          json list    ≤ 20, each ≤ 40 chars
  created_at, deleted_at
  index (owner, created_at desc), (source, created_at desc), (deleted_at)

artifact_blobs
  artifact_id   pk → artifacts.id   the bytes, in their own table so a list never loads them
  data          bytea (LargeBinary)

agent_defs
  image_artifact_id str(32) null    unversioned, like `icon`; NOT in DEF_FIELDS
```

`meta` shapes (all optional keys; the API validates size ≤ 8 KB of JSON):

- generated: `{provider, model, prompt, params: {size|aspect, quality},
  seed, cost_usd, duration_ms, reference_ids: [..], tool: "image_gen"}`
- derived: `{parent_id, operation: "markup" | "crop"}`
- tool: `{tool: <name>}` (a generic tool that wrote a file)
- upload: `{}`

Caps are settings: `artifacts_max_bytes` (8 MiB, the `session_blob_max_bytes`
precedent), `artifacts_total_max_bytes` (2 GiB; a write past it is a 507 with
a message the Studio shows), `artifacts_prune_days` (30: the dispatcher hard-
deletes soft-deleted rows older than that, in the retention loop that already
prunes transcripts), `image_gen_agent_per_hour` (10, per owner),
`image_gen_daily_usd` (5.00 total across all callers, from `meta.cost_usd`).

Seeds (mark-gated `_ensure_*` steps in `init_db`, the wiki pattern): the
`#art` open Relay channel (topic "every generated image, as a card"); the
`artist` agent row (below); a migration sweep that appends `artifacts` to
every existing agent's grants with `changed_via="migration"` (the quota
pattern), because the tool is meant to be as ambient as `relay`.

## Trust boundaries and guards

- **Bytes are never interpreted except by Pillow, for images**, with
  `Image.MAX_IMAGE_PIXELS` set explicitly (50 MP) so a decompression bomb is
  a 413, not an OOM. Mime is sniffed from magic bytes (png/jpeg/webp/gif;
  everything else is `application/octet-stream` unless a small allow-list of
  text types — `text/plain`, `text/markdown`, `text/csv`, `application/json`
  — is *claimed* and the bytes decode as UTF-8). Filenames are flattened to
  `[A-Za-z0-9._ -]`, ≤ 120 chars.
- **Serving.** `GET /api/artifacts/{id}/content` sets
  `X-Content-Type-Options: nosniff`, `Cache-Control: private, max-age=31536000,
  immutable` (content is immutable per id), and `Content-Disposition: inline`
  only for `image/png|jpeg|webp|gif`; everything else, SVG and HTML included,
  is `attachment`. `/thumb` is always an inline raster. No route ever reflects
  a client-supplied mime.
- **Identity.** `owner` is the caller's participant from the token; an agent
  cannot save as someone else and its write carries its `run_id` (the
  `tickets._run_of` rule: an agent token with no run is a 403). Reading is
  open to `READ_ROLES`; deleting is owner, `agents_edit` holders, or admin.
- **Untrusted text.** A prompt, a name, a tag is model- or human-supplied
  text: it is flattened and capped where it enters a `#art` card
  (`tickets.one_line`), escaped where it enters a tool result the model reads,
  and rendered through the sanitised Markdown everywhere else.
- **Budget and spend.** `POST /api/artifacts/generate` counts the owner's
  `source=generated` artifacts in the last hour → 429 over
  `image_gen_agent_per_hour` (humans are not metered); sums `meta.cost_usd`
  since local midnight → 402 over `image_gen_daily_usd` for everyone, with
  one system row per day in `#art` (the Tickets budget-notice pattern). The
  cost is the model registry's estimate; the artifact records it.
- **Third parties see only what the caller can see.** Reference artifacts
  are fetched by the API with the caller's own read scope before they are
  handed to the executor; at most 4, images only.
- **Keys** exist only in the executor subprocess's env for the duration of a
  call (design 12); the API never sees a provider key, and the model never
  sees a provider response — only the artifact.
- **Egress** is unchanged: the executor already may reach any host on 443
  (there is no per-host allow-list on the platform; a future egress proxy
  would be a separate design).

## The executor's file sink (a generic capability, not an image feature)

`services/tool-executor/executor.py` gains two directories per call, both
inside a per-call temp dir that is deleted afterwards:

- `TOOL_IN_DIR` — files the caller passed as `files_in: [{name, mime, b64}]`
  in the `/run` body (≤ 4 files, ≤ 8 MiB each); the tool reads them by name.
- `TOOL_OUT_DIR` — anything the tool writes there comes back in the `/run`
  response as `files: [{name, mime, b64, meta}]` (≤ 8 files, ≤ 8 MiB each,
  mime sniffed by the executor; an optional sidecar `<name>.meta.json` is
  merged into `meta` and deleted). Stdout keeps its 256 KiB text cap: the
  sink is how a tool returns something that is not text.

Two manifest additions in `toolregistry.py`: `internal: true` (the broker's
scan skips the tool — it is callable only by the API; `image_gen` is the
first) and `timeout_seconds` ceiling raised from 120 to 300 (image
generation polls; Kyle's client used 180 s). The broker's `CustomTool`
timeout becomes `manifest timeout + 30`. The `test_executor.py` env-
minimalism assertion gains the two new keys. Netpol: `allow-tool-executor`
ingress admits the api as well as the broker; the api Deployment gets
`AP_EXECUTOR_URL`.

## `tools/image_gen` — the port

`tools/image_gen/{tool.yaml, run.py, models.json, requirements.txt (empty),
test_run.py}`. `run.py` is `claude-ttrpg/tools/imagegen.py` reduced to a
library-shaped tool: stdlib only (`urllib`), `args = json.load(sys.stdin)`,
`action: generate | models`.

- `models.json` is the registry, one entry per real API id:
  `{id, provider, label, price_usd, sizes: [...] | aspects: [...], qualities:
  [...] | null, edits: bool, default: bool}`. Seed it from the ttrpg
  registry, verified against the providers' current docs at build time:
  OpenAI `gpt-image-2.5-flare` (default; "Flare for fast, high-quality
  everyday image generation"), `gpt-image-2.5-sunburst` (editing precision),
  `gpt-image-2`, `gpt-image-1-mini`; Gemini `gemini-3.1-flash-image`,
  `gemini-3.1-flash-lite-image`, `gemini-3-pro-image`,
  `gemini-2.5-flash-image`; BFL `flux-2-pro`, `flux-2-max`, `flux-2-flex`,
  `flux-2-klein-9b`, `flux-2-klein-4b` (Kyle's bulk-portrait workhorse),
  `flux-1-kontext-pro`, `flux-1-kontext-max`, `flux-1.1-pro`. `edits: true`
  where a reference image is accepted (OpenAI `/v1/images/edits`; Gemini an
  `inlineData` part; BFL `input_image` on FLUX.2 and Kontext). The API reads
  the same file to build the Studio's model list.
- `generate` args: `model, prompt, size ("WxH") | aspect ("16:9"), quality,
  seed?, references?: [filenames in TOOL_IN_DIR]`. Writes one image to
  `TOOL_OUT_DIR/image.<ext>` plus `image.meta.json`
  `{provider, model, seed, cost_usd, duration_ms, params}`; prints a one-line
  JSON summary. Providers as in `imagegen.py`: OpenAI `POST
  /v1/images/generations` (`b64_json`) or `/v1/images/edits` (multipart,
  `image[]`) when references are given; Gemini `generateContent` with
  `responseModalities: ["TEXT","IMAGE"]`, `imageConfig.aspectRatio`, an
  `inlineData` part per reference; BFL `POST /v1/{model}` then poll
  `polling_url` every 1.5 s to a 180 s deadline, `Ready → result.sample`,
  `Content Moderated / Request Moderated → clean error`. HTTP 401/403 →
  "API key invalid", 404 → "model drift", 429 → "provider quota"; exit 1
  with the message (the executor relays it; nothing is ever fabricated).
- `models` action: the registry with `configured: bool` per provider from
  the presence of its env key.
- `tool.yaml`: `internal: true`, `infra.secrets: [openai-api-key,
  gemini-api-key, bfl-api-key]` (a missing block degrades to "provider not
  configured", never an error at load), `timeout_seconds: 180`.
- `test_run.py`: the ttrpg suite adapted — every provider path with
  monkeypatched `urllib`, size↔aspect bridging, the error mapping, the sink
  files and sidecar.

Secret blocks (`secrets/<name>/secret.yaml`): `openai-api-key`
(`OPENAI_API_KEY`, probe `GET https://api.openai.com/v1/models`, Bearer),
`gemini-api-key` (`GEMINI_API_KEY`, probe
`GET https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}`),
`bfl-api-key` (`BFL_API_KEY`, script verify: `GET
https://api.bfl.ai/v1/get_result?id=00000000-0000-0000-0000-000000000000`
with `x-key`; 401/403 → invalid, anything else → valid). None `required`.
They are declarations in git, so they reach the site through the sync of
`main` — which is why the plan pushes them first and opens `/secrets` for
Kyle while the rest is built.

## API

```
GET    /api/artifacts?kind=&owner=&source=&q=&tag=&limit=&before=   metadata only (+ thumb_url, content_url)
POST   /api/artifacts                    multipart (file, name?, tags?, meta?) or JSON {name, mime?, text | content_b64, meta?, tags?}
GET    /api/artifacts/stats              {count, bytes, total_cap, generated_this_month, spend_this_month_usd, spend_today_usd, daily_cap}
GET    /api/artifacts/models             [{id, provider, label, price_usd, sizes|aspects, qualities, edits, configured, default}]  (registry × secret status)
POST   /api/artifacts/generate           {model, prompt, size|aspect?, quality?, seed?, reference_ids?[], name?, tags?} → artifact (sync; ≤ 210 s)
GET    /api/artifacts/events             SSE: created | deleted | agent_image (the tickets/wiki feed shape)
GET    /api/artifacts/{id}               metadata
GET    /api/artifacts/{id}/content       bytes (see Serving)
GET    /api/artifacts/{id}/thumb         raster thumb, images only (404 otherwise)
PATCH  /api/artifacts/{id}               {name?, tags?}
DELETE /api/artifacts/{id}               soft delete
PUT    /api/agents/{name}/image          {artifact_id | null}  admin, agents_edit holders, or the agent itself
```

`generate` is the one place a generation happens: budget and spend checks →
reference fetch (caller scope, images only, ≤ 4) → `POST executor /run
{tool: image_gen, args, files_in, caller}` → the first returned file becomes
an artifact (`source: generated`, `meta` from the sidecar plus `prompt`,
`reference_ids`) → a `#art` card → `artifacts.events`. The response is the
artifact. Executor failure text is returned as a 502 body verbatim (it is
already user-facing and never a traceback).

The nginx in front of the API gains `client_max_body_size 16m` on `/api/`
(today's default is 1 MiB and would 413 every upload).

## Broker tools (core, in `broker.py`, the wiki/tickets shape)

- `artifacts` (default-granted; grant list yields the participant role):
  `list(kind?, owner?, q?, limit?)`, `get(id, view=true)` — metadata as text
  plus, for an image, an `ImageContent` block (the thumb by default; the
  original when ≤ 1 MiB and `full=true`) — `save(name, text | content_b64 ≤
  256 KiB, tags?)`, `delete(id)`. Results tell the model the card syntax:
  "reference this in Relay as `[[artifact:<id>]]`".
- `image_gen` (granted explicitly; the artist holds it): `generate(model?,
  prompt, size|aspect?, quality?, reference_ids?)` → calls
  `/api/artifacts/generate` with the caller's token and returns the artifact
  line plus the ImageContent thumb, so the artist sees what it made;
  `models()` → the configured list. Both are `TOOL_HELP` entries with
  `display_name`s; both in `agentspec.PLATFORM_MCP_RELAY_TOOLS`-style grant
  lists (never `PLATFORM_MCP_TOOLS`); the facade exclusion list gains the
  content route (bytes do not belong in an MCP text result).

## Relay

`[[artifact:<id>]]` joins the chip rewrite in `components/relay/Message.tsx`
(the one that already protects code, links, images and `[[slug]]`): it
renders an **artifact card** — thumb (or a file glyph), name, owner face,
one-line provenance (`model · 3 s · $0.04`), click → lightbox, secondary link
→ `/artifacts/<id>`. Unknown id → a muted "artifact not found" chip. The
`#art` card the API posts is `[[artifact:<id>]]` followed by one flattened
line: `by <owner> · <model> · "<prompt ≤ 120 chars>"`. Cards carry
`mentions=[]` (system rows never summon).

## Agent images and the Agents page

`RelayFace` gains `image_url: string | null`; `relay_store.faces_for`
fills it from `image_artifact_id` (`/api/artifacts/<id>/thumb`), and every
existing face consumer (Relay, tickets, wiki, presence, search) shows the
picture with no further change because `Face.tsx` renders an `<img>` inside
the same hue-tinted disc when `image_url` is set, falling back to the emoji
on load error. `AgentSummary`/`AgentDefOut` carry `image_artifact_id` and
`face` so the Agents pages need no second fetch.

`/agents` renders a **card grid by default**: 96 px face/image, name,
description (two-line clamp), the status chip, the schedule/jobs line;
system agents in their own section as today. A segmented control in the
page header toggles grid ↔ table (the current table, unchanged);
`localStorage["agents.view"]` remembers it. The smoke probe for "blocked"
still passes because the card shows the same chip.

Agent detail: the header shows the 64 px face/image; the Config tab gains a
**Profile image** section outside the draft/PUT (the webhook-secrets
precedent: its own calls): current image, **Upload** (file input + drop
zone; the tickets board's `dataTransfer` pattern, reading `files`), **Choose
from artifacts** (a dialog with the image grid), **Generate** (dialog: model
select of configured models, prompt prefilled from
`Portrait of "<name>": <description>. <house style>`, Generate → preview →
Use), **Remove**. The generation is an ordinary artifact owned by the user,
so it also appears in `#art` and the Studio's strip.

## The Studio (`/studio`, in the web SPA)

Nav: a top-level **Studio 🎨** entry with child **Artifacts** (`/artifacts`),
between Wiki and Reporting in `buildPlatformNav`.

Layout (two columns ≥ 900 px, stacked below):

- **Compose** (left): model select (configured only; unconfigured providers
  listed disabled with "add key in Secrets →"), prompt textarea, size/aspect
  and quality selects driven by the chosen model's registry entry, seed
  (optional), a **reference strip** (drop zone, "pick from artifacts",
  "use result" — only when the model has `edits`), the Generate button with
  the registry's price beside it, and a stat row: images this month, spend
  this month, spend today / daily cap.
- **Stage** (right): the result at full width with a shimmer while
  generating (the request is synchronous; the UI shows elapsed seconds and
  the model), provenance below (model, size, seed, cost, duration, run link
  when an agent made it), actions: **Use as agent image** (agent picker →
  `PUT /api/agents/{name}/image`), **Iterate** (result → reference strip,
  prompt kept), **Mark up**, **Download**, **Delete**.
- **Recent** strip across the bottom: the last 24 image artifacts as thumbs,
  live via the SSE feed, click → stage; "Browse all" → `/artifacts`.

**Markup** is a mode over the stage: a canvas the size of the image with
tools pen, arrow, rectangle, text, crop; colour from the `--ds-chart-N`
cycle; undo; Cancel / Save. Save flattens to PNG (`canvas.toBlob`) and posts
a **derived** artifact (`meta.parent_id`, `operation`), which becomes the
stage result — so "circle the thing to change, then Iterate" is two clicks.
Hand-rolled (≈ 300 lines, no dependency), keyboard-reachable buttons, the
canvas labelled for a11y.

`/artifacts` is the block's page: a responsive thumb grid (files as glyph
tiles), filters (kind, source, owner, tag, search), lightbox with provenance
and the same actions, delete with confirm, the total-bytes bar against the
cap. `/artifacts/<id>` deep-links the lightbox.

The model picker groups **Codex allowance** above **API-priced models**. The
Codex choice has aspect and reference controls but no seed or API price; its
artifacts record `provider: codex`, `model: gpt-image-2`,
`billing: codex_allowance`, and `cost_usd: 0`. API spend totals retain their
original meaning.

The Codex runner still contains no OAuth secret. In broker mode it receives a
placeholder `auth.json` so the CLI enables first-party hosted tools, while
`openai_base_url` and `chatgpt_base_url` point at `codex-proxy`. The proxy
replaces every credential and exposes only the exact model, Responses
WebSocket, ImageGen, hosted MCP, plugin-catalog, and settings routes Codex
needs. The runner collects files from `$CODEX_HOME/generated_images` after a
successful turn. The upload route accepts only raster bytes from that run's
session token and only when the immutable run prompt contains the
platform-authored image specification.

## The artist

`codex-artist` is a second seeded, `system` agent with runtime `codex`, model
`gpt-5.6-luna`, and `imagegen`, `artifacts`, and `relay` access. It is the
execution engine behind the Studio's Codex choice and can also be summoned in
Relay for conversational briefs. It generates one image, uses artifact
references as visible inputs, and relies on the runner to create the artifact
id after the turn. Its system classification keeps it out of `@all` and the
daily standup while preserving Studio dispatch and direct mentions.

Seeded row `artist` (`model: sonnet`, not `system` so `@all` reaches it,
grants `image_gen, artifacts, relay`), description "Makes images on request:
portraits, avatars, scene art, icons. Summon with @artist and a brief." The
prompt carries: the job; **house styles** as named presets it may quote —
*storybook* ("warm, heroic storybook fantasy illustration; painterly colour
with clean ink linework; no text, no watermark, no border", from the RPG
work), *flat icon* ("minimalist flat vector, dark charcoal background,
subject fills 70 %, square", from the blog's agent icons), *photo*; **model
guidance** — avatars and icons on `gpt-image-2.5-flare` (Kyle's "more
cute"), bulk or cheap on `flux-2-klein-4b`, hero scene art on `flux-2-pro`,
edits with a reference on a model with `edits`; the **process** — act on a
clear brief without asking, ask one question on an ambiguous one, generate
one image, post `[[artifact:<id>]]` with one line (model, what it chose,
seed), offer one iteration, use the previous result as the reference when
asked to change something; **never claim an image exists without an
artifact id from the tool**; respect the budget error by saying so. A
`#art` welcome row explains how to summon it.

## Kafka

`artifacts.events` (3 partitions, 30 d): `artifacts.event` envelopes
`{event: created | deleted | agent_image, artifact: <metadata>, agent?}`,
published post-commit from `artifact_store` (best-effort, the wiki shape);
the API's SSE feed and the Studio's live strip consume it through the
existing `*_consumer_factory` wiring.

## Alternatives considered

- **Object storage (MinIO/PVC) instead of bytea.** Right for terabytes, wrong
  for a single-node NUC with a working `pg-backup` CronJob: bytea rides the
  backups, needs no new service, and the caps keep it honest. Revisit at the
  total cap.
- **A per-provider tool each.** Three secret bindings and three registries
  for one concept; the Studio would need to union them. One tool, one
  registry, one budget.
- **Studio as an apps-block pod.** Everything the Studio needs is a platform
  API and a component shared with `/agents` and Relay; an app pod would add
  an image, a netpol, a CI job and an empty schema. Kyle chose the SPA page.
- **Returning image bytes through the tool's stdout.** The 256 KiB text cap
  and the model-as-text path make this a hack; the file sink is the honest
  shape and works for any future file-producing tool.
- **Generating from the broker straight to the executor.** Would leave
  budget, references, storage and the card in two places; routing the
  broker's `image_gen` through the API's `generate` keeps one.

## Deferred (noted, not built)

Discord bridge attaching the image to the mirrored message; per-agent style
memory for the artist; a batch mode ("four variants"); video (BFL `flux-3`);
an egress allow-list for the executor; masks for OpenAI edits; server-side
resize on upload; artifact versions (today a markup is a new artifact with a
parent, which is enough).

## AS BUILT

Deltas from the design above, each forced by a review, a test, or a
provider's actual API (the ticked tasks in the plan record which). The
commits are the eight `feat(artifacts)` ones between `467e327` (T1) and
`6129ba0` (T13).

- **The executor sink is as designed, with three hardenings.** `/run` takes
  `files_in: [{name, mime, b64}]` (≤ 4 × 8 MiB, basenames only, the base64
  length checked before decoding, and a request-body ceiling enforced on the
  raw bytes before any JSON is parsed) and answers `files: [{name, mime, b64,
  meta}]` plus `warnings: [..]` (≤ 8 × 8 MiB; an oversized entry, a symlink or
  a non-regular file is skipped and named in `warnings` rather than failing
  the call). The sidecar is `<name>.meta.json` — for the image tool that is
  **`image.<ext>.meta.json`**, not the design's `image.meta.json`. The
  subprocess runs in its own session and the whole process group is killed
  on timeout: a forked grandchild used to keep `proc.wait()` from ever
  returning.
- **`internal: true` exempts a tool from the core-name shadow check.** The
  registry refuses a `tools/<name>/` that shadows a broker core tool; an
  internal tool is the API's to run by directory name and the broker never
  registers it, so a core `image_gen` broker tool and a `tools/image_gen/`
  directory are one feature, not a collision. The broker's scan skips
  internal tools, `help.py` leaves them off the tool-help list, and
  `mcp_names` (the grantable names) excludes them. The broker clamps a
  manifest timeout to 1..300 and forwards it plus 30 s.
- **The real BFL ids are the endpoint paths.** `flux-kontext-pro`,
  `flux-kontext-max` and `flux-pro-1.1` — not the design's
  `flux-1-kontext-pro` / `flux-1-kontext-max` / `flux-1.1-pro`. The OpenAI
  and Gemini ids are as designed; every id was checked against the
  provider's docs on 2026-09-17 and `models.json` is the place to fix drift.
  OpenAI's `gpt-image-2+` and every BFL width/height model accept custom
  sizes (`custom_size: true` in the registry, sizes snapped to a 16-pixel
  grid); `gpt-image-1-mini` is limited to its listed sizes. Gemini's
  `quality` is its `imageSize` (`1K`/`2K`/`4K`); FLUX.1 Kontext takes aspects.
- **One 165 s wall-clock budget inside the 180 s manifest.** `run.py` starts
  a budget at entry and every socket timeout is `min(60, remaining)`, so the
  provider's own reason reaches the user before the executor's SIGKILL would
  replace it with "tool timed out". Redirects are refused (a 3xx must never
  carry a key to another host), BFL's `polling_url` host is checked, response
  reads are capped at 32 MiB, and an unexpected exception is one clean stderr
  line — never a traceback in the browser.
- **The API's generate route is guarded by a reservation ledger.** A
  generation is check-then-spend with up to 210 s between the two, so
  every request arriving while one waits on the executor would read the same
  rows and pass the same caps. `_Reservations` in `image_gen_service.py`
  holds the price (and, for an agent, the one image) from the check until the
  row exists or the attempt has failed, and the caps are read against the rows
  **plus** what is reserved. It is process-local, which is exact only because
  the api Deployment pins `replicas: 1`; a second replica would reopen the
  window across replicas. The row count is still the record.
- **Everything that can refuse for the request's own sake refuses before the
  executor is called** — an unknown model, a size the registry does not list,
  a reference to a model without `edits`, a prompt over 2000 characters (the
  meta cap would otherwise refuse the row *after* the provider was paid) —
  and a registry entry with no price is refused too, so a free-looking image
  can never walk past the cap.
- **The `#art` card is `kind="event"`, not text.** The router re-parses
  `@mentions` from the body of every text row, so a prompt reading "@news
  retract that" would summon news with a fresh hop budget the moment its
  card landed. Event rows are never read for mentions — the ticket card's
  protection, borrowed whole. The row also carries a `card` payload
  (`{type: "artifact", artifact_id, owner, model, prompt}`), and the daily
  cap notice is a `kind="system"` row marked in `schema_marks`
  (`art-budget-notice-<day>`) so it is said once across processes.
- **`POST /api/artifacts/generate` requires the `image_gen` grant** for an
  agent, not the either-grant fence the store's routes share: `artifacts` is
  default-granted so any agent can keep a file, and this is the one door that
  spends money. The other `/api/artifacts/*` routes open to an agent holding
  either grant (`require_artifacts_access`) — the artist holds `image_gen`
  and needs the store its pictures land in.
- **`local_timezone`** (`America/Toronto`) is a new setting: the zone
  "today" turns in for the daily cap and the month for the stats. An unknown
  zone is a warning and UTC, never a route that cannot answer. `stats`'
  cap field is `daily_cap_usd`, not the design's `daily_cap`.
- **The api pod dials the executor through its own SPIRE tunnel.** Under
  `spire.enabled` the api Deployment carries an `executor-tunnel` ghostunnel
  client sidecar (with a startup probe) and `AP_EXECUTOR_URL` is
  `http://127.0.0.1:8301`; otherwise it is the executor Service directly.
  The executor's mTLS front door gains `--allow-uri` for the api's SVID and
  the `allow-tool-executor` netpol admits the `api` component beside the
  broker.
- **The models route reads secret status off the event loop, cached.** The
  k8s store is a synchronous call per block; `provider_status` caches for
  60 s and pushes the store call to a thread. `configured` is true when a
  block is `valid` or `unprobed` (how most keys arrive).
- **The secret probes.** The Gemini key travels in the `x-goog-api-key`
  header, not `?key=` in the URL (a query-string key lands in access logs, and
  newer-format keys reject it). The BFL script polls `get_result` for a
  sentinel task id with `x-key`: 401/403 → rejected, 404/422/200 → accepted,
  and **anything else (429, 5xx, a WAF page) is inconclusive and fails
  closed** — marking a key valid through an outage would let a dead key look
  green; the verifier heartbeat re-runs it every 600 s.
- **The broker's `generate` waits 240 s**, not the `_metered` default of
  20 s, which would have reported "unreachable — retry shortly" while the API
  kept spending; a timeout now tells the model not to retry blindly and to
  check `artifacts list` first. Attached pictures are bounded on the bytes
  that arrive (thumb ≤ 150 KiB, `full=true` ≤ 1 MiB, an image mime), refusals
  become plain capped strings with a hint per status (429 wait, 402 tomorrow,
  502 change the prompt), and audit rows record sizes, never bytes.
- **Agent images: the `agents_edit`-holder / self rule, plus grant parity.**
  `PUT /api/agents/{name}/image` is admin, an `agents_edit` holder, or the
  agent itself — "itself" meaning the run token's run (`_run_of`), and the
  run's agent must hold `artifacts` or `image_gen`, as the store's own routes
  ask. Deleting or pruning an artifact undresses every agent wearing it and
  publishes an `agent_image` clear (`artifact: null`), which the SSE feed
  passes through. The column is deliberately not a foreign key, so the two
  delete paths do by hand what a cascade would.
- **The web shares one SSE feed per tab.** The grid and every mounted
  `[[artifact:…]]` card subscribe to one `EventSource`
  (`components/artifacts/feed.ts`), started by the first subscriber and
  closed by the last, with the board's widening retry; a card that rendered
  before its row landed no longer stays "not found" for the session, and a
  deleted picture empties its cards. Byte URLs are used only when they start
  with `/api/artifacts/`. The nav label is "Studio" without the emoji.
- **The artist has a step 0.** The daily `#standup` `@all` reaches it (it is
  not `system`), so the prompt's first rule is that a summons that is not an
  image request gets one line or silence, never a generation. The three house
  styles, the model guidance, the never-claim rule and the budget rule are as
  designed; the row's first change-log entry is `changed_via: seed`. The
  profile-image dialog's prefilled prompt is `Portrait of "<name>":
  <description>. flat, friendly avatar, square, centred, no text`.
- **Markup** is at the picture's natural pixels with a 16 MP working-canvas
  cap (larger sources are scaled), Esc and ⌘Z, swatches from the chart tokens,
  a black-and-white crop marquee, and a Save that refuses a zero-op (a
  byte-identical copy labelled crop); click-only arrows and rectangles are
  dropped. `/studio/<id>` stages an artifact and `/studio?ref=<id>` preloads a
  reference; a result that lands after the reader has left the page no longer
  pulls them back.
- **`artifacts_default_grant`** is a fifth default-grant knob beside relay's,
  tickets', wiki's and quota's, and `POST /api/agents` honours it; the sweep mark is
  `artifacts-default-grant-v1`.

Deferred, noted in the plan's Deferred section and not built: the Discord
bridge attaching the image to a mirrored message; per-agent style memory for
the artist; a batch mode; video; an egress allow-list for the executor; masks
for OpenAI edits; server-side resize on upload; artifact versions (a markup is
a new artifact with a parent, which is enough). Review lows left as they are:
`Image.MAX_IMAGE_PIXELS` is set process-wide at import; `PATCH` publishes no
event; `TOOL_SCRATCH_DIR` is not validated at executor boot; the broker suite
stubs `fastmcp`; a generate holds up to ~43 MB of base64 references in
memory; `K8sSecretStore.get` is synchronous under the secrets page (the
models route wraps it, the page does not); `PUT …/image` publishes on an
unchanged value; the regenerated SDK's `AgentDefOut.from_dict` pops `face`
unconditionally, so the facade must restart after the deploy; `/artifacts`'
owner/tag pickers are drawn from the filtered rows; the Studio stat row reads
`$x / $0.00` when the daily cap is zero; and the pre-existing
`secretverify.py` probe whose DNS resolution is not bounded by its timeout.
