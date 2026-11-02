# Memories

**What:** what an agent remembers across runs — small keyed notes with tags.
An agent declares the `memory` TOOL (`docs/design/12-executable-capabilities.md`) and reads/writes **only
its own namespace** — the namespace is the caller identity the mcp-broker
verified (see the [Glossary](glossary.md)),
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

## History

Every insert, update and delete of a memory leaves a row in `memory_versions`, written by a database trigger. The row records which run, or `rest:<who>`, made the change. Update and delete rows hold the text that was replaced, so nothing an agent overwrites is lost straight away.

- **Retention:** history is kept for `AP_MEMORY_HISTORY_DAYS` (default 30) and then purged, so forgetting does eventually become permanent.
- **Browse:** admins can list it with `GET /api/memory-history?agent=|memory_id=|run_id=`.
- **Restore:** `POST /api/memory-versions/{version_id}/restore` writes an old state back, and the restore is itself versioned.
- **Erase:** `DELETE /api/memories/{id}?purge_history=true` erases a memory together with its history.
- **Access:** agents never see history.

## Commitments

When a persona promises future work, it calls `memory(action="commit", summary, destination, meaning)`.

- **Shown later:** open commitments are given to every later run of that persona, including roundtable and other Relay-triggered turns, as an `<open-commitments>` data block in the user turn.
- **Closing:** `complete` and `void` close a commitment.
- **Limit:** at most 20 can be open at once, which is exactly how many are delivered.
- **Delivery record:** each delivery is recorded, so `GET /api/commitments/stats` can show commitments that stayed open after many deliveries.
- See [design 44](../design/44-commitments-and-dreaming.md).

## Dreaming

With `AP_DREAM_ENABLED=true`, each eligible agent gets a `dream:<agent>` Scheduled Job, nightly from 03:00 America/Toronto, staggered 15 minutes apart. Eligible means not a system agent, holds memory, and has no result topic.

**What happens in a dream:**

- The run can call only its memory Tool.
- It pages through its conversations since its last dream, all of its memories and its commitments with `memory(action="review")`.
- It then saves, merges, corrects and deletes notes, using `delete`, which exists only in dreams.
- The history trigger caps how many existing memories one dream may replace or delete, at a quarter of the namespace with a minimum of one.

**Reviewing dreams:** `GET /api/dreams` lists them with their diary and an exact per-run change list. `POST /api/dreams {agent}`, or the job's Run Now, dreams immediately.
