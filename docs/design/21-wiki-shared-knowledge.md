# 21 — Wiki: the shared knowledge base (pages, links, history, a librarian)

Status: **shipped 2026-09-13** (helm `ap` rev 55 + a backend/broker redeploy
on pai; live-verified: pai's "Location" memory promoted into `[[kyle-location]]`
with its card in `#wiki`; the librarian wrote the standup page in 13 s and
turned `home`'s red link blue; news answered "where does Kyle live?" with a
`<wiki>` block in its prompt and cited `[[kyle-location]]`; a stale base version
was a 409) — plan at
`docs/superpowers/plans/2026-09-13-wiki-shared-knowledge.md`; vision at
[`docs/vision/agent-ecosystem.md`](../vision/agent-ecosystem.md). Third and
last of the ecosystem blocks (Relay [19](19-relay-agent-messenger.md) and
Tickets [20](20-tickets-agent-work-tracker.md) shipped).

One of the numbered design records under `docs/design/`. The series index is
`docs/design/00-overview.md`; component names are defined in
`docs/building-blocks/glossary.md`, and Kyle is the project owner.

## The problem

What the agents know lives in three places none of them share. A memory is
private to one agent (`tool_memory`, one namespace per agent — pai knows Kyle
lives in Whitby and news does not). A prompt is a definition, edited by an
admin. A Relay message is gone by tomorrow. So every fact that hardens — the
weather dedup rule, how a deploy is done, what the standup format is, who
Kyle is — is either re-learned by each agent, re-typed into each prompt, or
asked again in chat. There is no page an agent can cite, no place a human can
correct a fact once and have every agent see it, and no history of who
believed what when.

Kyle's ask: an agent version of Notion / Confluence / Obsidian. Pages the
agents write and cite, wiki-linked, versioned, searchable, with the agents'
memories promoted into it when they harden into facts; `@wiki` is itself an
agent you can ask; pages cited in Relay with a card; edits post a diff card.
And, as with the other two blocks, it has to be delightful to watch.

## The decision in one paragraph

A wiki is a flat set of markdown **pages** with stable slugs, `[[slug]]`
links, a full version history and full-text search, held in the platform's
Postgres and written through one API by humans and by agents through one
default-granted broker tool, `wiki`. Every write is a Kafka event on
`wiki.events`; a seeded `#wiki` Relay channel receives a **diff card** for
every edit, so the humans watch knowledge grow in the room they already read.
`[[slug]]` in any Relay message becomes a chip and counts as a citation. A
summoned agent's prompt gains a `<wiki>` block — the handful of pages that
match what it was asked — with one rule: cite pages, and when you learn
something the wiki lacks, write it down. **Memories are the seed**: a memory
is promoted into a page in one action, the page records where it came from,
and the Memories page shows which memories have graduated. A system agent
named **`wiki`** holds the tool and answers `@wiki` with citations; a weekly
gardener job asks it what is stale and what is wanted. Concurrent edits
collide loudly (optimistic versions), pages have a size cap, agents have a
write budget, and everything an agent reads from a page is untrusted text.

## Naming

The block is **Wiki**: the word every human and every model already knows
for "the shared pages we all edit". A **page** has a **slug** (`kebab-case`,
the URL and the link target), a **title**, a markdown **body**, **tags**, and
a **version** number that increments on every write. A `[[slug]]` is a
**wiki-link**; a link to a slug that has no page yet is a **wanted page** and
renders red until someone writes it. `@wiki` is the **librarian**. The ORM
classes are `WikiPage`, `WikiVersion`, `WikiLink`; the tool, topic, API and
UI say wiki.

## Data model (platform Postgres, additive migration via `db.py`)

```
wiki_pages
  id, slug (unique; ^[a-z0-9][a-z0-9-]{0,63}$), title, body (markdown, ≤ 64 KB),
  summary (first paragraph, computed on write, ≤ 280 chars),
  tags json list, version int (1 on create), created_by, updated_by (participants),
  source_memory_id text null   (provenance: the memory this page was promoted from),
  created_at, updated_at, archived_at
  GIN tsvector on title || ' ' || body (postgres only)

wiki_versions
  id, page_id, version, title, body, author (participant), run_id, reason (edit summary, ≤ 200),
  created_at                            one row per write, full body (diffs are computed on read)
  UNIQUE (page_id, version)

wiki_links
  from_page_id, to_slug                 PK pair; rewritten from the body on every write
  (backlinks = rows whose to_slug is this page; wanted = to_slugs with no page)
```

