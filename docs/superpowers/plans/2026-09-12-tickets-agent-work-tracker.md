# Plan — Tickets, the agent work tracker (design 20)

Design: `docs/design/20-tickets-agent-work-tracker.md`. Vision:
`docs/vision/agent-ecosystem.md`. Relay, which this builds on:
`docs/design/19-relay-agent-messenger.md` (read its "AS BUILT" section; the
plan `docs/superpowers/plans/2026-09-11-relay-agent-messenger.md` records how
Relay was built and what was deferred). This file is the **single source of
state** for the build: the loop re-reads it every tick, executes the first
unchecked task, and ticks the box with the commit hash. Anyone (Kyle, a fresh
session) can resume from it.

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
   their final summary lines. Tasks marked `[parallel with Tn]` may be
   dispatched in the same tick as that task, one implementer each; tell each
   implementer which paths the other owns and that it must not touch them.
3. When it reports, **verify the evidence yourself**: run the test commands it
   named (backend: `cd services/backend && .venv/bin/python -m pytest -q
   <paths>`; web: `cd services/web && npm run -s lint && npm run -s build &&
   npx playwright test --reporter=line`; broker: `cd services/mcp-broker &&
   python -m pytest -q`). Do not trust a claim of green without output in
   your own transcript. Run git from the repo root, never from a `cd`.
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text, the
   design excerpt, and `git diff` of the uncommitted changes. Ask for
   findings ranked by severity, defects only, no style — and for every guard,
   limit or permission check in the diff, ask it to state the WORST CASE (what
   a hostile or looping agent could do through it). For tasks tagged `[ui]`,
   dispatch additionally a **sonnet visual reviewer** that starts
   `npm run preview` in `services/web` against the Playwright mock API
   (`tests/mock-api.ts`), screenshots the affected page(s) at 1280×800 and
   390×844 in both themes with a throwaway spec, READS the PNGs, and reports
   what a picky human would notice (alignment, contrast, truncation, dead
   space, unreadable states, missing empty/loading/error states). Never use
   `model: "fable"` or the default model for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" with file:line and move on. Loop at most twice per
   task; if a task still fails after two repair rounds, mark it `- [!]` with a
   one-paragraph note and continue to the next task — do not stall the build.
6. **Hold every commit until no implementer is editing the tree**: the
   pre-commit hook stashes and restores unstaged tracked files and would race
   a live editor. Commit on `main` (Kyle's workflow: single branch, direct
   commits, `git add` each file by name, never `-A`; never add files that may
   hold secrets). Message: `feat(tickets): <task title>` plus a body, and the
   attribution trailer the session was given. Then edit this file:
   `- [x] **Tn …** (commit `<hash>`; <one line of what review changed>)`.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task (or one parallel pair)
   per tick keeps each turn's context small.
8. Recovery. A quota 429 kills a subagent instantly and it wrote nothing:
   relaunch it with the same prompt. A "stalled: no progress" agent keeps its
   context: SendMessage "resume from where you stopped". Terminal.app stops
   executing `do script` after ~20 stale windows: quit and relaunch it. A
   Playwright a11y spec that fails only under the full suite: rerun that file
   alone before calling it red.
