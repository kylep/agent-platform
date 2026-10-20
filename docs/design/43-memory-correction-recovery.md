# Conditional memory correction and bounded recovery (CA-9)

Status: conditional writes implemented; general revision history deferred.
This contract does not implement a history table or change retrieval ranking.

## Current write and recovery paths

The REST keyed save previously selected then replaced without a row lock;
its new-key unique-index loser retried as an unconditional overwrite. REST
PATCH/DELETE operated by owned ID. The memory Tool already locked an existing
key in its run-authority transaction, but had no caller precondition and its
missing-key race could fail insertion. Canonical rows live in
`tool_memory.memories`, shared by the API and Tool. Ordinary direct database
administration is outside the application concurrency contract.

A real, limited recovery path already exists: local persona Tool writes record
before/after states in `local_persona_mutations` in the same transaction.
Admin-only session mutation reads and closed/revoked-session, all-or-nothing
revert compare the current fields against each recorded after-state under a
row lock. This covers those session writes, not all REST/cluster writes. Its
field comparison has ABA limitations; it is not a universal revision log.
The transcript pruner clears local event payloads and transcript frames, but
does not prune this mutation table or immutable startup-context payloads.
Canonical memory deletion currently does not erase these other copies.

Content-free production observations at 2026-10-20 (before CA-9 verification):
301 canonical notes, 84 keyed; 16 local-session memory mutation records across
9 sessions, including 5 replacements, 0 recorded reverts, approximately 18,901
bytes of before/after JSON storage (excluding row/index overhead). The memory
Tool audit has 52 action-labelled allowed saves and 99 labelled reads; 12,256
older allowed calls have no action label. These are call counts, not proven
successful replacements or data-loss incidents. Neither this audit nor current
canonical rows establish the overall overwrite rate. No real lost update or
mistaken overwrite incident was established. Zero recorded reverts does not
mean no recovery was needed.

## Conditional write contract

REST and lexical Tool reads/saves return `revision`; hybrid `source_hash` is the
same identity. A keyed save may supply `expected_revision`:

| Precondition | Existing key | Missing key |
| --- | --- | --- |
| omitted or null | legacy unconditional replacement | create |
| `absent` | conflict, including identical desired content | create only |
| current 64-character lowercase hash | replace, or identical-state no-op | conflict |
| stale hash | conflict, including identical desired content | conflict |

REST PATCH accepts the same optional field; DELETE accepts the query parameter.
`absent` cannot match an existing row for either operation. Missing/foreign IDs
still return 404; agent callers cannot choose another namespace. A keyed save
without a nonempty key cannot use a precondition. Malformed tokens are rejected.
A successful changed replacement preserves ID/created_at and changes source
revision; delete/recreate gets a fresh ID even for identical text. Restoring
identical text after an intervening change normally changes updated_at and the
revision. Matching identical canonical content/tags/scope is a no-op and keeps
the token. Two distinct proposed replacements based on one token cannot both
succeed. An ambiguous network retry after a committed change must reread; an
old token is not a success receipt or idempotency key.

Comparison, mutation and local-session provenance share the transaction and
existing-row lock. The partial unique index arbitrates missing-key creation.
A losing conditional create returns a structured conflict even if the winning
insertion is deleted before the locked reread; then current_revision=null.
Successful API responses are captured before releasing the lock. Conflicts leave canonical
state, timestamps, provenance and embedding rows unchanged. REST responds 409
with `detail.code=memory_revision_conflict`, expected token and current token
(or null if missing). The Tool returns `ok=false` with the equivalent `error`
object, without saved text. Conflict metadata is only for the authorized
namespace. Reread, reconcile intentionally, then use the new token; do not
blindly turn a conflict into an unconditional save.

Tokens reuse SHA-256 over canonical JSON of ID, key, content, tags, updated_at.
Psycopg timestamps are normalized to UTC for interoperability with asyncpg.
This identifies current source state, not truth, semantic equivalence, authorship
or recoverable history. Scope/namespace are not directly hashed; normal scope
changes update updated_at. SQL that preserves/restores every hashed field can
cause ABA, and writers bypassing application semantics can retain stale tokens.
No claim of globally monotonic revision numbers is made. Old excerpt references
fail current-source validation immediately after replacement/deletion, before
asynchronous vector cleanup/reindex. History is not needed for this check.