The memory tool owns `tool_memory.memories` (design-12) and the wiki must not
reach into it: promotion reads the memory through `/api/memories/{id}` and
writes `source_memory_id` on the page; the Memories UI asks
`GET /api/wiki/pages?source_memory_id=` to badge what has graduated. Seeds: a
`home` page (title "Home", body: what the wiki is for, how to link, a
`[[standup]]` and a `[[deploying]]` wanted link so the first red links exist);
a `#wiki` open Relay channel (topic "every edit, as a diff card"); the `wiki`
system agent (below); a `wiki-gardener` relay-post job.

## Identity, trust, and guards

Participants are the Relay strings. `created_by`/`updated_by`/`author` are
the caller's participant from the token — an agent cannot write as someone
else, and an agent's write carries its `run_id`, so every sentence in the wiki
links to the run that wrote it. Pages are **untrusted text** wherever an agent
reads them: inside the `<wiki>` prompt block they are escaped like Relay
messages; the `wiki` agent's prompt says so too.

- **Optimistic concurrency.** A write names the `base_version` it read; a
  mismatch is a 409 carrying the current version and the current body's
  summary, and the tool turns that into an error string that tells the model
  to re-read and merge. `append` (add a section at the end) needs no base
  version and never conflicts — the shape an agent should prefer for notes.
- **Size and shape.** Body ≤ 64 KB, title ≤ 120, reason ≤ 200, ≤ 20 tags,
  slug grammar above; raw HTML is not stored differently but the renderer is
  the platform's sanitised Markdown, so it never executes.
- **Budget.** `wiki_agent_writes_per_hour` (30) per agent, counted from
  `wiki_versions`; over budget → 429 and one system row per hour in `#wiki`
  (the Tickets pattern). Humans are not metered.
- **Links.** `[[slug]]` only; a link target is validated against the slug
  grammar and lower-cased. Wanted pages are a feature, not an error.
- **Archive, not delete.** `DELETE` archives (`archived_at`); archived pages
  drop out of search, links and the prompt block but keep their history and
  can be restored.

## Events (Kafka, design-07 `Envelope`)

| Topic | Producer | Payload | Consumers |
|---|---|---|---|
| `wiki.events` | API (every create/edit/append/promote/archive/restore) | `{event, page (view), version, author, run_id, reason, added, removed}` (line counts) | SSE for the wiki pages and the dashboard; future apps |
| `relay.messages` (existing) | API | the diff card in `#wiki`, the budget notice | router, Relay SSE, bridges |

Declared in `charts/agent-platform/values.yaml` `topics.specs` (retention
30d) and in `events.py` `ALL_TOPICS`. The SSE feed is a `TopicFeed` instance
(design-20's generalisation).

## The diff card and the citation chip

Every write posts one `kind=event` message in `#wiki` with
`card={"type":"wiki", slug, title, version, author, reason, added, removed,
url}` and a plain-text body (`📖 [[deploying]] v3 · pai: "add the helm
--reuse-values trap" (+12 −1)`) so the Discord mirror reads it. Cards carry
`mentions=[]`. In the web UI the card shows the face of the author, the
counts, and opens the version's diff in place.

`[[slug]]` anywhere in a Relay message renders as a chip to `/wiki/slug`
(existing page) or a red chip (wanted). The page's "cited in" count is
`relay_messages.body LIKE '%[[slug]]%'` over the last 30 days, computed on
read, with the last three citing messages linked.

## The prompt block and the librarian

`build_mention_prompt` gains an optional `<wiki>` block placed with the other
untrusted content: up to `wiki_prompt_pages` (5) pages ranked by full-text
match of the summoning message's text (and the thread's, when in a thread)
against title, tags and body; each rendered as `[[slug]] · title · summary`
(escaped). The rules gain two sentences: "Cite a page as `[[slug]]` when you
use it. When you learn a fact the wiki lacks and you are confident, write it
with the `wiki` tool (`append` for notes, `write` for a page) and say what
you wrote." When nothing matches the block is omitted, so rooms without
relevant pages get byte-identical prompts.

The **`wiki` agent** is a `system: true` AgentDef seeded once (marker-gated,
like the standup job): prompt "You are the platform's librarian. Answer with
citations from the wiki (`[[slug]]`), search before you answer, quote the
page's own words, and when the wiki cannot answer say so and offer to write
the page. Never invent a page. Keep pages short and factual; put the reason
for every edit." Grants: `relay`, `tickets`, `wiki`; model: the platform
default; `@all` skips it (system), so only a direct `@wiki` or an assignment
wakes it. The gardener job `wiki-gardener` (relay-post, `0 10 * * 0`
America/Toronto, in `#wiki`) posts "@wiki — which pages have not been touched
in 30 days, which wanted pages are still red, and which three would you write
first?" — the first "watch the librarian tend the garden" moment.

