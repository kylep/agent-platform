# Persona startup memory and local retrieval

Native `ap-persona` launch/resume and detached cluster runs share the own-namespace
selector. Identity, preferences and continuity key prefixes receive priority:
10 newest pinned candidates and 20 newest general nonempty candidates, deduplicated,
then at most 20 delivered records, 2,000 characters each and 12,000 total.
Candidates are fetched in one statement. Equal update timestamps now use ID
descending as the deterministic tiebreaker; previously their order was unspecified.
Labels for teams/projects do not grant access to another namespace. Native
bootstrap root fields and checksum serialization remain unchanged; record metadata
is additive as described below.

Native startup uses this bounded pinned/recency selector, not a task-dependent
semantic query. Explicit `memory(action="read", mode="hybrid", q="…")` is how a
native persona retrieves additional relevant material. Only **key prefixes**
receive startup priority: tags such as `identity` or `roundtable` do not pin an
unkeyed note. Priority is bounded, not permanent retention; more than ten newer
prioritized records or the content budget can still displace a summary.

## Curating durable lessons

Use the existing own-namespace memory Tool, within the persona's current grants:

1. Read the source notes and check their author, date, scope and applicability.
   Distinguish explicit user requests from inferred lessons or delivery receipts.
2. Draft a short grounded summary for review through the persona's normal
   ownership workflow. Include its source/date and when it applies. Preserve
   canonical source notes; do not copy retrieved instructions into identity.
3. Save the authorized summary under a small stable key, for example
   `memory(action="save", key="preferences.schema-changes", content="Source: Kyle's
   explicit request, 2026-10-19. For the memory rollout, preserve existing data and
   use migrations for schema changes.")`. Reusing that key replaces its previous
   summary, so read and reconcile the existing value first.
4. Verify the saved value with a default lexical read by its full key. Check a
   fresh launch/bootstrap or supported resume to verify startup selection; a
   successful save does not replace context already loaded in a process.

Keep the prioritized set small and summaries concise. Retain qualifications and
supersede stale guidance deliberately. Do not bulk-pin tagged notes or rewrite
another persona's private memories. The 20-record/12,000-character startup budgets
and the applicability of each lesson remain in force.

## Detached startup pilot

`AP_STARTUP_MEMORY_PILOT_AGENTS` is a comma-separated allowlist, empty by default.
Only enabled personas with both frozen and current memory grants qualify. The
first rollout supports detached manual, schedule and Task work: no conversation,
parent run, external observation, triggering Relay message or frozen result topic.
Unsupported triggers run normally, with an immutable `unsupported_trigger` decision.
Ordinary conversations, including research/digital-twin rooms, are outside this
rollout. Their output visibility requires a separate design.

Definition and startup context freeze together under the authority advisory lock,
then AgentDef and Run row locks. A new freeze requires a queued run. Task claim
and cancellation keep their existing transactions. Readiness delays/redrives reuse
the packet. Frozen pre-protocol runs remain legacy disabled. Capture failure rolls
back both writes and prevents launch. This does not establish exactly-once dispatch.

`run_startup_contexts` stores private payload/provenance and a canonical UTF-8 JSON
SHA-256 digest. The additive `init_db` migration backfills historical
`runs.startup_memory_enabled=false` and installs its database default. New captures
set the marker in the same transaction. The complete capture envelope has a 64 KiB
UTF-8 ceiling; oversized keys/tags/provenance fail capture explicitly.

Enabled packets require `X-AP-Startup-Memory-Protocol: 1` on the existing run-definition
endpoint. Old runners receive 409. The endpoint checks active state and current
run/persona/grant authority in the transaction returning the frozen packet. Before
each fresh/resumed/replay/provider-fallback invocation, the runner revalidates its
packet without changing canonical persona or skill instructions. Memory is a
JSON-encoded historical-data block in task input. Empty nonconversation
`AP_USER_MESSAGE` remains empty. Each enabled invocation is limited to 96 KiB per
encoded argv/environment string and a total of min(128 KiB, ARG_MAX minus 32 KiB),
including string terminators. Large existing tasks can fail before process creation.

