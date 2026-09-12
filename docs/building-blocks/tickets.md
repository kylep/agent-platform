# Tickets

**What:** the board the platform's work is tracked on
(`docs/design/20-tickets-agent-work-tracker.md`). A **ticket** is one piece of
work — a title, a state, an owner, a thread — and agents open, pick up, hand
over and close them the same way people do. Every ticket lives in a **project**,
which is not a new kind of object: a project *is* a [Relay](relay.md) channel
with a key prefix, so `#ops` is the OPS project and a ticket opened there gets
the key `OPS-12`.

The point is that work an agent does has somewhere to live between runs. A run
is a thing that happened; a ticket is a thing that is still true tomorrow —
with the argument about it attached, in the room the humans are already in.

**Lives in:** platform Postgres — `tickets` and `ticket_events` beside Relay's
own tables, plus a `ticket_id` on the run. Nothing about a ticket lives in the
synced checkout: it is runtime state, not configuration. Every write is an
event on the `tickets.events` Kafka topic, which is what the board's live
stream reads. The card in the room is an ordinary Relay message, so a bridged
channel mirrors the card itself — but not the edits that follow, or Discord
would get the ticket again on every transition.

**Keys are permanent.** `OPS-12` is the ticket for as long as it exists,
including after it is closed, and the prefix comes from the channel's name
(`#ops` → `OPS`, with a digit appended to break a collision). The key is
case-sensitive on purpose: `ops-12` in a sentence is the word, not the ticket.

## States

Six, and the board draws five columns because the last two are the same
answer to "what is left to do":

| State | What it means |
|---|---|
| `open` | recorded, nobody has started |
| `in_progress` | somebody is on it right now |
| `blocked` | it cannot move until something else changes; the board asks for a reason and the agents are told to give one, but no move is refused for want of it |
| `review` | the work is done and wants a second pair of eyes |
| `done` | finished |
| `cancelled` | it will not be done, and that is a decision somebody made |

Reaching `done` or `cancelled` stamps `closed_at`; leaving one is a **reopen**,
recorded as its own event rather than as an ordinary move. A ticket that sits
in `in_progress` untouched for `tickets_stale_days` (**3**) is **stale**, and
one assigned to an agent the platform no longer runs is **orphaned** — both are
badges on the card and rows in the Dashboard's attention queue, because a board
that quietly rots is worse than no board.

## Assign = summon

Handing a ticket to an agent posts a real `@mention` in the ticket's thread.
The ask *is* the assignment: the agent wakes up, reads the ticket, and answers
in the same thread with a link to the run it answered from — under every guard
the Relay router already applies (hops, budgets, coalesced wakes). Untick
**Notify** to record an owner quietly instead; that is the 3am case, not the
default.

Moving a ticket is not a summons. Neither is a comment that does not mention
anybody. One rule, one place: only a mention wakes an agent, and assignment is
a mention.

**How to use it:** open **Tickets** in the sidebar. The board is five columns;
drag a card or use its *Move to…* menu (the same move either way), filter by
project, assignee, label or *only mine* — the filters live in the URL, so a
filtered board is a link you can send somebody — and press `/` to search.
**Today** above the columns is the standup nobody wrote: everyone who moved a
ticket in the last 24 hours, and how much.

A ticket's own page (`/tickets/OPS-12`) is the fields down one side and the
thread down the other — the *same* thread as the room's, read through the card
the ticket left there, so a reply typed here is a reply in `#ops`. When an
agent is working on it, its face pulses at the top of the page with a link to
the live run. `OPS-12` written in any Relay message is a chip that links here —
in ordinary prose only, never inside code, a link, an image, an autolink or a
URL, where a key is quoted text or already a destination.

## What agents can do

Agents hold one default-granted broker tool, `tickets`
(`mcp__platform__tickets`), with an `action` argument:

| action | what it does |
|---|---|
| `create` | open one in a project (`channel`, `title`, optional `body`, `assignee`, `priority`, `labels`, `parent`, `due`) |
| `get` | one ticket in full: fields, history, linked runs |
| `list` | its own unfinished queue by default; `assignee='any'` for everyone's, `state` to see closed ones |
| `update` | edit the fields (`title`, `body`, `priority`, `labels`, `parent`, `due`) |
| `move` | change the state, with a `reason` |
| `assign` | hand it over — wakes the assignee unless `notify` is off; `to='none'` unassigns |
| `comment` | say something in the ticket's thread |
| `search` | substring search over titles and bodies |

"Default-granted" is the same bargain as Relay's: while `tickets_default_grant`
is on, agent creation adds `mcp__platform__tickets` to the new agent's
`platform_tools`, and an admin can take it away through the normal grant path
([agents.md](agents.md)). An agent that cannot file what it found leaves the
finding in a transcript nobody reads.

An assignee is a participant string: `agent:news`, `user:admin`,
`discord:<id>`. A bare name that is an enabled agent is normalised to
`agent:<name>` so the hand-off actually summons somebody; any other bare string
is refused rather than stored, because an assignee that reaches nobody is a
board that lies. An agent assignee must also be enabled — that is what
**orphaned** counts.

The agents are told to move a ticket when they **start** it and when they
**finish** it, to say why with `comment` and move it to `blocked` when they
cannot, and never to close a ticket whose work they did not do. Authorship is
the token's agent, server-side: an agent cannot file as somebody else, and
there is no `reporter` field on the wire at all.

## The guards, in plain words

Anything that can open a ticket can open a thousand. Four rules stop that, and
all of them are visible on the board rather than buried in a log.

- **A creation budget.** An agent may open `tickets_agent_creates_per_hour`
  (**20**) tickets an hour. Moves, comments and assignments are deliberately
  not capped — they are how work gets finished, and an assignment already pays
  Relay's own mention budget. Over the cap, the create is refused with a 429
  the agent reads, *and* one line in the project channel per hour — so the
  humans watching the room see that an agent is looping without the board
  filling up with the evidence.
- **Membership, not role.** An agent reads and writes tickets only in the rooms
  it is a member of. A ticket in a room it cannot see answers 404 rather than
  403, because "there is a ticket called `WAR-3`" is itself something a private
  room was keeping; opening one in a channel it is not in is a 403, since the
  channel is the thing it already named. Archiving a project stops its ticket
  writes exactly as it stops its messages — also a 404.
- **The board's own transitions.** A closed ticket may only reopen; a ticket
  cannot be its own parent, and a parent chain cannot loop. A refused move is a
  409 that says what the ticket's state actually is.
- **Every change is an event.** `created`, `moved`, `assigned`, `edited`,
  `commented`, `reopened` — each one is a `ticket_events` row, a line in the
  thread, and a Kafka event. "Why is this in blocked?" is an answerable
  question, and the answer names who and when.

A ticket an agent reads is attributed, clearly **untrusted** content, the same
posture as `docs/design/08-prompt-injection.md`: what a ticket's title and body
say is data, not instruction. Somebody else's ticket cannot tell an agent to
grant itself anything.
