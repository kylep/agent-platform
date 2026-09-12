# Conversations

**What:** a multi-turn exchange with one agent. A conversation is now a
**Relay DM** — one room with two members, `user:<you>` and `agent:<name>` — and
everything a conversation was is a thing Relay does: each turn is still a Run,
the thread's context still carries between turns, and the history is now
`relay_messages` rows you can react to, thread, and search. Read
[relay.md](relay.md) first; this page exists to say where the old surface went.

Conversations are still **typed** by where they came from, which is now a
property of the room's membership and its binding:

- **web** — started and continued from the UI (an agent's Conversations tab, or
  Relay's DM side). Deletable, renamable.
- **discord** — a Discord thread bridged by the connector-discord service
  (see the [Glossary](glossary.md)) through `relay_bindings`. The thread lives
  in Discord; each turn shows who sent it, so a `discord:<id>` participant
  appears beside you in the room.

**Lives in:** platform Postgres. `conversations` is the channel table Relay
grew out of (`kind='dm'` for these), the turns are `relay_messages`, and the
per-agent resume blob moved from the conversation row to `relay_sessions`.
Kafka still carries `conversation.inbound` / `conversation.outbound` for the
bridges, and `relay.messages` carries every message in every room.

**How to have one:** open an agent → Conversations tab → type. Or mention the
Discord bot / reply in its thread. **`/conversations` in the UI redirects to
`/relay?kind=dm`** — the DM side of Relay — so old links and bookmarks still
land on the same rooms.

`/api/conversations*` remains as a compatibility facade over `kind=dm`
channels for callers that predate Relay; retiring it is a later design doc.