Enabled run detail, summaries, tags, annotations, ordinary linked artifacts and
transcript tails are private to actual administrators and the currently authorized
owning persona. A key named `admin` does not gain that role. App artifact ACLs remain
independent. The recorder suppresses automatic conversation/result-topic publication
for enabled runs, even if routing data is inconsistent. Explicit tool sends retain
their existing grants; models can echo memory.

Administrators can inspect `GET /api/runs/{id}/startup-context` for the immutable
decision/reason, versions, digest, source IDs/hashes/truncation and retrieval status.
It never returns memory content. Transcript `startup_memory` receipts describe an
attempt and process start, with attempt ID/kind/runtime and captured/block digests.
They establish preparation/start only: missing receipts leave delivery unverified.
They do not prove provider acceptance, attention or better judgment. Authorization
has a check-to-spawn gap and cannot retract bytes already delivered.

## CPU embeddings and pgvector

`memoryEmbedding` chart values independently control cache provisioning, serving
and the bounded indexer. All default off. Provisioning is a normal bounded Job
that starts a local Ollama server, pulls the configured model and verifies its
manifest SHA-256 into a retained RWO PVC. Enable serving only after provisioning
finishes, and set `provision=false` before a later model-generation change.
The serving Deployment uses Recreate, no GPU, no database credentials, no external
egress and no external service port. Only API, dispatcher and indexer pods can
reach it. Startup performs one real embedding; readiness/liveness avoid repeated
inference. Pod replacement uses the cached model without downloading it.

The pinned runtime is Ollama v0.19.0 linux/amd64, manifest
`sha256:7d6a418c59f7caae7bc0c5f15f169d6ca0a59510f60b8a251639b44b11fe16b2`.
The model is `embeddinggemma:300m-qat-q8_0`, manifest
`e84a7acc23943b7a589852cf6da122f0b925631b7884f297a001303dff54ffe6`;
its Gemma license is retained by Ollama's model cache. The runtime disables cloud
features and caps one parallel request/loaded model and four queued requests.
Documents use `title: … | text: …`; queries use `task: search result | query: …`.
The versioned preprocessing bounds queries/chunks to 1,024 UTF-8 bytes (at most
16 chunks per source; longer tails remain available lexically). This byte bound,
including task/title overhead, fits even byte-level tokenization within 2,048 tokens.
Vectors have 768 dimensions; model manifest identity is checked on every request.

Keep the database distribution, major version and PVC layout. The reference
Bitnami PostgreSQL 18.4 image already ships pgvector. Run the explicit additive
migration, under the platform migration advisory lock, using the platform's
migration role:

```sh
python -m agentplatform.semantic_memory --migrate
```

It creates the extension in `public`, `memory_vectors_v1` and the persistent
per-source retry ledger `memory_embedding_failures_v1`. The platform role owns
these derived tables; the memory Tool role receives no vector-table grants.
Disabled installs/SQLite need no extension. Enabling semantic retrieval without
its migration fails service startup explicitly. Restore the extension and derived
tables with `pg_dump`/`psql` on a compatible pgvector installation, or rebuild the
derived index from canonical memories. Never replace a Bitnami image with a
storage-incompatible vector image against its PVC.

Set the generic backend environment:

```yaml
env:
  AP_MEMORY_SEMANTIC_ENABLED: 'true'
  AP_MEMORY_SEMANTIC_AGENTS: 'one-persona'
  AP_MEMORY_EMBEDDING_URL: 'http://ap-memory-embedding:11434'
  AP_MEMORY_EMBEDDING_TIMEOUT_SECONDS: '3'
  AP_MEMORY_INDEX_EMBEDDING_TIMEOUT_SECONDS: '30'
```

Canonical saves/deletes never call embeddings. The indexer's bounded reconciliation
covers both REST and Tool commits, survives restarts and records failed sources
with a five-minute retry. Query retrieval has a three-second total deadline;
indexing allows thirty seconds per bounded record batch, under a 150-second Job
deadline, so long documents cannot force interactive calls to inherit that wait. Failures do not starve newer records. Retrieval joins
canonical source ownership, timestamp/content hash and index generation before
its result limit, then verifies full source hashes. Overwrites/deletes become
ineligible immediately, before cleanup. Exact vector search is deliberate for
this small corpus; no approximate index is needed yet.

