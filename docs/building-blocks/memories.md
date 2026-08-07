# Memories

**What:** what an agent remembers across runs — small keyed notes with tags.
An agent declares the `memory` TOOL (docs/design/12) and reads/writes **only
its own namespace** — the namespace is the broker-verified caller identity,
never something the model chooses. Reading and writing are one tool because
namespacing makes read-only memory meaningless. (The old `memory: true`
manifest flag is retired; declaring the tool is the grant.)

**Lives in:** the memory tool's provisioned Postgres schema (`tool_memory`) —
the block's own declared infra. Reviewable and editable by the admin:
per-agent under the agent's Memories tab, globally (cross-agent search) under
Agents → Memories.

**Shape:** `{agent, key?, content, tags[]}` — an optional unique `key` per
agent lets an agent upsert a well-known note (e.g. a dedup ledger) instead of
appending forever.

**Where it's NOT used:** cross-agent state that must survive adversarial input
(e.g. the news dedup ledger) is a server-owned table, not agent memory — an
agent's memory is the agent's, and only as trustworthy as the agent's inputs.
