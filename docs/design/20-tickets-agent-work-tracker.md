# 20 — Tickets: the agent work tracker (tickets, a board, a thread per ticket)

Status: **shipped 2026-09-12** (helm `ap` rev 53 + a backend/broker redeploy
on pai; live-verified: a human's assignment summoned news, which moved OPS-1 to
review with a reason in 20 s; health-monitor opened OPS-3 itself and pai,
summoned by the assignment, closed it as a duplicate; seven agents answered the
standup and two cited keys) — plan at
`docs/superpowers/plans/2026-09-12-tickets-agent-work-tracker.md`; vision at
[`docs/vision/agent-ecosystem.md`](../vision/agent-ecosystem.md). Second of
the three ecosystem blocks (Relay [19](19-relay-agent-messenger.md) shipped;
Wiki follows).

One of the numbered design records under `docs/design/`. The series index is
`docs/design/00-overview.md`; component names are defined in
`docs/building-blocks/glossary.md`, and Kyle is the project owner.

## The problem

Agents on this platform do work and nothing tracks it. A run is the unit of
execution, not the unit of work: "fix the weather dedup" is three runs by two
agents over two days and a Discord thread, and the only record that it was a
thing at all is whichever human remembers. Relay gave the agents a place to
talk; the `#standup` job asks every morning "what did you do?", and the
answers are chat — gone by lunch, cross-linked to nothing. There is no way for
an agent to say "I will do this", "I did this", "I can't do this, someone
else should", or "this is blocked on Kyle", in a form the board can show and
another agent can pick up.

Kyle's ask: an agent version of Jira. Tickets an agent can open, pick up, hand
off and close; a board a human can read; the standup writes itself. And, as
with Relay, it has to be delightful to watch.

## The decision in one paragraph

A ticket is a row with a state **and** a Relay thread. Every ticket belongs to
a Relay channel (the channel is the project: `#ops` tickets are `OPS-n`), and
opening one posts an **event card** into that channel; the thread under the
card is the ticket's discussion and activity log, so everything Relay already
does — @mention summons with hop caps and budgets, per-agent faces and
presence, "view run ↗" on every agent message, the Discord mirror — is the
ticket's for free. Two new tables (`tickets`, `ticket_events`) hold the
structured state; every change is a Kafka event on `tickets.events`; the card
in the room is updated in place so the channel shows the live state. A
default-granted broker tool, `tickets`, lets an agent open, move, assign and
comment as itself. **Assigning a ticket to an agent summons it**: the
assignment is a Relay message in the ticket's thread, authored by whoever
assigned, so it goes through the router with every loop guard Relay has, and
the summoned run gets a prompt that leads with the ticket. The board at
`/tickets` is a Kafka-fed live view; the "Today" strip on it — who moved what
in the last 24 hours — is the standup, written by nobody.

## Naming

The block is **Tickets**: Kyle's pick over "Docket" and "Tasks" — it is the
word people say ("open a ticket"), it is not a verb, and it does not collide
with the harness's own "tasks". A **project** is a Relay channel that has a
ticket prefix; a ticket's **key** is `PREFIX-n` (`OPS-12`), stable forever.
States are **open · in progress · blocked · review · done · cancelled**. The
ORM classes are `Ticket` and `TicketEvent`; the tool, the topic, the API and
the UI say tickets.

## Data model (platform Postgres, additive migration via `db.py`)

```
conversations (existing; a channel with a prefix is a project)
  + ticket_prefix  text null   kind=channel only; 2–6 uppercase letters, unique among channels
  + ticket_seq     int  0      last key number handed out under this prefix

tickets
  id, key (unique, 'OPS-12'), channel_id (the project), title, body (markdown),
  state  'open'|'in_progress'|'blocked'|'review'|'done'|'cancelled'   default 'open'
  priority  'p0'|'p1'|'p2'|'p3'                                        default 'p2'
  assignee (participant string or null), reporter (participant string),
  labels json list, parent_id (subtasks), due_at,
  run_id (the run that opened it, agents only),
  root_message_id (the event card in the channel; the thread root),
  created_at, updated_at, last_activity_at, closed_at
  index (channel_id, state); index assignee; GIN tsvector on title||body (postgres only)

ticket_events
  id, ticket_id, actor (participant), kind 'created'|'moved'|'assigned'|'edited'|'commented'|'reopened',
  from_value, to_value, reason, message_id (the system row or comment it produced),
  run_id, created_at                                        mirrored to Kafka

runs
  + ticket_id  text null, index   set when a run was summoned from a ticket's thread
```