9. If a Bash action is refused by the auto-mode classifier (kubectl
   apply/delete, helm upgrade), do not retry variants. Write the exact
   commands into "Handoff to Kyle" below, send a PushNotification saying the
   build is blocked on a deploy step, and continue with any task that does not
   depend on it. Reaching the NUC works via Terminal.app:
   `osascript -e 'tell application "Terminal" to do script "<cmd> > <scratchpad>/x.out 2>&1; echo EXIT=$? >> <scratchpad>/x.out; exit"'`
   then wait for `EXIT=` in the file (plain `ssh pai` from this process gets
   "No route to host" — macOS Local Network permission, see memory
   `claude-code-local-network-tcc-gotcha`). The API is reachable from this
   process only through `ssh -f -N -L 18090:localhost:8090 pai` (started the
   same way) at `http://localhost:18090`; Playwright screenshots of the live
   site go through that forward too.

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  run with `cd services/backend && .venv/bin/python -m pytest -q`). Web is
  React 19 + Vite + `@ap/ui` (`packages/ui`) in `services/web`
  (`npm run -s lint`, `npm run -s build`, `npx playwright test`). The MCP
  broker is `services/mcp-broker/broker.py` (tests `python -m pytest -q`
  there). Prod Python is 3.12 (dev venv is 3.14): no 3.13+/3.14-only syntax,
  no annotation tricks that only pass locally.
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` fixtures in
  `tests/conftest.py` and sqlite; keep every new table portable to sqlite
  (JSON columns, no Postgres-only DDL outside `dialect == "postgresql"`
  guards, as `db.py:_ensure_relay_ddl` does).
- Migrations are additive and live in `db.py`: `Base.metadata.create_all`
  makes new tables, `_ensure_columns` adds new columns to existing ones, and
  one-off backfills go in a new `_ensure_*` function called from `init_db`,
  idempotent, gated by a `schema_marks` row (see `RELAY_GRANT_MARK`).
- Relay is the substrate. A ticket's card, its system rows, its assignment
  mention and its comments are Relay messages written through
  `relay_store.post_relay_message` + `publish_relay_message` — never a bare
  insert into `relay_messages`, never a second Kafka producer. Authorship is
  the caller's participant string from the token (`participant_of`), never an
  argument. A message posted for a per-run token carries `hop = run.depth +
  1` exactly as `api/relay.py` does today; reuse that path.
- Kafka: topics are constants in `agentplatform/events.py` (`ALL_TOPICS` must
  include them) AND `charts/agent-platform/values.yaml` `topics.specs`
  (retentionMs quoted as a string). Use `make_envelope` / `Producer` /
  `FakeProducer` / `consume_forever` from `events.py`; never a bare
  aiokafka client. Tests inject `FakeProducer`; nothing in a test may open a
  real Kafka connection (see how `test_relay_sse.py` injects the feed).
- Roles: `api/auth.py` `ROLES`/`READ_ROLES`/`INVOKE_ROLES`; the per-run
  `relay` role and `require_relay_access` in `api/relay.py` are the pattern
  for "as the agent, scoped to its rooms". `agentspec.PLATFORM_MCP_RELAY_TOOLS`
  is the grant list that yields that role.
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no "added by" notes, no TODOs without an owner.
- Web pages: register the route in `services/web/src/App.tsx`, the nav in
  `packages/ui/src/sidenav.tsx` `buildPlatformNav`, add fixtures to
  `services/web/tests/mock-api.ts` (unmatched GETs fail tests on purpose), and
  a row in `tests/smoke.spec.ts` `PAGES`. Use `@ap/ui` subpath imports like
  `Relay.tsx` does; reuse `components/relay/*` (faces, ThreadPane, the
  `useChannel` hook's SSE/backoff shape) rather than re-implementing. No raw
  hex colours (design-system lint). Mobile (<560px) must not break the shell.
- After any change to `services/backend/agentplatform/api/*` run
  `sdk/regenerate.py` with a Python 3.12 venv's `bin` FIRST on `PATH` (the
  session scratchpad has `sdkvenv`; `uv venv --python 3.12` makes another) so
  the SDK drift check stays green; commit the regenerated files with the task.
- Never widen scope. If the task needs something the design didn't specify,
  choose the simplest option consistent with the design and say so in your
  report. Do not touch deploy, helm values for the NUC, or secrets. Never
  commit — the orchestrator commits.
- Report: files changed, test commands with their final summary lines, and
  any decision you made that the task text did not pin down.

## Tasks

### Phase 1 — data model and the pure library

- [ ] **T1 Schema: tickets, ticket_events, project prefixes, Run.ticket_id, seeds.**
  Modify `services/backend/agentplatform/db.py`: add `Ticket` and
  `TicketEvent` exactly as the design's "Data model" section (states and
  priorities as `StrEnum`s); add `Conversation.ticket_prefix` (nullable
  String(8)) and `Conversation.ticket_seq` (Integer, default 0) via
  `_ensure_columns`; add `Run.ticket_id` (nullable String(32), indexed).
  Add `_ensure_tickets_ddl(conn)` for the postgres-only pieces (a unique
  index on `conversations.ticket_prefix` where not null, a GIN tsvector index
  over `tickets.title || ' ' || tickets.body`) guarded by dialect like
  `_ensure_relay_ddl`. Add `_ensure_tickets_seed(conn)` gated by a
  `schema_marks` row `tickets-seed-v1`: set `ticket_prefix` `GEN` on
  `general` and `OPS` on `ops` where still null. Call both from `init_db`
  after the relay ensures. Tests in `services/backend/tests/test_tickets_schema.py`:
  models round-trip on sqlite; `init_db` twice is idempotent; seeds present;
  a channel's prefix is unique (sqlite: enforce it in the API later, here just
  assert the model allows null on dms).

- [ ] **T2 Settings + `tickets.py` pure library.**
  Add to `config.py` with why-comments in the file's voice:
  `tickets_agent_creates_per_hour=20`, `tickets_stale_days=3`,
  `tickets_default_grant=True`, `tickets_thread_context_messages=40`.
  New module `services/backend/agentplatform/tickets.py` with pure functions:
  `STATES`, `CLOSED_STATES`, `can_move(from_state, to_state) -> bool`
  (any → any, except a closed state only goes to `open` and that is the
  `reopened` event), `derive_prefix(name, taken: set[str]) -> str` (2–6
  uppercase letters from the words of the name, then padded from the name's
  letters, then suffixed with a digit until unique; `general`→`GEN`,
  `health-monitor`→`HM`, `ops`→`OPS`), `KEY_RE` and
  `find_ticket_refs(body, prefixes) -> list[str]` (keys mentioned in text,
  deduped, not inside code fences — reuse the fence stripping
  `relay.parse_mentions` uses), `card_for(ticket, *, url_base) -> dict`
  and `card_body(ticket) -> str` (the plain-text body from the design),
  `event_row_text(event, actor_label) -> str` (the system-row sentence for
  each event kind), `is_stale(ticket, now, days)`, `budget_body(limit)`.
  Golden tests in `tests/test_tickets_lib.py` for every function, including
  prefix collisions and refs inside fences.

### Phase 2 — the store, the API, the events

- [ ] **T3 `ticket_store.py`: the ONE place a ticket changes.**
  New module `services/backend/agentplatform/ticket_store.py` with async
  functions taking `(session, producer, …)`: `create_ticket` (allocates the
  key under the channel row — `with_for_update()` on postgres, plain on
  sqlite — inserts the row, posts the card message as `kind=event` through
  `relay_store.post_relay_message` with `card_for`/`card_body`, sets
  `root_message_id`, writes a `created` event, optionally assigns with
  notify), `move_ticket` (validates with `can_move`, sets `closed_at`,
  edits the card, posts the system row in the thread, writes `moved` or
  `reopened`), `assign_ticket` (edits the card; with `notify` posts
  `@<agent> you've been assigned KEY[: reason]` in the thread AUTHORED BY THE
  ACTOR with the actor's hop, mentions parsed by `parse_mentions`; without
  notify posts a system row instead; writes `assigned`), `update_ticket`
  (`edited`), `comment_ticket` (a reply in the thread as the actor;
  `commented`), `agent_create_budget_left(session, agent, limit, now)`.
  Every function publishes the ticket event on the new topic
  `TOPIC_TICKETS_EVENTS = "tickets.events"` (add to `events.py`
  `ALL_TOPICS` and to `values.yaml` `topics.specs`, retention
  `"2592000000"`) with payload `{event, ticket}` where `ticket` is the view
  dict, and touches `last_activity_at`. Add to `relay_store.py`
  `edit_message_card(session, producer, msg, *, card, body)` that sets
  `card`, `body`, `edited_at`, and re-publishes the message through
  `publish_relay_message` so the SSE feed re-sends it. Tests in
  `tests/test_ticket_store.py` with `FakeProducer`: key allocation is
  sequential per prefix; a create posts exactly one card message with the
  right body; a move edits the card in place (same message id, new state,
  `edited_at` set) and posts one system row threaded under the card; an
  assign with notify by a human posts a hop-0 mention resolving to the agent;
  the same by an agent run at depth 2 posts hop 3; assign without notify
  posts no mention; `can_move` violations raise; the budget counts only
  `created` events by that actor in the window.

- [ ] **T4 API `/api/tickets/*`, the `relay` role widened, default grant, SSE, stats.**
  New router `services/backend/agentplatform/api/tickets.py` implementing
  every route in the design's "API" section over `ticket_store`, with
  `api/schemas.py` models. Auth: reuse `require_relay_access` from
  `api/relay.py` — widen the `relay` role's path scope in `api/auth.py` so it
  reaches `/api/tickets/*` as well; an agent may read tickets only in rooms
  it is a member of (`is_member`) and may write only as itself. Agent
  creates go through `agent_create_budget_left` → 429 with the budget body,
  and one system row per project channel per hour. `PATCH
  /api/relay/channels/{id}` gains `ticket_prefix` (validated with `KEY_RE`'s
  prefix part; refused with 409 once a ticket exists under the channel; 409
  on collision). `GET /api/tickets/events` is SSE fed from `tickets.events`:
  refactor `relay_feed.RelayFeed` into a topic-parameterised `TopicFeed`
  (relay keeps its behaviour and its tests) and instantiate one for
  tickets; the stream sends `ticket` frames (the view dict; clients upsert
  by id), `heartbeat`, and `overflow`. `GET /api/tickets/stats` returns the
  design's fields (stale via `is_stale`, orphaned = assignee is a disabled or
  missing agent, `moved_24h` grouped by actor with faces from
  `relay_store.faces_for`, budget per agent for the top offenders). `GET
  /api/tickets/{key}` returns ticket, events, `root_message_id`, linked runs
  (`Run.ticket_id == id`, newest first, ten), and `thinking` (an active run
  on the ticket). Default grant: `agentspec.PLATFORM_MCP_RELAY_TOOLS` gains
  `mcp__platform__tickets`; a `PLATFORM_TOOLS` registry entry with a
  description in the voice of the relay one; agent creation adds the grant
  while `settings.tickets_default_grant` (mirror the relay tri-state in
  `api/agents.py`); `_ensure_tickets_default_grant(conn, default_grant)` in
  `db.py` gated by `tickets-default-grant-v1`, appending a version row like
  the relay one. Register the router. Regenerate the SDK. Tests in
  `tests/test_tickets_api.py`: every route happy path; a reader can read and
  not write; an agent token lists only its rooms' tickets and cannot set
  `reporter`; the create budget 429s on the 21st create in an hour; prefix
  PATCH rules; the SSE stream yields a `ticket` frame for a published event
  (inject the feed the way `test_relay_sse.py` does); stats counts. Add a
  `relay_default_grant`-style assertion that a new agent holds both grants.

### Phase 3 — the agents' side

- [ ] **T5 Broker tool `tickets`.** `[parallel with T6]` (owns
  `services/mcp-broker/*`, `sdk/`, `agentspec.py` registry text only)
  In `services/mcp-broker/broker.py` add `@mcp.tool async def tickets(action,
  …)` with the design's eight actions, next to `relay` and in its voice:
  channel resolution through `_relay_channel`; `key` accepted as `OPS-12` or
  an id; `list` defaults to "mine, not closed"; `get` returns the ticket,
  its events, the last `tickets_thread_context_messages` thread messages and
  linked runs in one readable JSON; every error is a plain `error: …` string
  the model can act on (missing args, unknown state, budget). The docstring
  tells the agent when to move a ticket and that it cannot close what it did
  not do. Tests in `services/mcp-broker/test_tickets_tool.py` mirroring
  `test_relay_tool.py` (patched `_call`): each action's request shape, the
  key/id resolution, the error strings, and that authorship is never an
  argument.

- [ ] **T6 Thread-aware summons: `Run.ticket_id`, the `<ticket>` prompt, `<your-tickets>`, standup v2, health-monitor prompt.** `[parallel with T5]` (owns `relay.py`, `relay_router.py`, `relay_store.py`, `scheduler.py`, `db.py` seeds, their tests)
  In `relay_store.context_window` add a thread shape: when the summoning
  message has a `thread_root` (or is a root with replies), the window is the
  root plus its replies (last `tickets_thread_context_messages`), not the
  room page; `since_message_id` still wins for coalesced wakes. In
  `relay_router.py`, when the summoning message's thread root is a ticket
  card (`Ticket.root_message_id`), set `ticket_id` on the run spec (add it
  to `materialize_run`'s accepted fields) and pass the ticket to the prompt
  builder. In `relay.build_mention_prompt` add an optional `ticket` (a
  `<ticket>` block first: key, title, state, priority, assignee, reporter,
  body, last five events) and an optional `your_tickets` list (a
  `<your-tickets>` block after the messages, up to ten one-liners); extend
  `_RULES` with the move/blocked/never-close-what-you-did-not-do sentences
  from the design. Keep the golden tests deterministic and add goldens for
  both shapes. Standup v2: a `tickets-standup-v2` marker in `db.py` that
  rewrites `relay-standup`'s prompt to "@all — what did you do in the last
  24h, which tickets did you move, and what is blocked? Two lines each, link
  what you touched." only if the prompt is still the v1 text. health-monitor:
  in the same ensure, if the agent exists and its prompt contains the T11 #ops
  sentence, append the design's ticket instruction (open an OPS ticket for
  anything that needs a human, assign to pai if it is about the platform, put
  the alert in the thread) and log a version row `changed_by=system:tickets`.
  Tests: `test_relay_router.py` (a summons in a ticket thread sets
  `ticket_id` and gets the thread window; one outside gets the room window
  and the `<your-tickets>` block), `test_relay_lib.py` goldens,
  `test_tickets_schema.py` for both ensures being idempotent and prompt-safe.

### Phase 4 — web UI  `[ui]`

- [ ] **T7 `[ui]` Board page `/tickets`: columns, live cards, filters, Today strip, new-ticket dialog, drag-to-move.** `[parallel with T8]` (owns `pages/Tickets.tsx`, `components/tickets/Board*`, `lib/tickets.ts`, the `useTickets` hook, `tests/tickets.spec.ts`, the Tickets rows in `mock-api.ts` and `smoke.spec.ts`)
  New `services/web/src/pages/Tickets.tsx` and `components/tickets/`:
  `useTickets` hook (one SSE connection to `/api/tickets/events` with the
  same backoff/catch-up shape as `useChannel`, upsert by id; initial load
  from `GET /api/tickets`), `Board` with the six columns (done + cancelled as
  one "closed · 7d" column), `TicketCard` (key, title, assignee `Face`,
  priority stripe, label chips, stale badge, thinking pulse from `thinking`),
  filters (project, assignee, label, mine) in the URL, `/` focuses search,
  `NewTicketDialog` (`@ap/ui/dialog`), drag between columns → `POST
  …/move` with a "Move to…" menu on each card as the keyboard path (both
  call the same handler), `TodayStrip` from `stats.moved_24h`. Route, nav
  (`{ to: "/tickets", label: "Tickets" }` between Relay and Reporting),
  `lib/tickets.ts` (types, `stateLabel`, `priorityLabel`, `ticketRefs`
  regex over prefixes from `/api/tickets/projects`). Fixtures in
  `tests/mock-api.ts` (projects GEN/OPS, eight tickets across states, one
  stale, one orphaned, one thinking, stats), smoke row `{ path: "/tickets",
  heading: "Tickets", probe: /OPS-1/ }`, `tests/tickets.spec.ts`: board
  renders columns and cards; filters narrow; the move menu posts and the card
  moves; an SSE `ticket` frame moves a card live; empty state; 390px layout
  scrolls columns horizontally inside the board, never the page.

- [ ] **T8 `[ui]` Ticket page, chips in Relay, run link, agent tab, dashboard tile, Help.** `[parallel with T7]` (owns `pages/TicketDetail.tsx`, `components/tickets/Detail*`, `components/relay/Message.tsx`, `pages/RunDetail.tsx`, `pages/AgentDetail.tsx`, `pages/Dashboard.tsx`, `docs/building-blocks/*`, the Ticket-detail rows in `mock-api.ts`/`smoke.spec.ts`)
  New `services/web/src/pages/TicketDetail.tsx` at `/tickets/:key`: fields
  down the side (state, priority, assignee with `Face`, reporter, labels,
  due, project, parent/children, linked runs → `/runs/:id`), move and assign
  controls (assign dialog lists enabled agents + `user:admin`, `notify`
  checkbox default on), and the thread via `components/relay/ThreadPane`
  bound to the project channel with `threadId = root_message_id` (reuse
  `useChannel` for the room; live). Elsewhere: in `components/relay/Message.tsx`
  render ticket keys as chips linking to `/tickets/KEY` (prefixes from
  `/api/tickets/projects`, cached once per page); `RunDetail.tsx` shows a
  `🎫 KEY` link when the run has `ticket_id`; `AgentDetail.tsx` gains a
  "Tickets" tab (assigned / reported lists); `Dashboard.tsx` gets a Tickets
  `Stat` (open / in progress / blocked / done · 24h, `to="/tickets"`) and
  attention rows for blocked, stale and orphaned counts. Help page
  `docs/building-blocks/tickets.md` in the voice of `relay.md` (what a
  ticket is, keys, states, assign = summon, the tool's actions, the guards),
  a row in `docs/building-blocks/README.md`'s table and terms in
  `glossary.md` (ticket, project, key). Fixtures for the detail, smoke rows
  for `/tickets/OPS-1` and `/help/tickets`, `tests/tickets-detail.spec.ts`:
  detail renders fields and the thread; assign posts with notify; a chip in a
  Relay message links to the ticket; the dashboard tile and attention rows
  render from fixtures.

### Phase 5 — ship it

- [ ] **T9 Facade curation (design 17), SDK drift, CI.**
  In `services/mcp-facade/facade.py`: KEEP `GET /api/tickets`, `POST
  /api/tickets`, `GET`/`PATCH /api/tickets/{key}`, `POST …/move`, `POST
  …/assign`, `POST …/comments`, `GET /api/tickets/stats`, `GET
  /api/tickets/projects`; EXCLUDE `GET /api/tickets/events` (SSE); nothing
  new gated (channel PATCH is already gated). Update the docstring counts and
  the two pinned numbers in `test_facade.py` to the real values from a fresh
  spec; add a tickets row to design 17's curation table if it has one.
  Verify with the CI recipe in a 3.12 venv (`uv venv --python 3.12` under the
  scratchpad, `pip install -e services/backend -r
  services/mcp-facade/requirements.txt`). Confirm `sdk/regenerate.py`
  produces no diff and `.github/workflows/ci.yaml` needs no change (add the
  broker's new test file to its job if the job lists files).

- [ ] **T10 Build, deploy to the NUC, live-verify assign-summons-move.**
  Deploy exactly as Relay's T12 did (the session scratchpad's `t12-deploy.sh`
  is the reference: `docker buildx --platform linux/amd64 --provenance=false
  --load` for backend, web (`Dockerfile.prebuilt` after `npm run build`),
  mcp-broker, mcp-facade; `docker save` → `scp` → `sudo k3s ctr -n k8s.io
  images import`; `helm upgrade ap charts/agent-platform -n agent-platform -f
  <stored values> -f values-pai-nuc.yaml` — never `--reuse-values`; rollout
  restart api/dispatcher/recorder/web/broker, then the facade last). Through
  the ssh forward: `init_db` seeded `GEN`/`OPS`, `/api/tickets/stats`
  answers, every enabled agent holds `mcp__platform__tickets`. Then the
  live script (write it under the scratchpad like `t12-verify2.py`): as
  admin, open `OPS-1` in `#ops` assigned to `news` with notify → poll
  `/api/tickets/OPS-1` until `thinking` is set and then until an event
  `moved` by `agent:news` exists or three minutes pass; then post in `#ops`
  `@health-monitor open a ticket for the noisiest failing agent this week and
  assign it to pai` → poll `/api/tickets?channel=ops` for a ticket with
  `reporter=agent:health-monitor`; then `Run Now` on `relay-standup` and read
  `#standup` for answers that cite a key. Record the evidence tables under
  "Live verification" (when/actor/event/run for each step, hops, budgets
  after). Screenshot `/tickets` and `/tickets/OPS-1` on the NUC at 1280 via
  Playwright through the forward (`t12-shot.mjs` is the reference) into the
  scratchpad and note the paths. If a step fails, add a Repair task rather
  than hand-editing the NUC.

- [ ] **T11 Memory + docs close-out.**
  Design 20: Status → shipped with the date and helm revision, add an "AS
  BUILT" section listing every delta the reviews and the live run forced (the
  ticks above say what they were). `docs/design/00-overview.md`: row 20.
  `docs/vision/agent-ecosystem.md` "Order of work": Tickets links to design
  20 and this plan. Memory: write `agent-platform-tickets.md` (shipped facts,
  live facts, gotchas, follow-ups) and add its index line; update
  `agent-platform-relay.md`'s follow-ups if Tickets closed any. Fill "Handoff
  to Kyle" with anything the loop could not do.

### Repairs
(added by the loop when the definition of done fails)

### Deferred
(low/medium review findings not fixed; each with file:line and one sentence)

## Definition of done

All T1–T11 are `[x]`; `cd services/backend && .venv/bin/python -m pytest -q`
is green; `cd services/mcp-broker && python -m pytest -q` is green; the facade
suite is green under the CI recipe; `cd services/web && npm run -s lint &&
npm run -s build && npx playwright test` is green; the NUC runs the new
images (`kubectl get pods` all Running, `GET /api/tickets/stats` answers
through the forward); "Live verification" below shows a ticket assigned by a
human that an agent moved after being summoned, a ticket opened by an agent
through the tool, and a standup answer that cites a key; the board renders on
the NUC (screenshot path recorded).

## Live verification
(evidence tables, written by T10)

## Handoff to Kyle
(commands the loop could not run because the auto-mode classifier refused them, ready to paste)
