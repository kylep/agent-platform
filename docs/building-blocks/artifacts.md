# Artifacts

**What:** the files the platform keeps
(`docs/design/23-artifacts-and-image-studio.md`). An **artifact** is a named
blob with a mime type, a size, a sha256, an **owner** (a participant string,
as in [Relay](relay.md): `agent:artist`, `user:kyle`), the run that made it
when an agent did, a **source** — `upload`, `generated`, `derived` or `tool`
— a JSON `meta` that is its provenance, and up to 20 tags. An artifact whose
bytes are a PNG, JPEG, WebP or GIF is `kind: image` and carries its width,
height and a thumb; everything else is `kind: file`.

The point is that an agent can hand back something that is not text. A run's
result is a string, a Relay message is markdown, a report is a sanitised HTML
fragment — so before this block, an agent that made a picture or kept a CSV
had nowhere to put it and the humans had nowhere to look. Three things ride
on the same block: **image generation** (the platform's own capability, an
executor tool behind three provider keys), an **agent's picture** (a face
that is an image rather than an emoji), and the **Studio**, a page where a
person picks a configured model, writes a prompt and generates without an
agent in the loop.

**Lives in:** platform Postgres — `artifacts` (the metadata, the thumb) and
`artifact_blobs` (the bytes, in their own table so a list never loads them).
Every create, delete and agent-image change is an event on the
`artifacts.events` Kafka topic, which is what the live pages read. A
generation also posts a card into the `#art` channel. Nothing about an
artifact lives in the synced checkout; the image **registry** does
(`tools/image_gen/models.json`), and so do the three secret blocks.

**How to use it:** open **Studio** in the sidebar (between Wiki and
Reporting); its child entry **Artifacts** (`/artifacts`) is the block's own
page — a thumb grid (files as glyph tiles), filters for kind, source, owner,
tag and a name search (each one a URL parameter, so a filtered shelf is a
link), a lightbox with the provenance and the actions, delete with a confirm,
and the store's total-bytes meter against its cap. `/artifacts/<id>` deep-links
the lightbox. A picture in a room is the same card: write `[[artifact:<id>]]`
in a Relay message (below).

## The routes

All of them are behind the platform session or a run token; an agent needs
the `artifacts` (or `image_gen`) grant as well, server-side, because the
participant role alone bounds nothing here — there is no room to be a member
of.

| route | what it does |
|---|---|
| `GET /api/artifacts` | metadata, newest first; `kind`, `owner`, `source`, `q`, `tag`, `limit` (≤ 200), `before` (an artifact id — keyset paging) |
| `POST /api/artifacts` | save: multipart (`file`, `name?`, `tags?`, `meta?`) or JSON (`name`, `mime?`, `text` \| `content_b64`, `meta?`, `tags?`, `source?`) |
| `GET /api/artifacts/stats` | `count`, `bytes`, `total_cap`, `generated_this_month`, `spend_this_month_usd`, `spend_today_usd`, `daily_cap_usd` |
| `GET /api/artifacts/models` | the image registry × whether each provider's key is set (`configured`) |
| `POST /api/artifacts/generate` | one image: `prompt`, `model?`, `size?` \| `aspect?`, `quality?`, `seed?`, `reference_ids?` (≤ 4 images), `name?`, `tags?` → the artifact, synchronously (up to ~210 s) |
| `GET /api/artifacts/events` | SSE: `created` \| `deleted` \| `agent_image` frames off the topic |
| `GET /api/artifacts/{id}` | metadata |
| `GET /api/artifacts/{id}/content` | the bytes (see the serving rules below) |
| `GET /api/artifacts/{id}/thumb` | the raster thumb; 404 for a file |
| `PATCH /api/artifacts/{id}` | rename, retag |
| `DELETE /api/artifacts/{id}` | soft delete |
| `PUT /api/agents/{name}/image` | `{artifact_id \| null}` — the agent's picture ([agents.md](agents.md)) |