## Proposed recovery follow-up: opt-in, bounded, separately approved contract

Recommendation: defer general history implementation until retention and erasure
choices below are settled. Five observed local-session replacements justify a
concrete bounded design; they do not justify silently retaining every memory
forever. Ship stale-write protection now. Do not expand routine automated
summary replacement or consolidation on the assumption that recovery exists.

Proposed follow-up boundaries (defaults proposed, not deployed):

- Opt-in per namespace; store prior canonical states plus opaque revision,
  actor/run and timestamp. Cap at 10 previous versions per keyed memory, 30 days,
  and 10 MiB UTF-8 logical history bytes per namespace. Prune oldest history in
  the write transaction when count/byte limits require it; expired reads deny
  access immediately and a daily pruner reclaims bytes. If one prior version
  exceeds the cap, refuse a history-enabled replacement with a clear quota
  error rather than silently promising recovery. No silent oldest-current loss.
- Separate namespace-scoped history read/list/restore routes, with the current
  memory grant and owning active run/session checks on every agent operation.
  Admin reads remain explicit. Revocation blocks new reads/restores immediately;
  it cannot retract previously delivered content. No history in lexical,
  hybrid, startup, promotion defaults or embedding indexing. Keep it outside the
  canonical table and normal candidates. No model-driven compaction.
- Restore copies a chosen old state into the current canonical row using the
  same current-revision CAS; never restores old timestamps or reuses a token.
  Concurrent correction conflicts. A deleted row is not resurrected by an old
  token; an explicit create-only restore would need a new ID and affirmative
  owner intent, if that product behavior is selected.
- Intentional canonical deletion should atomically purge its online recovery
  history and prevent ordinary future retrieval. Choose explicitly whether
  deletion must also redact local-session mutation copies and captured startup
  packets. Immutable captures and historical run results are real retained
  copies today, so canonical deletion is not permanent erasure.
- Permanent erasure needs a separately scoped admin operation with an inventory
  of canonical rows, vectors/failure rows, mutation states, startup packets,
  local event payloads, transcripts/results, exports and backup copies. Redact
  or cryptographically erase online owned copies and keep content-free receipts;
  invalidate affected capture digests explicitly rather than silently mutating
  immutable evidence. Apply tombstones on backup restore. Document the backup
  expiration window; do not promise instantaneous deletion from offline dumps
  or that already received material can be recalled.
- At 100 notes × 10 prior versions × 2 KiB, logical content is about 2 MiB per
  namespace before metadata/indexes/backups; the 10 MiB cap is a hard proposed
  bound, not an observed average. Measure actual stored/TOAST and backup cost
  during a small opt-in pilot. Retention pruning and restore-race tests are
  required before enabling it broadly.

Unresolved choices: opt-in owner and default; 30-day/10-version/10-MiB limits;
whether ordinary delete redacts captured/mutation copies or requires a separate
privacy operation; backup expiry guarantees; treatment of deleted-key restores;
transition/retention for existing local-session mutation records. A follow-up
must settle these choices, additive schema/permissions, deletion/restore tests
and migration of existing recovery copies. This document authorizes none of
those retention changes and does not promise full erasure today.

## Verification and rollback

Focused REST tests cover malformed/missing/stale/create-only, identical retries,
PATCH/DELETE, recreation and denied cross-namespace metadata. The isolated real
PostgreSQL gate races API and Tool on the same revision and absent key, tests
rollback/retry and non-UTC Tool sessions, and checks stale source resolution and
unchanged index rows on conflicts. No real persona notes are rewritten.

No data migration or new column is required. Existing clients remain valid;
callers using preconditions must not downgrade to a backend/Tool version that
ignores them. For rollback, stop conditional clients first, restore matching
backend and capability revision, and relaunch schema-caching persona clients.
Existing canonical data and rebuildable index format remain compatible.
