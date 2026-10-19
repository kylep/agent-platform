# Persona startup memory and local retrieval

Native `ap-persona` launch/resume and detached cluster runs share the own-namespace
selector. Identity, preferences and continuity key prefixes receive priority:
10 newest pinned candidates and 20 newest general candidates, deduplicated,
then at most 20 delivered records, 2,000 characters each and 12,000 total.
Candidates are fetched in one statement. Equal update timestamps now use ID
descending as the deterministic tiebreaker; previously their order was unspecified.
Labels for teams/projects do not grant access to another namespace. Native
bootstrap fields and checksum serialization remain unchanged.

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
```

Canonical saves/deletes never call embeddings. The indexer's bounded reconciliation
covers both REST and Tool commits, survives restarts and records failed sources
with a five-minute retry. Failures do not starve newer records. Retrieval joins
canonical source ownership, timestamp/content hash and index generation before
its result limit, then verifies full source hashes. Overwrites/deletes become
ineligible immediately, before cleanup. Exact vector search is deliberate for
this small corpus; no approximate index is needed yet.

The one shared hybrid ranking policy combines 20 lexical term-OR and 20 cosine
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
building the native resume invocation. Use `ap-persona status <id>` first; a drained
or expired session needs a fresh `ap-persona launch` instead. Plain native resume,
`/clear` and forks do not substitute for this path. A server deploy alone also
provides no evidence that an old laptop process's AP bridge/network has recovered.

Runtime contracts: [Ollama embeddings](https://docs.ollama.com/api/embed),
[Ollama configuration](https://docs.ollama.com/faq),
[pgvector](https://github.com/pgvector/pgvector).