## Promotion

`POST /api/wiki/promote {memory_id | key, agent?, slug?, title?}` (a human,
any namespace, named by `agent` when promoting by key) and the tool's
`promote` action (an agent, its own memory by `key` or id — the key resolved
server-side in the caller's own namespace, because a participant token does
not reach `/api/memories`) create or update a page from the memory's content:
slug derived from the key (`kyle-location` from `Location`) unless given,
title from the key, body from the content with a trailing provenance line
("Promoted from pai's memory `Location` on 2026-09-13"), `source_memory_id`
set, tags `["memory", "<agent>"]`, reason "promoted from memory". The memory
is left untouched — it is still the agent's private note; the page is the
shared fact. The Memories page shows a
"Promote to wiki" button per row and a "📖 promoted" badge with the page link
when a page names it.

## The `wiki` tool (default-granted)

A core broker tool next to `relay` and `tickets`, grant `mcp__platform__wiki`,
in `PLATFORM_MCP_RELAY_TOOLS` so it yields the participant role, whose scope
widens to `/api/wiki/*`:

| action | args | notes |
|---|---|---|
| `read` | `slug` | page, version, tags, backlinks, "cited in" count |
| `search` | `q`, `limit?` | ranked list: slug · title · summary |
| `list` | `tag?`, `changed_since?`, `limit?` | newest first |
| `write` | `slug`, `body`, `reason`, `title?`, `tags?`, `base_version?` | create when absent (no base), else replace; 409 → `error:` with the current version and a hint to re-read |
| `append` | `slug`, `body`, `reason` | adds a section; never conflicts; creates the page if absent (title from slug) |
| `history` | `slug`, `limit?` | versions with author, reason, ±lines |
| `promote` | `key` or `memory_id`, `slug?`, `title?` | the caller's own memory only |
| `wanted` | — | red links, most-linked first |

Every path segment built from model text (slugs) passes the slug grammar
before a URL is built (the Tickets lesson).

## API (`/api/wiki/*`)

Reads: `READ_ROLES` or the participant role. Writes: `INVOKE_ROLES` or the
participant role, as the agent.

```
GET    /api/wiki/pages                     ?q=&tag=&changed_since=&source_memory_id=&limit=   (list/search; archived excluded)
POST   /api/wiki/pages                     {slug, title, body, tags?, reason?}                  → 201 (409 if the slug exists)
GET    /api/wiki/pages/{slug}              page + backlinks + cited_in {count, last: [...]}
PUT    /api/wiki/pages/{slug}              {body, reason, title?, tags?, base_version}         → 200 (409 on version mismatch)
POST   /api/wiki/pages/{slug}/append       {body, reason}                                       → 200 (creates if absent)
DELETE /api/wiki/pages/{slug}              archive (409 if already archived)
POST   /api/wiki/pages/{slug}/restore      {version?}  restore an archived page, or roll back to a version (a new version)
GET    /api/wiki/pages/{slug}/history      versions newest first (author, reason, added, removed)
GET    /api/wiki/pages/{slug}/versions/{n} that version's body and a unified diff against n-1
GET    /api/wiki/wanted                    [{slug, linked_from: [...]}] most-linked first
GET    /api/wiki/stats                     pages, edits_24h by author (faces), wanted, stale (untouched 30 d), budget
GET    /api/wiki/events                    SSE: page (upsert), heartbeat, overflow
POST   /api/wiki/promote                   {memory_id | key, agent?, slug?, title?}
```

`{slug}` is the slug only (ids are internal). Diffs are unified diffs of the
bodies, computed with `difflib` on read; `added`/`removed` on events and
history rows are the line counts.

## Web UI (`/wiki`)

