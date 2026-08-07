# Conversations

**What:** a threaded, multi-turn exchange with one agent. Each turn is a Run
under the hood, sharing the thread's context. Conversations are **typed** by
where they came from:

- **web** — started and continued from the UI (an agent's Conversations tab,
  or the global Conversations table). Deletable (with confirmation), renamable.
- **discord** — a Discord thread bridged by the connector-discord service
  (see the [Glossary](glossary.md)). Read-only in the UI
  (the thread lives in Discord; each turn shows who sent it), renamable.

**Lives in:** Postgres (`conversations` + turn linkage on runs), with Kafka
topics (`conversation.inbound` / `conversation.outbound`) carrying turns
between the API, connectors, and the recorder.

**How to have one:** open an agent → Conversations tab → type. Or mention the
Discord bot / reply in its thread.