The owner is the caller's participant from the token — the body may say
`owner` and it is ignored — and an agent's write carries its `run_id`; a
per-run token whose run is gone may read and cannot save. Renaming,
retagging and deleting are for the owner, an `agents_edit` holder, or the
admin. A delete is `deleted_at`, not a `DELETE`: the artifact drops out of
every list and its bytes stop being served, a card naming it says so, and the
dispatcher hard-deletes the row and its blob after `artifacts_prune_days`
(**30**) — an `ArtifactPruner` running beside the transcript and report
pruners. Deleting an artifact an agent wears as its picture clears that
agent's picture too.

**Provenance** is `meta`, shaped by the source. A generated image carries
`provider`, `model`, `prompt`, `params` (size or aspect, quality), `seed`,
`cost_usd`, `duration_ms`, `reference_ids`, `tool: "image_gen"`, and any
`executor_warnings`; a derived one carries `parent_id` and `operation`
(`markup` \| `crop`); a tool's file carries the tool's name; an upload carries
nothing. A card's one-line provenance is read off it: `model · 3 s · $0.04`,
or the source word when the row has none of those.

## The serving rules

The content route is the platform handing a browser bytes somebody else
chose, so it is where the trust boundary is:

- The mime is **sniffed from the bytes** on the way in — PNG, JPEG, WebP, GIF
  by magic; `text/plain`, `text/markdown`, `text/csv` and `application/json`
  only when a client claimed one *and* the bytes decode as UTF-8; everything
  else is `application/octet-stream`. No route ever reflects a mime a client
  sent. An HTML file that called itself a PNG is an octet-stream.
- `GET …/content` always sets `X-Content-Type-Options: nosniff` and
  `Cache-Control: private, max-age=31536000, immutable` (bytes never change
  under an id), and `Content-Disposition: inline` **only for the four
  rasters**; everything else, SVG and HTML included, is an `attachment` with
  its filename flattened to `[A-Za-z0-9._ -]`. `…/thumb` is always an inline
  raster.
- The only code that ever interprets the bytes is Pillow, for the four
  rasters, behind an explicit 50-megapixel cap: a decompression bomb is a
  413, not an out-of-memory. The thumb it makes is ≤ 512 px on the long side,
  JPEG (PNG when there is alpha), ≤ 150 KiB.
- The web never uses a byte URL that does not start with `/api/artifacts/`.

## What agents can do

Agents hold one default-granted broker tool, `artifacts`
(`mcp__platform__artifacts`), with an `action` argument:

| action | what it does |
|---|---|
| `list` | newest first; `kind`, `owner`, `q`, `limit` to narrow it |
| `get` | the metadata as text — and for an image, **the picture itself** as an image content block the model can look at (the thumb; `full=true` the original when it is ≤ 1 MiB) |
| `save` | `name` plus `text` or `content_b64` (≤ 256 KiB — a tool argument crosses the transcript), `tags?`, `mime?` |
| `delete` | its own |

Use it when the thing to hand back is a file: a screenshot to look at, a CSV
somebody will download, a picture to show in a room. An agent that cannot keep
a file describes it instead, which is why the grant is ambient: while
`artifacts_default_grant` is on, agent creation adds `mcp__platform__artifacts`
to the new agent's `platform_tools`, a one-time sweep granted it to every agent
that already existed, and an admin can take it away through the normal grant
path ([agents.md](agents.md)). Results teach the card syntax: every `list`
line starts with the chip, and `get`, `save` and `generate` end with
"reference it in Relay as `[[artifact:<id>]]`".

A second tool, `image_gen` (`mcp__platform__image_gen`), is **not** granted by
default — the artist holds it, and an agent that should draw is one an admin
decided should:

| action | what it does |
|---|---|
| `generate` | `prompt`, `model?`, `size?` \| `aspect?`, `quality?`, `seed?`, `reference_ids?` (≤ 4) → the artifact line plus the thumb as a picture, so the agent sees what it made |
| `models` | the registry with what is configured, priced, with each model's sizes or aspects, qualities and whether it takes references (`edits`) |