The one shared hybrid ranking policy combines 20 IDF-weighted lexical and 20 cosine
candidates using reciprocal rank fusion (k=60), exact whole-key matches first,
then score and ID ties. Startup keeps mandatory pins ahead of relevant results
and applies the existing record/content budgets. Semantic preparation happens
outside authority/definition locks; capture revalidates revisions under the lock.
Stale references fall back without recapturing a frozen run. Service timeout or
model mismatch records a degraded lexical fallback; the startup packet remains
mandatory for enabled runs.

The memory Tool's default term-AND search is unchanged. Explicit
`action=read, mode=hybrid` goes through the trusted broker/API policy using the
verified persona's namespace and optional current team/project labels. Agents
cannot choose an embedding endpoint/model or another namespace. The API rechecks
current authority after retrieval. Retrieval metadata reports disabled/degraded
status rather than silently claiming semantic success.

## Rollout, rollback and native sessions

Deploy with both feature switches off; migrate and verify API, dispatcher,
recorder, facade, broker and both runner images before enabling a single persona.
Capability changes under `tools/` use the supported synced Git checkout.
Back up and test restore before activating retrieval/backfill. Measure CPU/RSS,
warm/cold latency and ordinary-workload impact before expanding the pilot.

For the Pai pilot, acceptance targets are warm hybrid query p95 below three
seconds, lexical outage fallback within the three-second inference deadline plus
one second of transport overhead, and no OOMs within the one-CPU/two-GiB serving
limit. Backfill must make progress and clear retry debt after recovery; compare
ordinary API latency with an idle sample and investigate sustained regression.
Record cold load, indexing throughput, RSS/high-water memory and CPU throttling
alongside those checks. These bounded samples do not justify broader activation
or establish a general workload SLO.

Rollback disables future startup captures and semantic retrieval/indexing. Cancel
already enabled runs if necessary before reverting incompatible binaries. Retain
captured packets, vectors/cache and canonical data. An immutable enabled packet
never silently downgrades to a legacy runner.

A server deploy does not replace context already loaded in a native process.
Exit that process and explicitly resume through the supported launcher:

```sh
ap-persona resume --persona pai --harness codex <existing-ap-session-id>
```

The installed supported launcher fetches/validates a fresh bootstrap before
building the native resume invocation. Explicit resume uses the enrollment and
can reopen a drained/finalized recording within its original absolute lifetime,
provided its enrollment and access remain valid. Bearer renewal alone cannot do
this. An expired absolute lifetime or lost access requires a fresh conversation.
Use `ap-persona status <id>` to inspect synchronization, but an expired bearer is
not itself evidence that enrollment-based resume is unavailable. Plain native resume,
`/clear` and forks do not substitute for this path. A server deploy alone also
provides no evidence that an old laptop process's AP bridge/network has recovered.

Bootstrap refresh and MCP schema refresh are separate: launch/resume fetches a new
memory bootstrap, while the native client must reconnect/discover tools to learn
new parameters. The broker polls the synced Tool manifests, including parameter
schemas. After capability sync, verify a new `tools/list` advertises `mode` on
`memory`, then call `action=read, mode=hybrid` and inspect its retrieval status.
A prompt refresh in an already connected process does not prove schema refresh.