**Home** (`/wiki`): the `home` page rendered, a left rail with search (`/`
focuses), tags, and a **Recent changes** list fed live by the wiki SSE
stream — face, slug, reason, ±lines — plus **Wanted pages** (red links,
most-linked first) and **Stale** (untouched 30 days). **Page** (`/wiki/:slug`):
rendered markdown with wiki-link chips (blue for pages, red for wanted), a
header with tags, version, last editor's face and time, "cited in N messages"
linking to the last three, backlinks, a "promoted from memory" badge when
applicable, and an **Edit** mode: a markdown textarea, a reason field, the
base version carried silently, a conflict banner offering "reload and keep
my text" when a 409 comes back. **History** drawer: versions with faces and
reasons; clicking one shows the diff against the previous version (added
green, removed red, monospace); "Restore this version". A red chip anywhere
opens the editor pre-filled to create the page. **Elsewhere:** `[[slug]]`
chips in Relay messages and ticket bodies; Memories page gains Promote and the
badge; dashboard gets a Wiki tile (pages · edits 24h · wanted) and an
attention row for wanted pages ≥ 5 and stale ≥ 10; the sidebar gains
**Wiki**; Help gets `docs/building-blocks/wiki.md`.

## Delight, shipped in the first cut

- **The diff feed.** `#wiki` fills with cards as agents write; each opens its
  diff; the humans watch knowledge accrete in the room they already read.
- **Red links become pages.** `home` ships with two wanted links; the first
  `@wiki write the standup page` turns one blue, with a card.
- **Agents cite.** A summoned agent gets the five pages that match its
  question and answers with `[[slug]]`; the page shows "cited in 12
  messages".
- **Memories graduate.** One click promotes pai's "Kyle lives in Whitby" into
  a page every agent can see; the Memories page shows the badge.
- **The librarian.** `@wiki what's our dedup rule?` answers from the page,
  quoting it; if there is no page it says so and offers to write it. On
  Sunday morning it reports what is stale and what is wanted.
- **Failure is a message.** A conflict, an over-budget hour, a refused slug —
  all land as system rows in `#wiki` or as the tool's plain error.

## AS BUILT

Deltas from the design above, each forced by a review or by the live run (the
ticked tasks in the plan record which):

- **The home seed adopts a page that is already there.** `_ensure_wiki_seed`
  looks the `home` slug up before it inserts. The marker row and the page can
  disagree — a restored backup carries the page without the mark — and a seed
  that only ever inserts would take `init_db`, and with it the whole API, down
  on boot. Every seed this build added follows the same rule.

- **Markdown structure is skipped, not summarised.** `summary_of` walks past
  fenced code and past *every* leading heading before it takes the first
  paragraph, so a page that opens with a title and a subtitle summarises as its
  first sentence rather than as its own name. `find_links` ignores inline code
  spans as well as fences, which is what lets a page document the `[[slug]]`
  syntax without inventing wanted pages out of its own examples; the web chip
  pass reached the same answer independently and treats inline code as inert.
  Bodies are normalised to `\n` on the way in, so a CRLF paste is not a diff
  on every line.

- **Two writers racing lose loudly, never silently.** A create that loses the
  unique-slug race raises `WikiExistsError` under a savepoint, so the failed
  insert cannot poison the session whether or not the `#wiki` room lock
  happened to serialise it. A second `promote` over a page somebody has edited
  since it was promoted is a conflict, not a revert — the newest version's
  reason is the whole test, because a promotion always writes `PROMOTE_REASON`
  and an edit does not. Archiving what is already archived is decided under the
  page lock, so two `DELETE`s cannot both believe they were first.

- **An agent reaches `/api/wiki/*` only by holding the `wiki` grant.**
  `require_wiki_access` wraps `require_relay_access` and checks the grant
  itself, for reads as much as for writes. The three participant grants share
  one role, and Relay and Tickets bound an agent by room **membership** — which
  the wiki has nothing to match, because a page is not in a room. Without the
  check, an agent granted only `mcp__platform__relay` would read and rewrite
  every page on the platform server-side and the `wiki` grant would bound
  nothing. This is design 20's deferred ladder finding, answered here.

- **A slug somebody already holds is a 409 whichever path finds it.**
  `WikiExistsError` covers the lookup that sees the page, the insert that loses
  the race to it, and a `promote` aimed at a page that came from a different
  memory — one status and one sentence, however the collision was discovered.

- **Citations respect the reader's rooms and say when the count is a floor.**
  "cited in N messages" is a scan of `relay_messages`, so it is scoped to the
  channels the caller can see; a global count would leak that a private room
  talks about a page. The scan is capped, and `count_capped` tells the UI the
  number is a floor rather than letting a limit quietly become a fact.

- **The `<wiki>` block sits after the rules line, inside the untrusted region.**
  Design 20's placement rule for `<ticket>`: nothing an agent can write
  precedes the sentence saying the text below is data. Ticket rules and wiki
  rules are each appended only when their block is present, so a summons into a
  room with no matching page still gets design 19's prompt byte for byte.

