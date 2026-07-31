# Memories

**What:** what an agent remembers across runs — small keyed notes with tags,
searchable with Postgres full-text search. An agent with `memory: true` in its
manifest gets a per-run scoped token and saves/recalls **only in its own
namespace**; the platform never shares memory between agents implicitly.

**Lives in:** Postgres. Reviewable and editable by the admin: per-agent under
the agent's Memories tab, globally (cross-agent search) under Agents →
Memories.

**Shape:** `{agent, key?, content, tags[]}` — an optional unique `key` per
agent lets an agent upsert a well-known note (e.g. a dedup ledger) instead of
appending forever.

**Where it's NOT used:** cross-agent state that must survive adversarial input
(e.g. the news dedup ledger) is a server-owned table, not agent memory — an
agent's memory is the agent's, and only as trustworthy as the agent's inputs.
