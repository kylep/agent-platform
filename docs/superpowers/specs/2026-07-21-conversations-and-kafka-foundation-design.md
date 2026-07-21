# Conversations & Kafka Foundation — Design

Date: 2026-07-21. Status: approved (Kyle: "do K then C, don't stop in the middle").

Two phases. **K (Kafka foundation)** hardens the event bus and makes ingress
event-sourced. **C (Conversations)** adds a first-class Conversation entity and a
connector abstraction (Discord real, Slack stubbed) built on K's bus.

---

## Phase K — Kafka foundation

Goal: make the Kafka usage technically sound and event-driven — no clever
shortcuts, textbook patterns.

### K1. Event envelope + schema

Every Kafka message value becomes a versioned envelope (was: a raw dict):

```json
{
  "type": "run.requested",      // event type
  "schema_version": 1,
  "id": "<uuid>",               // unique event id (dedup/trace)
  "ts": "2026-07-21T...Z",      // producer timestamp (UTC)
  "key": "<partition key>",     // e.g. run_id / conversation_id
  "source": "api|runner|dispatcher|scheduler|connector:discord|...",
  "data": { ... }               // the domain payload
}
```

- `events.py`: `Envelope` pydantic model; `Producer.publish(topic, key, data, *, type, source)`
  wraps into an envelope. `unwrap(value) -> (envelope, data)` for consumers,
  tolerant of legacy un-enveloped messages during rollout (treat as `data` with
  `type="legacy"`).

### K2. Producer hardening

`AIOKafkaProducer(enable_idempotence=True, acks="all", compression_type="gzip")`.
Idempotent producer + acks=all = no duplicates on retry, durable to the leader.
Add `lz4` to backend + runner requirements. (RF=1 on a single broker limits real
durability — documented, not fixable without more brokers.)

### K3. Consumer error handling → dead-letter (correctness fix)