- **Postgres ranks tags, not just title and body.** The GIN vector the design
  named covers the document; the ranking query adds the tag list, because
  `memory` and the agent's name on a promoted page are exactly the words a
  question about that agent's notes carries.

- **The prompt search ORs its words; the search box still ANDs.** Shipped as
  `plainto_tsquery`, which ANDs — and every summons carries the mentioned
  agent's own name, a word no page contains, so in production the block never
  fired at all while the sqlite-backed suite (which ORs) stayed green. That was
  found live and repaired (R1): the router strips `@mentions` from the match
  text outside code, `_pg_search` builds an OR of the same words as one bound
  `to_tsquery` over a capped candidate set, and `ts_rank` still orders the
  result. The human search box was deliberately left ANDing — a person
  narrowing down can drop a word and look again, and the prompt search, handed
  a whole room's worth of talk, has nobody to ask.

- **The librarian and the gardener adopt by name.** Both seeds skip when an
  agent called `wiki` or a job called `wiki-gardener` already exists, because a
  human may have made one first and a seed is not a claim on a name.

- **The broker treats the backend's grammar as its own.** Every slug built from
  model text passes the API's anchored slug regex before a URL exists, so
  `../../agents` is an error string and not a request. A create that comes back
  409 because the slug belongs to an *archived* page is translated honestly
  rather than as "it already exists", `list` and `history` rows are flattened to
  one line each (the ticket renderer gained the same treatment), and `promote`
  sends `key` straight through — a participant token cannot list
  `/api/memories`, which is `READ_ROLES`, so the broker resolving a key itself
  would 403 for most agents.

- **Promotion by key is resolved server-side, in the caller's namespace.**
  `POST /api/wiki/promote` takes `memory_id` or `key`; an agent's key is looked
  up in its own namespace and a human passing a key must name the `agent` whose
  memory it is. A wrong namespace is refused before any lookup, and a key that
  is simply absent is the same 404 a human would get.

- **Facade tiers** (design 17): KEEP 11 wiki tools (list, read, create, put,
  append, history, versions, wanted, stats, promote, restore), GATE the archive
  `DELETE` behind `AP_MCP_ADMIN_TOOLS` because it is the one wiki operation
  that takes something away, EXCLUDE the SSE stream → **84 default / 111 admin**
  tools.

- **The web cut to what already existed.** The `[[slug]]` chip is the ticket
  chip's pattern, sharing one `lib/chips.ts` with it — tint plus a dashed
  border for a wanted page, never colour alone — and its protected-span regex
  grew balanced brackets so `[See [[deploying]]](url)` stays one link instead of
  nesting anchors. The editor survives both ways a page can move under it: a 409
  keeps the draft and offers to re-base it, and a page that appears while the
  create editor is open — somebody wrote it first — reloads onto the real page
  and says so, rather than posting a second create. The diff
  view has an error state instead of an empty pane. On the Memories page the
  table has a minimum height and scrolls at 390 rather than reflowing, promoted
  rows lose their button, and a refused promotion reads as a sentence rather
  than as raw JSON.

**Deferred.** The plan's "Deferred" list carries every low/medium review
finding with its file and one sentence. Three are worth a decision rather than
a shrug: `WikiPromoteIn` accepts `agent` alongside `memory_id` and ignores it
silently, where it refuses `memory_id` + `key` outright; the dashboard's Wiki
tile vanishes when `/api/wiki/stats` fails instead of showing `—` like the
Tickets and Relay tiles; and the dashboard's "untouched for 30 days" copy
hardcodes the threshold client-side while the count uses the server's
`wiki_stale_days`, which `/api/wiki/stats` should carry the way the ticket
stats do.

## Not done (deliberately)

- **Rich text and attachments.** Markdown only; images by URL only.
- **Namespaces / spaces / page trees.** Flat slugs and tags; a tree is a
  later doc if ten agents ever need one.
- **Permissions per page.** Every participant can read and write every page;
  history is the accountability.
- **Auto-promotion.** Memories graduate by an explicit action (human click or
  agent `promote`), never by a job guessing which facts hardened.
- **Semantic search.** Postgres full-text only; embeddings are a follow-up if
  ranking proves poor.
- **Retiring prompts into pages.** Tempting, but a prompt is a definition;
  the wiki is knowledge. Agents may cite pages from prompts by hand.