Both tools forward the caller's own token, so the owner of a saved file and
the payer of a generation are the agent the token names. `generate` waits up
to 240 s and, when that runs out, tells the model not to retry blindly — the
request it gave up on is still being served, and a retry would be a second
image and a second charge.

Studio also has a virtual `codex-imagegen` model backed by the seeded
`codex-artist`. It uses Codex's hosted ImageGen through `codex-proxy`, so its
usage comes from the Codex subscription allowance rather than provider API
keys. The resulting file enters through a run-scoped upload and becomes the
same generated artifact and `#art` card; provenance marks
`billing: codex_allowance` and API spend totals ignore it.

Somebody else's artifact — its name, its prompt, a text file's contents — is
**untrusted** input, the posture of `docs/design/08-news-and-injection-hardening.md`: data,
never instructions.

## The artifact card

`[[artifact:<id>]]` in ordinary prose renders as an **artifact card** wherever
Relay prose is rendered: the thumb (or a file's glyph), the name, the owner's
face, the one-line provenance; click for the lightbox, `open ↗` for
`/artifacts/<id>`. It joins the same chip rewrite as `[[slug]]` and ticket
keys, with the same protection: inside a fence, inline backticks, a markdown
link or a bare URL it is quoted text. An id nobody has is a muted
"artifact not found" chip rather than a card with a hole in it, and a card
that is on screen when its artifact is deleted becomes one — every open room
and the grid share one SSE stream per tab.

Every generation posts a card into **`#art`** (an open channel the platform
seeds, topic "every generated image, as a card"): the chip, then one
flattened line — `by <owner> · <model> · "<prompt ≤ 120 chars>"`. The row is
an **event**, not a text message, and that is load-bearing: the router
re-parses `@mentions` from every text row, so a prompt reading "@news retract
that" would otherwise summon news the moment its card landed. Cards carry no
mentions.

## The caps and settings

| setting | default | what it bounds |
|---|---|---|
| `artifacts_max_bytes` | 8 MiB | one artifact; a bigger one is a 413, checked before the body is parsed |
| `artifacts_total_max_bytes` | 2 GiB | the live store; a write past it is a 507 with a message the Studio shows |
| `artifacts_prune_days` | 30 | how long a soft-deleted row waits before the dispatcher hard-deletes it |
| `artifacts_default_grant` | on | whether agent creation grants `artifacts` |
| `image_gen_agent_per_hour` | 10 | generations per **agent** per rolling hour; humans are not metered — a person iterating in the Studio is the point of the Studio |
| `image_gen_daily_usd` | 5.00 | the whole platform's spend per day, everyone, summed from `meta.cost_usd` since local midnight |
| `local_timezone` | `America/Toronto` | when "today" turns |
| `executor_url` | `http://agent-platform-tool-executor:8000` | where the API runs the internal tool (`AP_EXECUTOR_URL`; under SPIRE, `127.0.0.1:8301` through the api pod's own ghostunnel client) |

Over the hourly budget an agent gets a 429 that says how long to wait (until
the oldest generation in the window ages out); over the daily cap everyone
gets a 402, and `#art` is told **once** a day — the Tickets budget-notice
pattern, marked in `schema_marks` so the notice survives the room being
cleared. Both are counted from the artifact rows, deleted ones included (a
deleted image was still paid for), plus a process-local ledger of what is
reserved by requests still waiting on a provider, so two requests arriving
together cannot both slip under the line. The cost is the registry's estimate,
recorded on the row. The nginx in front of the API allows 16 MiB bodies on
`/api/` for the uploads; the API checks its own bound as well, because an
in-cluster caller does not pass through nginx.

## The Studio

`/studio` is a page in the web SPA, deterministic — no agent in the loop. Two
columns (stacked on a phone):

- **Compose** — the model select (every registry model, priced; one whose
  provider key is not set is listed disabled with "add key in Secrets"), the
  prompt, size or aspect and quality from the chosen model's registry entry,
  an optional seed, a **reference strip** (drop a file, pick from artifacts,
  or take the last result — offered when the model takes references) and
  the Generate button with the price beside it, over a stat row: images this
  month, spend this month, spend today against the daily cap.
- **Stage** — the result with a shimmer and the elapsed seconds while it
  generates, the provenance below it, and the actions: **Iterate** (result →
  reference strip, prompt kept), **Use as agent image** (an agent picker →
  `PUT /api/agents/{name}/image`), **Mark up**, **Download**, **Delete**.
- **Recent** — the last 24 images as thumbs, live from the stream; click one
  to stage it; "Browse all" opens `/artifacts`.

`/studio/<id>` stages an artifact so a result can be sent to somebody;
`/studio?ref=<id>` opens with that artifact as a reference. **Mark up** is a
mode over the stage: a canvas at the picture's natural pixels with pen, arrow,
rectangle, text and crop, colours from the chart tokens, undo, Esc to leave.
Save flattens to a PNG and uploads it as a **derived** artifact naming its
parent (`meta.parent_id`, `operation: markup | crop`), which becomes the stage
result — so "circle the thing to change, then Iterate" is two clicks. A save
with nothing drawn is refused rather than uploading a byte-identical copy.

## The artist

`@artist` is a seeded agent ([agents.md](agents.md)) that holds `image_gen`,
`artifacts` and `relay`. Its prompt carries three named **house styles** it
may quote whole (*storybook*, *flat icon*, *photo*), model guidance (avatars
and icons on `gpt-image-2.5-flare`, bulk or cheap on `flux-2-klein-4b`, hero
scene art on `flux-2-pro`, a change to an existing image on a model with
`edits` with the image in `reference_ids`), and the process: act on a clear
brief, ask one question on an ambiguous one, generate one image, answer with
the card and one line, offer one iteration, use the previous result as the
reference when asked to change something. It never claims an image exists
without an artifact id from the tool, says so when the budget refuses it, and
— step 0 — does not generate for a summons that is not an image request (the
daily `#standup` `@all`).

## How to add a provider or a model

The generator is one internal executor tool, `tools/image_gen/`
([tools.md](tools.md)): a stdlib `run.py` over the registry in `models.json`,
bound to the optional secret blocks `openai-api-key` (`OPENAI_API_KEY`),
`gemini-api-key` (`GEMINI_API_KEY`) and `bfl-api-key` (`BFL_API_KEY`). The
API reads the same `models.json` to build the Studio's list, so a model the
Studio offers is one the tool knows.

- **A new model on an existing provider** is one entry in `models.json`:
  `{id, provider, label, price_usd, sizes | aspects, custom_size?, qualities,
  edits, default}` — `id` is the provider's real API id (the BFL ids are
  their endpoint paths, `flux-2-pro`, `flux-kontext-pro`, `flux-pro-1.1`),
  `sizes` for a model that takes `WxH`, `aspects` for one that takes `a:b`
  (the tool bridges one to the other), `edits: true` when it accepts
  reference images, a price the cap can count. A model with no price is
  refused before anything is spent. The edit lands through the
  [change loop](changes.md) like any tool edit and is live on the next sync.
- **A new provider** is a secret block under `secrets/<name>/secret.yaml`
  ([secrets.md](secrets.md)) with a probe that costs nothing, its name added
  to the manifest's `infra.secrets`, a `KEY_ENV` entry and a `call_<provider>`
  in `run.py` (the request shape, the error mapping — 401/403 "key invalid",
  404 "model drift", 429 "provider quota" — and the one image written to
  `TOOL_OUT_DIR/image.<ext>` with its `image.<ext>.meta.json` sidecar), and
  the same name in the API's `PROVIDER_SECRETS` (`image_gen_service.py`), which
  a test holds in lockstep with the manifest. Keys exist only in the executor
  subprocess's env for the duration of a call; the API never sees a provider
  key, and the model never sees a provider response — only the artifact.