Today dispatcher & recorder do `try: handle() except: log; commit()` — a poison
message is silently dropped. Change: on handler exception, publish the raw
message + error to a `dead.letter` topic, then commit (so it doesn't loop).
Nothing is lost; failures are inspectable. Shared helper `consume_forever(consumer,
handler, producer, dlq_topic)` used by dispatcher/recorder/ingest loops.

### K4. Topic configuration

`values.topics` becomes a list of objects `{name, partitions, retentionMs}`.
`topics-job` creates each with `--partitions` + `--config retention.ms=...`, and
`--alter`s partitions upward for existing topics (tolerant). Targets:

| topic | partitions | retention | why |
|---|---|---|---|
| run.inbound | 6 | 1h | keyed by run_id; ingest parallelism |
| run.requests | 6 | 1h | keyed by run_id |
| run.events | 6 | 1h | recorded to DB; short |
| run.transcript | 6 | 1h | recorded to DB; short |
| run.dlq | 1 | 7d | run-launch failures (DLQ UI) |
| conversation.inbound | 6 | 24h | keyed by conversation_id |
| conversation.outbound | 6 | 24h | keyed by conversation_id |
| dead.letter | 1 | 7d | consumer processing failures |

RF=1 everywhere (single broker).

### K5. Event-sourced ingress

Principle: **event-source asynchronous triggers; keep synchronous commands
request/response.** Event-sourcing a synchronous command (faking async) is an
anti-pattern, so `POST /api/runs` stays DB-first + returns the id. Asynchronous
triggers become events.

- `run.inbound` topic + an **ingest consumer** (added to the dispatcher service's
  gather loop). Single materialization point: `materialize.py::materialize_run(
  session, producer, spec)` creates the Run row (idempotent on id) and publishes
  `run.requests`. Used by both the sync `create_run` and the ingest consumer.
- **webhook** (`/api/webhooks/{agent}`): validate + pre-assign a run_id, produce a
  `run.requested` envelope to `run.inbound`, return `202 {id}`. No direct DB write
  → **this is the "webhooks now use Kafka" fix.**
- **scheduler**: produces to `run.inbound` instead of creating+publishing directly.

Delivery is at-least-once; `materialize_run` is idempotent on run_id, so a
redelivered inbound event is a no-op.

---

## Phase C — Conversations + connectors

Goal: a first-class **Conversation** (create/delete/continue from the UI and from
connectors), a connector abstraction (Discord real, Slack stub), all on K's bus.

### C1. Data model (`db.py`)

- `Conversation`: `id, connector (discord|slack|web), external_ref (nullable,
  e.g. discord thread id), agent, title, status (active|closed), created_at,
  updated_at`.
- `Run` gains `conversation_id` (nullable) and `result` (Text — the final
  assistant reply, captured by the recorder from the terminal `result` frame). A
  conversation's turns are its Runs, ordered by created_at; the platform now owns
  conversation history.

### C2. Conversation API (`api/conversations.py`)

- `POST /api/conversations {connector, agent, title?}` → create (operator+).
- `GET /api/conversations` (list), `GET /api/conversations/{id}` (with turns:
  each turn's prompt + result + state).
- `DELETE /api/conversations/{id}` → close (status=closed).
- `POST /api/conversations/{id}/messages {text}` → **continue**: build the run
  prompt from prior turns (prompt/result pairs) + the new message + injected
  memory, then `materialize_run` a run tagged with `conversation_id`. Returns the
  run id. This one endpoint powers both the UI "send" and connector ingest.

### C3. Connector abstraction

A connector bridges an external channel ↔ conversations via Kafka:

- **Inbound**: connector produces `conversation.inbound` events `{connector,
  external_ref, external_user, text}`. A **conversation-ingest consumer** (in the
  dispatcher service) maps `external_ref` → Conversation (create if new, bound to
  the connector's default agent), then runs the C2 continue logic.
- **Outbound**: when a conversation run reaches terminal, a **projector** (in the
  recorder) publishes a `conversation.outbound` event `{conversation_id,
  connector, external_ref, text=run.result}`. Connectors consume it, filter to
  their connector, and post to the external channel.
- **The web UI is a connector too** (`connector=web`): "continue from UI" calls
  C2 directly and reads the reply from the conversation turns / run tail — same
  mechanism as Discord, no external post needed.

Connector registry: a small static list (`connectors.py`) of known connectors
with `{name, kind, implemented, description}` exposed at `GET /api/connectors`, so
the UI can show Discord (implemented) and Slack (`implemented=false`, "Not Yet
Implemented").

### C4. Discord connector service (`services/connector-discord/`)

A thin long-lived Deployment (discord.py gateway), modeled on pai's gateway but
reduced to a Kafka bridge:
- On mention / bound-thread reply → produce `conversation.inbound` (external_ref =
  Discord thread id; creates a thread if replying to a channel mention).
- Consume `conversation.outbound`, filter `connector==discord`, post `text` to the
  thread; typing indicator while a run is in flight (correlate by conversation).
- Thread binding + processed-id dedup (small, from pai's patterns).
- Needs a `discord-bot` secret (token + guild). Ships deployable; the real WS
  connection activates once the token secret is set. Verified pre-token via
  synthetic `conversation.inbound` events end-to-end.

### C5. Slack connector — stub

Registered in `connectors.py` with `implemented=false`; no service. UI shows it
greyed with "Not Yet Implemented." Placeholder `services/connector-slack/README.md`
documenting the same inbound/outbound contract for later.

### C6. UI

- **Conversations** page: list (connector, agent, title, status, last activity);
  create (pick connector=web + agent); open → turn transcript (prompt/reply per
  turn) + a message box to continue (live-tails the in-flight run); delete/close.
- **Connectors** section: the registry (Discord ✓, Slack — NYI).

---

## Testing

- K: envelope round-trip + legacy tolerance; producer config; consumer→dead.letter
  on handler throw; `materialize_run` idempotency; webhook produces to run.inbound;
  ingest consumer materializes.
- C: conversation CRUD; continue builds prompt from history + materializes a
  conversation-tagged run; outbound projector emits on terminal; connector
  registry; conversation-ingest maps external_ref→conversation.
- Live: K verified with a webhook→run and a normal run; C verified with a web
  conversation (create → continue → reply) end-to-end, plus a synthetic
  `conversation.inbound` driving a run and a `conversation.outbound` emission.

## Out of scope / deferred

- Real Discord live verification (needs Kyle's bot token/guild) — plumbing ships,
  activates on secret.
- Slack implementation (stub only).
- Multi-broker replication (single-node RF=1).
- Token-streaming replies to Discord (post-once for now; live-tail in the UI).
