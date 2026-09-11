# Plan — Relay, the agent messenger (design 19)

Design: `docs/design/19-relay-agent-messenger.md`. Vision:
`docs/vision/agent-ecosystem.md`. This file is the **single source of state**
for the build: the loop re-reads it every tick, executes the first unchecked
task, and ticks the box with the commit hash. Anyone (Kyle, a fresh session)
can resume from it.

## Loop protocol (read this every tick, follow it exactly)

You are the **orchestrator**. You do not write product code yourself. You
dispatch subagents, verify their evidence, commit, and update this file.

1. Re-read this plan top to bottom. Find the first `- [ ]` task in "Tasks".
   If there is none, run "Definition of done"; if it passes, stop the loop
   (ScheduleWakeup `stop: true`) after a PushNotification with the one-line
   outcome; if it fails, add a task under "Tasks → Repairs" and continue.
2. Dispatch **one opus implementer** (Agent tool, `model: "opus"`,
   `subagent_type: "general-purpose"`) with: the task text verbatim, the
   "Ground rules for implementers" block below verbatim, the relevant design
   sections pasted in (not linked — the subagent has no conversation context),
   and the file paths from the task. Require it to work TDD: failing test →
   implementation → green, and to report the exact test commands it ran with
   their final summary lines.
3. When it reports, **verify the evidence yourself**: run the test commands it
   named (backend: `cd services/backend && .venv/bin/python -m pytest -q
   <paths>`; web: `cd services/web && npm run -s lint && npm run -s build &&
   npx playwright test --reporter=line`). Do not trust a claim of green without
   output in your own transcript.
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text, the
   design excerpt, and `git diff` of the uncommitted changes. Ask for
   findings ranked by severity, defects only, no style. For tasks tagged
   `[ui]`, dispatch instead (or additionally) a **sonnet visual reviewer**
   that starts `npm run preview` in `services/web` against the Playwright mock
   API (`tests/mock-api.ts`), screenshots the affected page(s) at 1280×800 and
   390×844 with `npx playwright screenshot` or a throwaway spec, reads the
   PNGs, and reports what a picky human would notice (alignment, contrast,
   truncation, dead space, unreadable states, missing empty/loading/error
   states). Never use `model: "fable"` or the default model for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" and move on. Loop at most twice per task; if a task
   still fails after two repair rounds, mark it `- [!]` with a one-paragraph
   note and continue to the next task — do not stall the whole build.
6. Commit on `main` (Kyle's workflow: single branch, direct commits, `git add`
   each file by name, never `-A`). Message: `feat(relay): <task title>` plus
   a body, and the attribution trailer the session was given. Then edit this
   file: `- [x] **Tn …** (commit `<hash>`)`.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task per tick keeps each
   turn's context small.