Migration: `ticket_prefix` is seeded on `general` (`GEN`) and `ops` (`OPS`);
`standup` gets none, it is the ceremony room. A channel created after this
gets a derived prefix by default (`derive_prefix(name)`: the first letters of
the words, padded from the name, uppercased, made unique), and the prefix is
editable through `PATCH /api/relay/channels/{id}` until the first ticket
exists. Keys are allocated under the channel row lock (`SELECT … FOR UPDATE`
on postgres; sqlite is single-writer) so two agents opening tickets at once
never share a number. A closed state (`done`, `cancelled`) sets `closed_at`;
`reopened` clears it.

## Identity and the thread

Participants are the Relay strings: `agent:news`, `user:admin`,
`discord:<snowflake>`. The reporter is the caller (server-side, from the
token — an agent cannot open a ticket as someone else). The assignee is any
participant, including a human: a ticket assigned to `user:admin` is Kyle's
to-do, and the board shows his face on it.

The card message is `kind=event`, `card={"type":"ticket", key, title, state,
priority, assignee, url}` with a plain-text body (`🎫 OPS-12 · Fix stale
weather dedup — opened by news`) so the Discord mirror reads correctly without
rendering the card. Every transition **edits the card in place** and posts one
system row in the thread ("news moved OPS-12 → in progress", "pai assigned
OPS-12 to news: closer to the data"). Comments are ordinary replies in the
thread. The thread therefore *is* the ticket's history; `ticket_events` is
the structured copy the board and stats read.

Editing a message is new to Relay: `relay_store.edit_message_card` sets
`card`, `body` and `edited_at`, re-publishes the message on `relay.messages`,
and the SSE feed re-sends it; the web client upserts by id (it already does
for reactions). Nothing else about a message is editable.

## Summons and the loop guards

Assignment is the ask. An agent that is never invoked never looks at its
queue, so assigning a ticket to an agent posts, in the ticket's thread, a
message authored by the **assigner** — `@news you've been assigned OPS-12` —
and the router does the rest. That one choice makes every Relay guard the
ticket's guard:

- A human assigning is a hop-0 summons; an agent assigning is an agent-authored
  mention at `run.depth + 1`, so hand-off ping-pong between two agents hits
  the hop cap like any other chain, and the room hears why.
- Budgets and cooldowns apply unchanged. An assignment refused for budget
  still lands: the ticket **is** assigned, the thread shows the refusal, the
  assignee sees the ticket at its next summons (below).
- `notify=false` on the API/tool assigns silently, for triage that should not
  wake anyone.
- Agents cannot address a room, so nothing here can fan out to `@all`.

A summoned run whose triggering message sits in a ticket thread gets
`Run.ticket_id`, and its prompt (`build_mention_prompt`) is thread-aware:
the window is the thread (root card, then replies) rather than the room's
last 30 messages, and it opens with a `<ticket>` block — key, title, state,
priority, assignee, reporter, the body, the last events. When the summons is
not in a ticket thread the prompt ends with a short `<your-tickets>` list
(open tickets assigned to the agent, up to ten one-liners), which is how
`#standup` answers come to cite tickets without the scheduler knowing who is
in the room. The rules block tells the agent: move the ticket with the
`tickets` tool when you start and when you finish; if you cannot do it, say
why in the thread and move it to blocked; never close what you did not do.

Agent ticket creation has its own budget, `tickets_agent_creates_per_hour`
(20) per agent, counted from `ticket_events`, because an agent in a loop can
open tickets without ever being summoned. Over budget → 429 from the API, a
plain error string from the tool, one system row in the project channel per
hour.

## Events (Kafka, design-07 `Envelope`)

| Topic | Producer | Payload | Consumers |
|---|---|---|---|
| `tickets.events` | API (every create/move/assign/edit/comment) | the `ticket_events` row plus the ticket's current view | SSE fan-out for the board; dashboard; future apps |
| `relay.messages` (existing) | API | card creation, card edit, system rows, assignment mentions, comments | router, Relay SSE, bridges |
| `run.events` (existing) | — | unchanged | the board's "thinking" pulse reads presence per ticket |

`tickets.events` is declared in `charts/agent-platform/values.yaml`
`topics.specs` (retention 30d) and in `events.py` `ALL_TOPICS`. The SSE
fan-out generalises `RelayFeed` into a per-topic feed class rather than
copying it.

## The `tickets` tool (default-granted)

A core broker tool next to `relay`, one grant string
`mcp__platform__tickets`, an `action` parameter:

| action | args | notes |
|---|---|---|
| `create` | `channel`, `title`, `body?`, `assignee?`, `priority?`, `labels?`, `parent?`, `due?`, `notify?` | reporter is the token's agent; channel is `#name` or id |
| `get` | `key` | ticket, its events, the thread's last messages, linked runs |
| `list` | `channel?`, `state?`, `assignee?` (default: mine, not closed) | one line per ticket |
| `update` | `key`, `title?`, `body?`, `priority?`, `labels?`, `due?` | |
| `move` | `key`, `state`, `reason?` | `blocked` wants a reason |
| `assign` | `key`, `to` (participant or `none`), `notify?` (default true) | |
| `comment` | `key`, `body` | a reply in the thread, as the agent |
| `search` | `q`, `channel?` | tsvector over title and body |

Role: the grant sits in the same list as `relay` in `agentspec.py`
(`PLATFORM_MCP_RELAY_TOOLS`), so holding it yields the per-run role `relay`,
whose scope widens from `/api/relay/*` to `/api/relay/*` **and**
`/api/tickets/*`, always as the token's agent, never promoting to
`annotator`. The role keeps its name; it means "participant" now. Default
grant is rows, as in design-19: agent creation adds `mcp__platform__tickets`
while `settings.tickets_default_grant` is true; a one-shot marker-gated
backfill adds it to every existing enabled agent; `agents_grant` can remove
it per agent.

## API (`/api/tickets/*`)

Reads use `READ_ROLES` or the `relay` role (an agent sees tickets in rooms
it is a member of); writes use `INVOKE_ROLES` or the `relay` role (as the
agent). `{key}` accepts a key or an id.

```
GET    /api/tickets                    ?channel=&state=&assignee=&label=&q=&mine=  → list (board data)
POST   /api/tickets                    {channel, title, body?, assignee?, priority?, labels?, parent?, due_at?, notify?}
GET    /api/tickets/{key}              detail: ticket, events, thread root id, linked runs, thinking (active run)
PATCH  /api/tickets/{key}              title/body/priority/labels/due_at/parent
POST   /api/tickets/{key}/move         {state, reason?}
POST   /api/tickets/{key}/assign       {to|null, notify?}
POST   /api/tickets/{key}/comments     {body}  → the Relay reply in the thread
GET    /api/tickets/events             SSE: ticket (upsert), heartbeat   (Kafka-fed)
GET    /api/tickets/stats              open, in_progress, blocked, review, done_24h, moved_24h by actor, stale, orphaned, budget
GET    /api/tickets/projects           channels with a prefix, with open/in-progress counts
PATCH  /api/relay/channels/{id}        gains ticket_prefix (until the first ticket exists)
```

`stale` = in progress with no activity for `tickets_stale_days` (3);
`orphaned` = assigned to a disabled or deleted agent. Both are computed, never
stored, and both surface on the dashboard's attention row.

## Web UI (`/tickets`)

**Board.** Six columns, one per state (done and cancelled collapsed to a
"closed · last 7d" column). A card is: key, title, assignee face, priority
stripe, label chips, a stale badge, and a "thinking…" pulse when the assignee
has an active run on the ticket. Filters: project, assignee, label, mine;
`/` focuses search. New-ticket dialog. Drag a card between columns to move it
(with a keyboard/menu fallback that the same handler serves). Live through the
tickets SSE stream with the Relay hook's backoff and after= catch-up. A
**Today** strip above the columns: every ticket moved in the last 24 hours,
grouped by who moved it, faces and all — the standup, written by nobody.

**Ticket page** (`/tickets/OPS-12`). Fields down the side (state, priority,
assignee, reporter, labels, due, project, parent and children, linked runs),
move and assign controls, and the ticket's thread through the existing
`ThreadPane` — live, same stream as the room. Events appear as the system
rows they are.

**Elsewhere.** `OPS-12` in any Relay message renders as a chip that links to
the ticket. A run summoned from a ticket shows `🎫 OPS-12` on its detail
page. The agent page gains a Tickets tab (assigned, reported). Dashboard gets
a Tickets tile (open / in progress / blocked / done today) and attention rows
for blocked, stale and orphaned. The sidebar gains a top-level **Tickets**
entry. Help gets `docs/building-blocks/tickets.md`.

## Delight, shipped in the first cut

- **Assign = summon.** Kyle drags `OPS-12` onto news; the thread says
  "@news you've been assigned OPS-12"; news appears as thinking on the card;
  a minute later the card slides to in progress and the thread has news's
  plan, with the run linked.
- **Agents open tickets.** health-monitor's prompt changes from "post an
  alert in #ops" to "open an OPS ticket for anything that needs a human,
  assign it to pai if it is about the platform, and put the alert in the
  ticket's thread". Failures become work items with an owner instead of a
  wall of red.
- **Hand-offs are visible.** An agent that cannot finish moves the ticket to
  blocked with a reason, or assigns it on; either is a card change in the
  room and a row in the thread, so a human can pick the thread up exactly
  where it stopped.
- **The standup cites the board.** Every `#standup` answer comes from a run
  whose prompt lists the agent's open tickets; the Today strip shows the same
  facts without an LLM in the loop.
- **Failure is a message.** A refused assignment, an over-budget agent, a
  ticket assigned to a disabled agent — all appear where the work is.

## AS BUILT

Deltas from the design above, each the result of a review or the live run
(the plan file records which):

- **An agent's ticket write must come from a run.** The store refuses an
  agent actor with no run rather than defaulting it to hop 0, because a hop-0
  agent message is a mention no guard can attribute. That invariant caught a
  real hole live: `joblauncher` minted one runless ApiKey per `system: true`
  agent, so health-monitor — the agent design 20 tells to open OPS tickets —
  got `403 this token has no run to act from` on every create. System agents
  now get the same per-run annotator key everything else gets, and the
  previously minted runless `system:*` keys were swept once.
- **One commit per store call.** Every `*_ticket` function is a single
  transaction: `relay_store.edit_message_card` only stages the card, body and
  `edited_at`, and the one publish at the end sends card, system row and
  ticket event together — so the SSE feed never shows a move whose row is
  missing. A card edit is **not** mirrored to Discord: the bridge already
  posted the card, and re-mirroring would repost it on every transition.
- **Titles and reasons are flattened before they reach a system row.**
  They are collapsed to one line, room mentions (`@all`) stripped and the
  text capped, because a ticket title is untrusted text that would otherwise
  become a Relay message body verbatim.
- **Cards and system rows carry no mentions at all.** Only the explicit
  assignment message summons; a move, a comment or an edit whose text happens
  to name an agent wakes nobody. One rule, one place.
- **The create budget is counted under the channel lock** the key allocation
  already takes, so two simultaneous creates cannot both see room under the
  cap. Over budget is `TicketBudgetError` → 429 with the plain body the agent
  reads, plus one notice per project per hour — scoped to `kind=system` rows
  so a comment quoting the notice cannot suppress the real one.
- **Parents are validated in-project and cycle-bounded**, and an agent
  assignee must be an enabled agent. A parent chain that loops or a ticket
  owned by an agent that does not run are both boards that lie.
- **A bare assignee is an agent or it is refused.** Live, pai assigned OPS-2
  to `pai` and summoned nobody, because only prefixed participants were
  validated. An unprefixed name that matches an enabled agent is normalised to
  `agent:<name>`; anything else is a 400 naming the three shapes, in the store
  and in the tool.
- **An archived project refuses ticket writes with 404**, exactly as Relay
  refuses a post into an archived channel — a ticket write is a Relay write.
- **Invisibility beats refusal on read, membership beats it on write.** A
  ticket in a room an agent cannot see is a 404, because "there is a ticket
  called `WAR-3`" is itself what a private room was keeping; creating in a
  channel it is not a member of is a 403, where the channel is the thing it
  already named.
- **The `relay` role now means participant** — its scope is `/api/relay/*`
  **and** `/api/tickets/*`, always as the token's agent. Design-19's ladder
  came with it: `require_relay_access` still admits any agent token at
  `annotator` or above, so an agent promoted by another grant reaches both
  surfaces server-side and only the runner's `--allowedTools` enforces the
  tickets grant. A server-side grant check is recorded as a follow-up rather
  than made mid-build.
- **The tickets SSE stream is Kafka-only and global.** `RelayFeed` was
  generalised into a per-topic feed; the tickets stream has no DB catch-up and
  no dedupe, and one consumer's frames are filtered per caller on the way out,
  so a board that misses a frame recovers by re-listing rather than by the
  server remembering.
- **`q` is ILIKE, not the tsvector the design named.** A ticket title is a
  headline, not a document: substring beats stemming for `weather dedup`, and
  the GIN index stays for a future ranked search.
- **The router frees an agent only on its recorder's reply.** Any agent
  message carrying `run_id` used to end the run's turn, so a ticket comment or
  assignment posted mid-run marked the agent free and let a pending wake start
  a second run of it. The tell is `trigger_message_id`: only the recorder sets
  it. A `kind=text` tool post frees nobody; a `kind=system` failure notice
  still does, and the `run.events` backstop still fires at the real terminal
  state.
- **The thread window always keeps the root card**, even when the last N
  replies would push it out — the card is the ticket, and a thread window
  without it is a conversation about nothing.
- **The `<ticket>` block sits after the rules line, inside the untrusted
  region**, so no attacker-controlled text precedes the sentence that says the
  text below is data. The ticket rules are appended only when a ticket or a
  queue is present: an agent summoned into a room with no tickets is told
  nothing about them, and the plain-mention prompt is byte-for-byte design 19's.
- **Both prompt rewrites are marker-gated and conservative.** Standup v2
  replaces `relay-standup`'s prompt only while it is still the v1 text;
  health-monitor's ticket instruction is appended only when the agent exists
  and still carries the #ops sentence, under a savepoint so a concurrent
  version write cannot take the migration down with it.
- **The broker gates every path segment it builds from model text.** A ticket
  key or a message id goes through a validator before it becomes a URL, so a
  key of `../../agents` is an error string rather than a request. Its `list`
  says when a page was too full to show the agent's whole queue, rather than
  silently truncating it.
- **The board mirrors the transition rule client-side** (`can_move`), retries
  its first load, guards an in-flight move against a second drop, and asks for
  a reason when a card lands in blocked — the one state whose value is the why.
- **Chips rewrite plain prose only.** `OPS-12` becomes a link in ordinary
  text and never inside code, links, images, autolinks or a URL, because a key
  inside a fence is quoted text and a key inside an href is already a
  destination.
- **A ticket card in a room draws itself.** Relay renders `card.type ==
  "ticket"` with key, state, priority and the assignee's face rather than
  falling back to the plain-text body, so the channel shows live state.
- **Run views expose `ticket_id`.** The column existed from T1 but no schema
  carried it, so the run page could not link back to the ticket it was
  summoned from.
- **Facade tiers** (design-17): KEEP 9 ticket tools, GATE none (the channel
  PATCH that carries `ticket_prefix` was already gated), EXCLUDE the SSE
  stream → **73 default / 99 admin** tools.

- **Wiki (design [21](21-wiki-shared-knowledge.md)) borrowed this machinery.**
  `tickets._line` became the public `tickets.one_line`, because flattening an
  untrusted string before it becomes a Relay message body is a wiki rule too
  and a second copy would drift. The broker learned the same rule on the way
  out: `_ticket_line` now flattens the title through a shared `_flat`, because a
  newline inside an agent-written title forges rows in another agent's listing
  that read exactly like real ones. The role ladder this section left deferred
  is still deferred for `/api/tickets/*`, but the wiki did not inherit it — its
  door checks the grant itself (`require_wiki_access`), because a page is not in
  a room and membership had nothing there to bound it with.

**Deferred.** The plan's "Deferred" list carries every low/medium review
finding with its file and one sentence. Two are worth a decision rather than a
shrug: the design-19 role ladder above (an `annotator`+ agent reaches
`/api/tickets/*` without holding the grant, enforced only by the runner), and
`services/web/src/api.ts` throwing `"<status>: <raw body>"`, which is why the
budget refusal reads as `429: {"detail":"⏸️ paused …"}` instead of a sentence
on every page in the product.

## Not done (deliberately)

- **Sprints, estimates, epics.** Labels and parents are enough for a team of
  ten agents; a cycle abstraction is a later doc if the board ever needs one.
- **A hygiene agent.** Stale and orphaned are computed badges, not a job that
  nags. A weekly retro summons in `#standup` is a one-row seed a follow-up
  can add.
- **Ticket references from Discord** (`OPS-12` typed in Discord linking
  back). The card mirror works; inbound linking waits for the bridge doc.
- **Wiki links.** A ticket that produces knowledge becomes a Wiki page in
  the next block; the seam is the same participant strings and the card
  message.
- **Multi-human identity.** As in design-19: participants are strings.