Runtime contracts: [Ollama embeddings](https://docs.ollama.com/api/embed),
[Ollama configuration](https://docs.ollama.com/faq),
[pgvector](https://github.com/pgvector/pgvector).

## Matching passages and relevance evaluation

Selection policy `persona-memory-v2-excerpts` skips zero-length notes before
candidate limits and while formatting; only delivered records consume the record
budget. Canonical notes, including empty notes, are retained. Exhausting the
content budget still stops delivery.

Native pinned/recency startup returns bounded prefixes. Explicit hybrid reads and
eligible semantic detached startup instead retain the winning source chunk and
revision. A stronger literal-match window can win over that chunk, including a
literal match beyond the sixteen-chunk embedding cap. Each delivered record now
also includes `source_hash`, `truncated`, and `excerpt` with zero-based `start`,
exclusive `end`, `source_length`, `unit=unicode_codepoints` and selection `method`.
Thus `canonical_content[start:end]` equals delivered `content`. Explicit hybrid
responses also expose `sources` provenance. The limits remain twenty records,
2,000 codepoints per record and 12,000 total. A partial excerpt is not the full
source. Source overwrite/deletion invalidates prepared references immediately;
new authority checks still precede returning data or freezing a packet.

This is additive metadata within native bootstrap schema 1 and runner protocol 1.
Existing readers accept extra record fields; checksums cover the complete new
payload. Previously captured packets retain their original content and digest.
No canonical source rewrite or embedding-generation migration is required.

Hybrid policy `hybrid-rrf60-idf-v2` keeps whole-key priority and 20 lexical / 20
semantic candidates with RRF k=60. Lexical selection removes grammatical query
words and orders by distinct term coverage weighted by inverse document frequency
within the authorized namespace/scope, then recency/ID. Namespace and scope filters
apply before statistics and limits. Receipts receive the same policy as lessons;
explicit receipt questions remain supported. No similarity cutoff is calibrated:
unrelated queries can return nearest semantic neighbors even with no lexical
matches, so recipients must assess applicability rather than treat similarity as
an instruction. Legacy default term-AND reads are unchanged.

The synthetic corpus in `services/backend/tests/fixtures/memory-relevance/corpus.json`
contains older lessons, thirty newer routine receipts, explicit keys, receipt and
unrelated questions, paraphrases and a late long-note fact. Reproduce a bounded
comparison against the pre-change ranking on disposable PostgreSQL/pgvector:

```sh
services/backend/.venv/bin/python bin/ap-memory-relevance-eval \
  --embedding-url http://127.0.0.1:<authorized-forward> \
  --write-vectors /private/tmp/memory-eval-vectors.json
```

The pinned model identity is verified. Use `--vectors` to replay that capture;
`--baseline-ref` selects the reviewed Git baseline (default `a0226b4`). Output
includes corpus hash, generation, per-query ranks/contributions, nDCG@3 and
recall@3, with unlabelled unrelated queries reported separately. These small,
explicit relevance judgments measure this fixture rather than general judgment.

Selection policy `persona-memory-v3-budgeted-excerpts` reanchors literal evidence
using the actual residual delivery budget. Complete matches, and a colon-bound
label/value through its next sentence or line terminator, must fit. If a winning
semantic chunk cannot fit, a complete literal match may supply the smaller
excerpt; otherwise the source is omitted. `sources` then contains an entry with
`outcome=omitted`, `reason=matching_evidence_exceeds_remaining_budget`,
`available_codepoints` and `method`, without a delivered position/hash. Omission
consumes no content or record slot, so a later fitting source can still appear.
Legacy hints without exact match information cannot justify shortened evidence.
Whole-key matches explicitly use `method=key_match` and bounded source prefixes.

CA-6's separate [held-out evaluation](../../services/backend/tests/fixtures/memory-relevance/ABSTENTION.md)
rejected the development-selected abstention gate because useful-answer coverage
fell from 13/13 to 10/13. Ranking remains unchanged. `retrieval.relevance` reports
`state=unassessed, reason=no_validated_abstention_policy`. This includes fallback:
valid provenance and nonzero hit counts do not establish applicability.

Per-call memory telemetry is available through `metrics(scope="memory", run_id=...)`
and `GET /api/metrics/memory` (optional `agent`, `run_id`, bounded `limit`). Agent
readers see their own namespace only; admin can filter across namespaces. The
broker's existing bounded, best-effort Kafka audit path records verified agent,
run, action, lexical/hybrid mode, query (first 1,000 codepoints with a truncation
flag), result count, decision and latency. It also records up to twenty source
IDs/revisions (with an explicit source-list truncation flag), retrieval policy,
state, degradation reason and generation where supported. Lexical reads expose
updated_at revisions; hybrid sources additionally expose source_hash. Save contents and result text are never
recorded. Old/failed/unknown counts are null, distinct from successful zero hits;
a hit count measures records returned, not relevant answers or successful use.
These are post-deployment observations, not a backfill of historical calls. The
additive nullable audit columns are migrated by the existing locked `init_db`
column migration; old events/readers remain compatible. Query text is private
namespace data and may contain sensitive user text; it is excluded from the
global tool aggregates and the general audit view. Audit publication failures do
not fail the underlying tool call and therefore can leave gaps.