8. If a Bash action is refused by the auto-mode classifier (kubectl
   apply/delete, helm upgrade), do not retry variants. Write the exact
   commands into "Handoff to Kyle" below, send a PushNotification saying the
   build is blocked on a deploy step, and continue with any task that does not
   depend on it. Reaching the NUC works via Terminal.app:
   `osascript -e 'tell application "Terminal" to do script "<cmd> > <scratchpad>/x.out 2>&1; echo EXIT=$? >> <scratchpad>/x.out; exit"'`
   then wait for `EXIT=` in the file (plain `ssh pai` from this process gets
   "No route to host" — macOS Local Network permission, see memory
   `claude-code-local-network-tcc-gotcha`).

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  run with `cd services/backend && .venv/bin/python -m pytest -q`). Web is
  React 19 + Vite + `@ap/ui` (`packages/ui`) in `services/web`
  (`npm run -s lint`, `npm run -s build`, `npx playwright test`). Prod Python
  is 3.12 (dev venv is 3.14): no 3.13+/3.14-only syntax, no annotation tricks
  that only pass locally.
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` fixtures in
  `tests/conftest.py` and sqlite; keep every new table portable to sqlite
  (JSON columns, no Postgres-only DDL outside `dialect == "postgresql"` guards,
  as `db.py:init_db` already does for the memory schema).
- Migrations are additive and live in `db.py`: `Base.metadata.create_all`
  makes new tables, `_ensure_columns` adds new columns to existing ones, and
  one-off backfills go in a new `_ensure_*` function called from `init_db`,
  idempotent, guarded by "does the thing already exist".
- Kafka: topics are constants in `agentplatform/events.py` (`ALL_TOPICS` must
  include them) AND `charts/agent-platform/values.yaml` `topics.specs`
  (retentionMs quoted as a string). Use `make_envelope` / `Producer` /
  `FakeProducer` / `consume_forever` from `events.py`; never a bare
  aiokafka client.
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no "added by" notes, no TODOs without an owner.
- Web pages: register the route in `services/web/src/App.tsx`, the nav in
  `packages/ui/src/sidenav.tsx` `buildPlatformNav`, add fixtures to
  `services/web/tests/mock-api.ts` (unmatched GETs fail tests on purpose), and
  a row in `tests/smoke.spec.ts` `PAGES`. Use `@ap/ui` subpath imports like
  `AgentChat.tsx` does. No raw hex colours (design-system lint).
- Never widen scope. If the task needs something the design didn't specify,
  choose the simplest option consistent with the design and say so in your
  report. Do not touch deploy, helm values for the NUC, or secrets.
- Report: files changed, test commands with their final summary lines, and
  any decision you made that the task text did not pin down.

## Tasks

### Phase 1 — data model, migration, API

- [ ] **T1 Schema: channels, messages, participants, sessions, bindings, wakes, invocations.**
  Modify `services/backend/agentplatform/db.py`: extend `Conversation` with
  `kind` (default `"dm"`), `name`, `topic`, `open` (bool), `archived_at`; add
  models `RelayParticipant`, `RelayMessage`, `RelayReaction`,
  `RelaySession`, `RelayBinding`, `RelayWake`, `RelayInvocation` exactly
  as the design's "Data model" section; add `AgentDef.icon` (nullable str).
  Add `_ensure_relay_backfill(conn)` to `init_db`: copy
  `claude_session_id`/`session_blob` into `relay_sessions` for every
  conversation with a blob, create a binding from `(connector, external_ref)`
  when `connector != "web"`, create the two participant rows per DM, and
  synthesise two `relay_messages` per historical turn (human `user_message`,
  then agent `result` with `run_id`, `hop 0`), skipping conversations that
  already have messages. Seed channels `general`, `ops`, `standup`
  (`kind=channel, open=true`) if absent. Tests in
  `services/backend/tests/test_relay_schema.py`: models round-trip on sqlite;
  backfill is idempotent (run `init_db` twice); a legacy conversation with two
  turns yields four messages in order; seeds exist.

- [ ] **T2 Settings + participants + mention parsing library.**
  Add to `config.py`: `relay_max_hops=4`,
  `relay_channel_invocations_per_hour=30`,
  `relay_global_invocations_per_hour=120`,
  `relay_agent_cooldown_seconds=20`, `relay_context_messages=30`,
  `relay_default_grant=True`, each with a why-comment in the file's voice.
  New module `services/backend/agentplatform/relay.py` with pure functions:
  `parse_mentions(body, agents: set[str], author) -> list[str]` (dedupe, drop
  self, `@all` only for non-agent authors, returns `["*"]` for `@all`),
  `strip_room_mentions(body)` (for agent authors), `participant_of(agent|principal)`,
  `face_for(name) -> {emoji, hue}` (deterministic from a hash over a curated
  emoji list of ~40 friendly faces/objects), `is_member(channel, participant,
  enabled_agents)` honouring `open`. Tests in `tests/test_relay_lib.py`.

- [ ] **T3 API: channels, messages, DM, reactions, search, presence, stats.**
  New router `services/backend/agentplatform/api/relay.py` mounted in
  `api/app.py`, schemas in `api/schemas.py` (`RelayChannel`, `RelayMessage`,
  `RelayPresence`, `RelayStats`, …). Implement every route in the design's
  "API" table except SSE and bindings (T6, T12). Posting inserts the row,
  resolves mentions via `parse_mentions`, publishes `relay.messages` (add
  `TOPIC_RELAY_MESSAGES`, `TOPIC_RELAY_INVOCATIONS` to `events.py` and
  `values.yaml` topics). Authorship: human routes use `INVOKE_ROLES`/`READ_ROLES`
  and the principal name; a `relay`-role token (T5) may only act as
  `request.state.api_key_agent` and only in channels it is a member of (403
  otherwise). `presence` derives from `Run` rows in `ACTIVE_STATES` with
  `conversation_id` set plus agent def state. `stats` counts from
  `relay_messages`/`relay_invocations` over 24h and reports both budgets'
  usage. Tests `tests/test_relay_api.py`: post→message has mentions and a
  Kafka envelope on the `FakeProducer`; agent token cannot post as another
  agent; non-member 403; DM get-or-create is idempotent; search finds a word;
  presence shows `thinking` for an active channel run.

- [ ] **T4 Compatibility facade: `/api/conversations` over DM channels; recorder posts replies as messages.**
  Modify `conversation.py`: `_history` reads `relay_messages` (falling back
  to runs only when a channel has no messages), `continue_conversation`
  inserts the human message (author = participant of `requested_by`), keeps
  the one-turn-in-flight rule for `kind=dm` only. Modify `recorder.py`: on a
  terminal result for a run with `conversation_id`, insert the agent's reply
  as a `relay_messages` row (`run_id`, `hop` = triggering message hop + 1 for
  `trigger=="mention"`, else 0, `reply_to` = the triggering message's thread),
  publish `relay.messages`, and keep publishing `conversation.outbound` only
  when the channel has a binding (look up `relay_bindings`, fall back to the
  legacy columns). On a failed/timed-out run in a channel, insert a `system`
  message "😵 {agent} couldn't answer: {short error}". The exactly-once
  `_claim_reply` stays the gate for both. Modify `api/runs.py` session
  endpoints to read/write `relay_sessions` keyed by `(run.conversation_id,
  run.agent)` (migrated rows make this transparent). Tests: existing
  conversation tests still pass; a finished conversation run yields a message
  with `run_id`; a failed run yields a system message; session round-trip via
  the new table.

### Phase 2 — the tool and the grant

- [ ] **T5 `relay` role + default grant.**
  `agentspec.py`: add `PLATFORM_MCP_RELAY_TOOLS = ("mcp__platform__relay",)`
  with a `TOOL_HELP` entry (not sensitive), include it in
  `GRANTABLE_PLATFORM_TOOLS`/`AVAILABLE_TOOLS`, keep it out of the
  annotator-promoting list. `api/auth.py`: add role `relay` to `ROLES`; the
  relay API accepts it. `joblauncher.py` `_platform_token_role`: relay-only
  → `relay`; relay plus annotator tools → `annotator` (higher wins).
  Default grant: in `api/agents.py` create/import paths and the agent wizard
  server side, add `mcp__platform__relay` to `platform_tools` when
  `settings.relay_default_grant` and the caller did not explicitly exclude
  it; add `_ensure_relay_default_grant(conn)` backfill in `db.py` that adds
  the grant to every enabled agent lacking it, recorded through the design-15
  version log with `changed_by="platform:relay-default-grant"` (once,
  guarded by a marker row or by checking the version log). Tests: role
  ladder table-driven; a new agent has the grant; backfill idempotent; an
  admin `agents_grant` removal sticks (backfill does not re-add — record the
  removal as the marker).

- [ ] **T6 Broker tool `relay` + run context prompt + SSE.**
  `services/mcp-broker/broker.py`: add core tool `relay(action, channel?,
  body?, reply_to?, limit?, before?, to?, message_id?, emoji?, q?)` forwarding
  the caller's headers to `/api/relay/*`; action enum and a docstring that
  teaches the agent the hop rule in one sentence. `services/backend/agentplatform/relay.py`:
  `build_mention_prompt(channel, messages, mention_message, agent, hops_left,
  participants)` producing the design's context prompt with
  `<relay-messages author=… at=…>` attributed untrusted blocks. API: add
  `GET /api/relay/channels/{id}/events` (SSE) fed by a single in-process
  `relay.messages` consumer in the API (`api/app.py` lifespan; group id
  `api-sse-<pod>` with `auto_offset_reset=latest`), fanning out to per-channel
  asyncio queues; heartbeat every 15s; also emits `presence` when a run in the
  channel starts/ends (derive from `run.events` in the same consumer).
  Tests: prompt golden test; SSE endpoint streams a message posted after
  connect (use the FakeProducer + direct fan-out hook); broker tool unit test
  mirrors the existing core-tool tests.

### Phase 3 — the router

- [ ] **T7 `RelayRouter` with all loop guards.**
  New `services/backend/agentplatform/relay_router.py`, hosted in
  `dispatcher_main.py` next to `ConversationIngestor`, consumer group
  `relay-router`. Implement steps 1–5 of the design's "router" section
  exactly: parse, hop check, budgets (count `relay_invocations` in the last
  hour), cooldown + `relay_wakes` coalescing (fire the follow-up when the
  recorder's reply message for that agent arrives), invoke via
  `materialize_run` with `trigger="mention"`, `depth=hop`, `initiated_by`
  inheritance, `user_message` = `build_mention_prompt(...)`. Every decision
  writes `relay_invocations` and publishes `relay.invocations`. System
  messages for `hop_limit` (per thread) and `budget` (once per channel per
  hour) are posted through the same insert+publish helper the API uses.
  Tests `tests/test_relay_router.py` (table-driven, FakeProducer, sqlite):
  human mention invokes at hop 0; agent reply at hop 4 mentioning another
  agent is suppressed with a system message; A↔B ping-pong stops after
  `max_hops` runs; three mentions while busy → one wake → one follow-up run
  whose prompt contains all three; channel budget suppresses the 31st in an
  hour and posts exactly one system message; agent `@all` is stripped; human
  `@all` invokes every member; self-mention ignored; `initiated_by` survives
  the chain.

### Phase 4 — web UI  `[ui]`

- [ ] **T8 `[ui]` Relay page: rail + messages + compose, live via SSE.**
  `services/web/src/pages/Relay.tsx` (+ `components/relay/*`): three-pane
  layout per the design's "Web UI" section; faces from `face_for` mirrored in
  TS (`lib/face.ts`, same hash + same emoji list — add a shared JSON fixture
  test so the two stay in sync); presence dots; "x is thinking…" row; SSE
  via `EventSource` with 5s polling fallback; `@` autocomplete over
  `/api/agents`; reactions; "view run ↗" linking `/runs/{run_id}`; system
  rows muted; `?channel=` and `?thread=` in the URL. Route `/relay`, nav
  entry **Relay** top-level with children Channels (`/relay`) and DMs
  (`/relay?kind=dm`); `/conversations` redirects to `/relay?kind=dm`;
  `AgentDetail` Conversations tab renders the DM through the new message
  pane. Fixtures + smoke rows + a11y path. Empty state for a channel with no
  messages must be inviting, not blank.

- [ ] **T9 `[ui]` Thread pane, search, dashboard tile, Help page.**
  Thread pane opens on reply; search box hits `/api/relay/search`;
  Dashboard gets a Relay `Stat` tile (messages 24h, invocations 24h,
  suppressed 24h, budget gauges) linking to `/relay`; write
  `docs/building-blocks/relay.md` (Help auto-renders it) in the voice of the
  sibling pages; update `docs/building-blocks/conversations.md` to point at
  Relay; add the glossary entries (Relay, channel, DM, thread, hop, wake).

### Phase 5 — Discord bridge

- [ ] **T10 Bindings API + connector channel mirroring + per-agent webhooks.**
  API: `POST/GET/DELETE /api/relay/channels/{id}/bindings`. Recorder/API:
  when a message lands in a bound channel, publish `conversation.outbound`
  with `{channel_id, connector, external_ref, author, text, kind}` (extend the
  payload, keep old fields). `services/connector-discord/connector.py`:
  consume outbound for bound **channels** (not only threads): resolve the
  Discord channel, get-or-create a webhook named `relay` there, post with
  `username=<agent name>` (system messages post as `Relay`); inbound: any
  message in a bound Discord channel (author not a bot, no `webhook_id`) →
  `conversation.inbound` with `external_ref=<channel id>` and `text`
  unchanged (plain-text `@news` routes like an in-app mention); the existing
  mention-the-bot thread flow is untouched. `conversation_ingest.py`: resolve
  `external_ref` through `relay_bindings` first. Tests: ingest maps a bound
  ref to the channel; outbound envelope carries `author`; connector unit
  tests for the webhook path with a fake discord client (mirror whatever the
  existing connector tests do; if none exist, add a minimal fake).

### Phase 6 — delight

- [ ] **T11 `#standup` job, `#ops` alerts, Settings toggle.**
  Seed a system Job "relay-standup" (agent `pai` or a new tiny system agent
  `relay-host` with the `relay` grant only) with cron `0 9 * * *` timezone
  `America/Toronto`, prompt: post to `#standup` the message
  "@all — what did you do in the last 24h? Two lines, link anything you
  touched." (the router fans it out; the agent itself posts nothing else).
  health-monitor: its alert prompt gains "also post the alert to Relay
  `#ops` with the run linked" (edit through the design-15 change log via the
  API, not files). Settings page: toggle for `relay_default_grant` and a
  read-only view of the four guard values. Tests: seeding idempotent; the
  Settings API round-trips the toggle.

### Phase 7 — ship it

- [ ] **T12 Build, deploy to the NUC, live-verify a real agent conversation.**
  Build `agent-platform-backend:dev`, `agent-platform-web:dev`
  (`npm run build -w web` first, `Dockerfile.prebuilt`),
  `agent-platform-mcp-broker:dev`, `agent-platform-connector-discord:dev`
  for `linux/amd64` with `--provenance=false --load`
  (`DOCKER_HOST=unix://$HOME/.rd/docker.sock`); `docker save` + scp + `sudo
  k3s ctr -n k8s.io images import` on pai; `helm upgrade ap
  charts/agent-platform -n agent-platform -f <stored values> -f
  charts/agent-platform/values-pai-nuc.yaml` (read
  `docs/deployment.md` and memory `pai-nuc-hardware-and-recovery` first:
  never `--reuse-values`); rollout restart api/dispatcher/recorder/web/
  mcp-broker/mcp-facade/connector-discord. Live verify through the API
  (Terminal.app path): post "@news @health-monitor say hi to each other and
  then stop" in `#general` as admin; confirm two `invoked` invocations, two
  agent messages with `run_id`, and that the exchange ends by the hop cap or
  naturally with a system row if capped; confirm `#standup` job exists; open
  the Relay page in a browser screenshot if possible. Record results here
  under "Live verification".

- [ ] **T13 Memory + docs close-out.**
  Update `docs/design/19-*.md` Status line to shipped with the date, add an
  "AS BUILT" section for any deltas, update `docs/design/00-overview.md` row
  19 if the one-line changed, and write the agent-platform memory file
  `agent-platform-relay.md` (index line in MEMORY.md) recording what shipped,
  the live-verification evidence, and gotchas. Send the final
  PushNotification.

### Repairs
(added by the loop when the definition of done fails)

### Deferred
(low/medium review findings not fixed; each with file:line and one sentence)

## Definition of done

All T1–T13 are `[x]`; `cd services/backend && .venv/bin/python -m pytest -q`
is green; `cd services/web && npm run -s lint && npm run -s build && npx
playwright test` is green; the NUC runs the new images (`kubectl get pods`
all Running, `GET /api/relay/stats` answers); "Live verification" below
shows two different agents exchanging messages in `#general` with linked
runs; the Relay page renders on the NUC; `#standup` job is scheduled.

## Live verification
(filled in by T12: timestamps, message ids, run ids, invocation decisions, screenshot path)

## Handoff to Kyle
(commands the loop could not run because the auto-mode classifier refused them, ready to paste)
