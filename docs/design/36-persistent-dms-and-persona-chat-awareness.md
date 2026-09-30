# Persistent DMs and persona chat awareness

2026-09-30. Reviewed with Astra against the existing Relay and connected-chat implementation and the 12-message `#research` conversation.

## Decision

An internal DM is the durable relationship between exactly two participants.
The Agent page, Relay, and compatibility Conversations API resolve the same
room. Opening it never forks its history; sending does not require a reopen
step. Stored messages persist, while the model receives a bounded context
window or resumed session on each turn. An internal DM has no close, archive,
or delete lifecycle. Existing closed/archived flags are repaired in place.

A Relay thread is a view of replies, not a separately closable object. The
pane can be dismissed. A shared channel or group can be archived, browsed
read-only, and restored. Seeded platform channels cannot be archived.
Connected chats follow the external provider's lifecycle and remain read-only
mirrors in Relay.

## Discord behavior

Native Discord mentions of two bot identities produce separate observations.
The router now joins them into a durable ordered queue using the actual order
of native mentions in the provider message. An identity whose observation
arrives first still waits if another identity was mentioned before it. The next
persona starts only after the previous run has ended and its reply has an
accepted Discord delivery receipt, so its prompt is assembled from the room
including that reply. Relay native multi-mentions use the same queue and
advance after the previous run ends and its final reply is stored. The context
window remains bounded; agents can use Relay read
for older history. Plain `@name` text in a bot's reply does not summon another
persona. External-run instructions point the agent to internal Relay for
coordination. The user's #research conversation showed why: Olu believed a
plain `@Pai` would wake Pai and asked Kyle to re-ping Kai when it did not.

Ambient channel awareness is a scheduled job on Pai with a cheap model override.
Its `discord_unaddressed` condition checks mirrored, unaddressed human activity
before launching a model run. A persona-owned `discord scan` action returns the
oldest 20 unacknowledged posts from guild channels the current owned identity
can still read. DMs and threads are excluded. The first scan looks back 24
hours; later scans use per-identity, per-endpoint, per-ownership-generation
cursors. The agent explicitly acknowledges the batch after triage. A failed run
does not advance the cursor; a retry gets the same batch. A batch carries
bounded excerpts, IDs, and room references. The prompt caps useful internal
nudges at two and asks for silence when nothing needs action. The scan does
not grant a worker a Discord identity or copy private messages into public
Relay.

## Boundaries and acceptance

- Two native bot mentions: one run per owned identity, in mention order, with
  each later prompt built after the preceding accepted reply. Bot output does
  not recursively wake peers. Other addressees are context, not delegated work.
- Two or more Relay mentions: run in written order; a queued recipient sees
  preceding replies, subject to the existing bounded context window. The
  existing hop, membership, budget, and coalescing guards still apply.
- Both DM entry points return the same ID. Old archived/closed DMs preserve
  messages, runs, and session state. DM archive/delete return a conflict.
- Shared-room archive is reversible. Seeded rooms stay live. Threads remain
  replyable while their room is active.
- Scanner skips bot-authored and already addressed posts, DMs, threads, stale
  permissions, and identities assigned to another persona. A failed triage
  replays; ack advances. Quiet scheduled ticks make no model run.
- A schedule's model override changes only that job, not Pai's normal chats.

The scan is deliberately a notification/triage mechanism, not a second
auto-reply router. The owning persona decides whether a public response is
useful after seeing the room context. The platform does not infer that every
unmentioned statement needs an answer.

The Pai job runs every 15 minutes in `America/Toronto` on
`gpt-5.6-luna`. Its prompt is: "Use your discord Tool to find your owned
identity and scan unaddressed guild-channel human messages. Read the room
around anything that might need action. Make at most two useful internal Relay
nudges to the appropriate persona owners; give each the source room and message
ID. Do not send a Discord reply merely to prove the scan ran. If nothing
needs action, stay quiet. Acknowledge the scan batch only after triage. Do not
copy private material to a public Relay room, and avoid tables in Discord."
