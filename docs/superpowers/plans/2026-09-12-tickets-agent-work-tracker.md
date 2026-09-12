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

- [x] **T1 Schema: tickets, ticket_events, project prefixes, Run.ticket_id, seeds.** (commit `544dc43`; review fixed: ticket_seq backfilled to 0 on pre-existing rows, legacy-row test asserts it)
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

- [x] **T2 Settings + `tickets.py` pure library.** (commit `13d5f2c`; review fixed: titles/reasons flattened + room-mentions stripped + capped before entering system rows, is_stale normalises both operands, derive_prefix never yields a letterless prefix)
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

- [x] **T3 `ticket_store.py`: the ONE place a ticket changes.** (commit `e959d4c`; review fixed: actor↔run invariant so no agent mention lands at hop 0, one commit per call with edit_message_card staging-only, row locks on mutation, create budget counted under the channel lock (TicketBudgetError), parent validated + cycle-bounded, agent assignee must be enabled; router freed_by interaction moved to T6)
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

- [x] **T4 API `/api/tickets/*`, the `relay` role widened, default grant, SSE, stats.** (commit `7133d9d`; review fixed: PATCH prefix race is a 409 not a 500, archived projects refuse ticket writes; deferred: grant-independent annotator+ access is design-19's ladder)
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

- [x] **T5 Broker tool `tickets`.** (commit `0637337`; review fixed: key/id path-segment gate closes a traversal (relay react too), full-page and closed-only notes on list, none-sentinels for parent/due, wrapped keys) `[parallel with T6]` (owns
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

- [x] **T6 Thread-aware summons: `Run.ticket_id`, the `<ticket>` prompt, `<your-tickets>`, standup v2, health-monitor prompt.** (commit `2d68d94`; review fixed: root card always kept in the thread window, ping-pong hop-cap router test, health-monitor version race guarded by a savepoint) `[parallel with T5]` (owns `relay.py`, `relay_router.py`, `relay_store.py`, `scheduler.py`, `db.py` seeds, their tests)
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
  **Binding addition from the T3 review:** `relay_router._freed_by` treats ANY
  agent-authored message carrying `run_id` as the run's last word, and both
  `api/relay.py` (the relay tool) and `ticket_store` post mid-run messages with
  `run_id` for attribution — so an agent that comments on or assigns a ticket
  mid-run is marked free early and a pending wake can start a second run of
  the same agent while the first is still executing. Fix it in the router:
  free an agent only on the recorder's reply (it is the one message whose
  `trigger_message_id` is set — the API and the store never set it) or on a
  `kind=system` failure notice with `run_id`; a `kind=text` agent message with
  `run_id` but no `trigger_message_id` is a tool post and frees nobody (the
  `run.events` backstop still fires the wake at the real terminal state). Add
  the mutation-style test: a mid-run tool post while a wake is pending fires
  nothing; the recorder's reply fires it.

### Phase 4 — web UI  `[ui]`

- [x] **T7 `[ui]` Board page `/tickets`: columns, live cards, filters, Today strip, new-ticket dialog, drag-to-move.** (commit `054104d`, shared with T8; visual review fixed: five columns fit at 1280, 28px faces, designed empty state, halo pulse; defect review fixed: legal-move rule mirrored client-side, initial-load retry, `.sr-only` defined, fence-aware refs, in-flight move guard, filtered assignee picker + you, blocked asks a reason) `[parallel with T8]` (owns `pages/Tickets.tsx`, `components/tickets/Board*`, `lib/tickets.ts`, the `useTickets` hook, `tests/tickets.spec.ts`, the Tickets rows in `mock-api.ts` and `smoke.spec.ts`)
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

- [x] **T8 `[ui]` Ticket page, chips in Relay, run link, agent tab, dashboard tile, Help.** (commit `054104d`, shared with T7; review fixed: move select via canMove, overflow refetch, chip rewrite skips code/links/images/autolinks/URLs, prefix cache retries, memoised, ticket cards in rooms draw key/state/assignee face, move error inline, read-ordering race; visual: ship) `[parallel with T7]` (owns `pages/TicketDetail.tsx`, `components/tickets/Detail*`, `components/relay/Message.tsx`, `pages/RunDetail.tsx`, `pages/AgentDetail.tsx`, `pages/Dashboard.tsx`, `docs/building-blocks/*`, the Ticket-detail rows in `mock-api.ts`/`smoke.spec.ts`)
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

- [x] **T9 Facade curation (design 17), SDK drift, CI.** (commit `ee7c301`; KEEP 9 / GATE 0 / EXCLUDE 1 → 73 default, 99 admin, spec-computed; run views gained ticket_id; review: ship, two doc/comment lines applied)
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
  **Addition from T8:** `api/runs.py`'s run detail and list schemas do not
  expose `Run.ticket_id`, so the run page cannot show its `🎫 KEY` link;
  add `ticket_id: str | None` to `RunDetail` (and the list row if cheap)
  in `api/schemas.py`/`api/runs.py`, regenerate the SDK, test it in
  `tests/test_runs_api.py` (or wherever run views are tested).

- [x] **T10 Build, deploy to the NUC, live-verify assign-summons-move.** (helm rev 53 at 17:16 EDT; scenarios 1 and 3 PASS, scenario 2 PARTIAL → R1/R2 below; evidence under "Live verification"; screenshots `scratchpad/live/tickets-board-1280.png`, `tickets-detail-1280.png`)
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

- [x] **R1 A system agent's run token must carry its run, so its ticket writes are not refused.** (commit `486c0d8`; per-run annotator key via `_invoke_token(run, label="system")`, runless `system:*` keys swept once (launcher-written rows only); redeployed; round two: health-monitor opened OPS-3 at hop 1, no 403)
  Live: `agent:health-monitor` got `403 this token has no run to act from` on
  four `tickets action=create` calls. `joblauncher.py` `_system_token(agent)`
  mints ONE annotator ApiKey per system agent with `run_id` NULL (cached for the
  process), so `api/tickets.py::_run_of` cannot resolve the run and refuses every
  write by exactly the agent design 20 tells to open OPS tickets (the relay tool
  tolerated it only by treating a missing run as hop 1). Fix: give a `system:
  true` agent's run a PER-RUN token that carries `run_id` (reuse the
  `_invoke_token(run, role="annotator")` path or mint the system key per run
  with `run_id=run.id`), keep the annotator scope, and let the existing per-run
  key lifecycle (revocation/GC) apply; drop the per-agent cache and its
  replace-predecessor dance if nothing else needs it. Tests in
  `tests/test_joblauncher.py`: a system agent's launch env token resolves to an
  ApiKey with `run_id == run.id`; `tests/test_tickets_api.py`: a system agent's
  per-run token can create a ticket (201) and the event carries `run_id`.
  Then rebuild the backend image, redeploy api/dispatcher/recorder (+ facade),
  and re-run T10 scenario 2 (`scratchpad/t20-verify.py` step2) — record the
  result under "Live verification".

- [x] **R2 A bare agent name as assignee must resolve to `agent:<name>` or be refused.** (commit `486c0d8`; round two: bare `pai` normalised to `agent:pai` and pai was summoned; `nobody-here` → 400 with the syntax)
  Live: pai assigned OPS-2 to `pai` (no prefix); `_check_assignee` validates only
  prefixed participants, so the ticket shows an assignee that summons nobody.
  Fix in `ticket_store` (one place): normalise an unprefixed assignee that names
  an enabled agent to `agent:<name>`; refuse any other unprefixed string with
  `TicketRuleError("assignee must be agent:<name>, user:<name> or discord:<id>")`
  (API → 400). Apply to `create_ticket` and `assign_ticket`. Mirror the
  normalisation in the broker's `tickets` tool for `assignee`/`to` (a plain
  `error:` when it cannot). Low, same change: `POST /api/tickets` should accept a
  bare channel name (`"ops"`) like the broker does, not only `#ops`/id. Tests in
  `tests/test_ticket_store.py`, `tests/test_tickets_api.py`,
  `services/mcp-broker/test_tickets_tool.py`. Ships with R1's redeploy.

### Deferred
(low/medium review findings not fixed; each with file:line and one sentence)
- T2: `derive_prefix` strips digits from names, so a channel literally named `ops2` derives base `OPS` and, when `OPS` is taken, gets the same `OPS2` the collision loop would hand a second `ops`; keys stay globally unique, only prefix→channel inference is ambiguous (nothing does that today).
- T2: `find_ticket_refs` reuses `relay._FENCE`, which recognises backtick fences only; a `~~~` fence is live text for mentions and ticket refs alike (pre-existing gap in relay.py).
- T3: `say_budget_once` is check-then-insert with no lock, so two simultaneous refusals in one channel can post two hourly notices (worst case: a duplicate row, not a missed one).
- T3: the channel-row lock taken for key allocation is held for the whole `create_ticket` transaction (card post, event, announce), so creates in one busy project serialise entirely rather than only on the counter.
- T3: `_next_key`'s postgres `with_for_update` branch has no test (sqlite only in CI); verified live in T10 by opening two tickets back to back.
- T4 (design-19 inheritance, needs its own decision): `require_relay_access` accepts any agent token whose role is `annotator` or above (`api/relay.py` `AGENT_ROLES`), so an agent promoted by `runs_read`/`metrics`/`query_app` reaches `/api/relay/*` and `/api/tickets/*` server-side without holding `mcp__platform__relay`/`mcp__platform__tickets`; the grant is enforced only by the runner's `--allowedTools`. A server-side grant check (read `platform_tools` from the agent store in the dependency) is the fix; it touches every relay test fixture, so it is a follow-up, not a mid-build change. Record in design 20 AS BUILT.
- T4: a create-time `derive_prefix` collision between two simultaneous channel creates is caught by the generic IntegrityError handler and reported as "#name already exists" (right status, wrong reason).
- T7 (pre-existing, cross-cutting): `services/web/src/api.ts` throws `"<status>: <raw body>"`, so every error a page shows is raw JSON (`429: {"detail":"⏸️ paused …"}`); parsing FastAPI's `detail` there would make the budget notice and move refusals read as sentences everywhere. Touches every page's error text and some specs' assertions.
- T7: move errors are one page-level banner overwritten by the next failure rather than a message on the card.
- T2→T3: the once-per-hour budget row is deduped by matching `BUDGET_PREFIX`; the store's query must be scoped to `kind == "system"` rows the way `relay_router._say_budget` is, or a comment starting with that text suppresses the real notice.

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

Deployed to the NUC (`pai`) on **2026-09-12** from the orchestrator's
Terminal.app window: backend/web/mcp-broker/mcp-facade images built at
17:14:51–17:16:24 local, imported into k3s containerd, `helm upgrade` at
17:16:54 → **REVISION 53** (`STATUS: deployed`, "Upgrade complete"), then a
rollout restart of ap-api / ap-dispatcher / ap-recorder / ap-web /
ap-mcp-broker and finally ap-mcp-facade (it reads the API's OpenAPI at boot);
every `kubectl rollout status` returned successfully and the script ended
`### DONE-DEPLOY` / `EXIT=0` at 17:19. Post-deploy state at 17:19:10 showed the
old pods Terminating behind the new ones and the Kafka topic list containing
`relay.invocations relay.messages tickets.events` — the design-20 topic exists.
(`ap-pg-backup-29818560-…` sits in Error: the known harmless catch-up backup
pod, unrelated to this deploy.) Verification ran from this machine through the
ssh port-forward at `http://localhost:18090`; script
`scratchpad/t20-verify.py`, screenshots `scratchpad/t20-shot.mjs`.

### Preflight (21:19:38Z)

| check | result |
| --- | --- |
| `GET /api/tickets/stats` | 200, all counters 0, `budget.creates_per_hour=20`, `stale_days=3` |
| `GET /api/tickets/projects` | 200 — `#general` → `GEN`, `#ops` → `OPS` (both seeded by init_db) |
| `GET /api/agents` | 10 enabled agents, **10/10** carry `mcp__platform__tickets`; none missing |
| `relay-standup` job | prompt is v2 — "@all — what did you do in the last 24h, **which tickets did you move**, and what is blocked? …"; cron `0 9 * * *` America/Toronto, enabled |
| `health-monitor` prompt | contains "Open an **OPS ticket** for anything that needs a human, assign it to pai, and put the alert in the ticket's thread." |

### Scenario 1 — assign = summon (PASS)

Admin opened `OPS-1` ("Live check: confirm the weather dedup is still daily",
p1) in `#ops` assigned to `agent:news` with `notify: true`. The whole exchange
took **20 seconds** end to end.

| when (UTC) | actor | event / message | hop | run id | outcome |
| --- | --- | --- | --- | --- | --- |
| 21:20:00.135 | user:admin | thread root card `🎫 OPS-1 · … — opened by admin` | 0 | — | ticket created (`state=open`) |
| 21:20:00.142 | user:admin | event `created` | — | — | |
| 21:20:00.150 | user:admin | `@news you've been assigned OPS-1` | 0 | — | the summons the assignment produced |
| 21:20:00.152 | user:admin | event `assigned` → `agent:news` | — | — | |
| 21:20:10 | agent:news | `thinking` appears on `GET /api/tickets/OPS-1` | — | `c07a7d9c52c74fd9843e52bc1486bd09` | +10 s after the assign |
| 21:20:16.885 | agent:news | event `moved` `open → in_progress` | — | `c07a7d9c…` | system row in the thread: "news moved OPS-1 → in progress" |
| 21:20:19.780 | agent:news | event `moved` `in_progress → review`, reason "Verified: weather items are always emitted with published=today's date and Whitby/Toronto-specific forecast, so the platform's dedup naturally treats each day's forecast as a new item — no code changes needed." | — | `c07a7d9c…` | final state **review** |
| 21:20:23.741 | agent:news | "First thing I'd do: check that my weather item always carries `published` = today's date …" | **1** | `c07a7d9c…` | the "what would you do first" reply, posted into the ticket thread |

Run `c07a7d9c52c74fd9843e52bc1486bd09` (`agent=news`, `trigger=mention`)
finished `succeeded`; `thinking` cleared afterwards.

Stats around scenario 1: `invocations_24h` **14 → 15 (+1)** — one summons, not
a storm. `suppressed_by_reason` unchanged at `{hop_limit 0, budget 0,
not_member 0, coalesced 4, facade_owns_turn 0}` (delta all zero). Relay budget
`global_used_last_hour` 1 → 2 of 120. Tickets stats went to `review: 1` with
`moved_24h = [agent:news ×2]`.

### Scenario 2 — agents open tickets (PARTIAL — an agent opened the ticket, but not the one asked, and `agent:health-monitor` could not write)

Admin posted in `#ops`: "@health-monitor open a ticket in #ops for the noisiest
failing agent this week …, assign it to pai, and put your reasoning in the
ticket's thread."

| when (UTC) | actor | event / message | hop | run id | outcome |
| --- | --- | --- | --- | --- | --- |
| 21:20:46.731 | user:admin | the summons in `#ops` | 0 | — | mentions `health-monitor` |
| 21:21:43 → 21:22:04 | agent:health-monitor | **4 × `mcp__platform__tickets` `action=create` → `error: 403 {"detail":"this token has no run to act from"}`** | — | `b8f167f579b54ad9b9d099eb01a25ecb` | every ticket write refused |
| 21:22:16.409 | agent:health-monitor | "…I tried 4 times to open the OPS ticket … it's failing with `403 this token has no run to act from` … @pai — could you either create/own the OPS ticket …" | 1 | `b8f167f5…` | run `succeeded`; it delegated instead |
| 21:22:42.978 | agent:pai | `🎫 OPS-2 · Investigate health-monitor failure streak (8 consecutive failures, 2026-09-12) — opened by pai` | **2** | `aa2cc15ceb0245dcaecd6689734936eb` | ticket **OPS-2** created, `reporter=agent:pai` |
| 21:22:42.983 | agent:pai | event `assigned` → `pai` | — | `aa2cc15c…` | see the note below — **no summons fired** |
| 21:22:50.008 | agent:pai | event `commented` — "Reasoning (from health-monitor's health check, relayed by pai): … 850 failed / 3850 total runs … run-summarizer shares the *exact* last_failed_at (19:00:03Z) → points to a shared platform-side cause …" | 2 | `aa2cc15c…` | the reasoning landed in the ticket thread as asked |
| 21:22:55.501 | agent:pai | "Done ✅ Created **OPS-2** … assigned to me (pai), with your full reasoning posted in the ticket thread." | 2 | `aa2cc15c…` | reported back in the summons thread |

So the **capability** is live — an agent opened a ticket through the tool, at
hop 2, with its reasoning in the thread, and the hop cap held (no further
wake). Two defects fell out of it:

1. **A `system: true` agent cannot write to the board.**
   `api/tickets.py::_run_of` (writing=True) 403s when
   `request.state.api_key_run_id` is empty, and
   `joblauncher.py:432` gives a system agent
   `_system_token(run.agent)` — an ApiKey row minted **per agent with
   `run_id` NULL** (`joblauncher.py:46-67`), not the per-run token
   `_invoke_token` mints. health-monitor is `system: true`, so every ticket
   create/move/assign/comment it attempts is a 403, while `news` and `pai`
   (both `system: false`) wrote fine in the same session. This hits exactly
   the agent whose prompt design-20 told to open OPS tickets.
2. **A bare agent name as `assignee` is silently treated as a human.**
   pai passed `assignee: "pai"`; `ticket_store._check_assignee` only validates
   participants that already carry the `agent:` prefix (`relay.agent_name`
   returns None otherwise), and the broker's `tickets` tool forwards the
   argument verbatim (`broker.py:684`). OPS-2 therefore stored
   `assignee = "pai"`, no `@pai` summons was sent, and nothing in the room said
   so — the board shows a name that reaches nobody. Watched for 3 further
   minutes: no `thinking`, no `moved`/`commented` by `agent:pai` from the
   assignment (the only pai events on OPS-2 are its own create/assign/comment
   from run `aa2cc15c…`).

Relay stats over the scenario: `invocations_24h` 15 → 17 (+2: health-monitor
and pai), `suppressed_24h` stayed 0.

### Scenario 3 — standup cites the board (PASS)

`POST /api/jobs/bce128bd0de14251aa54467d6b869d25/run` at 21:27:23Z (Run Now on
`relay-standup`) → 200 `{"id": "9d11d5ce78ae44f6bade934a9c2057dd", "relay_channel": "standup"}`.

| when (UTC) | actor | message (first ~90 chars) | hop | run id | ticket key cited |
| --- | --- | --- | --- | --- | --- |
| 21:27:23.181 | system:scheduler | "@all — what did you do in the last 24h, which tickets did you move, and what is blocked?…" | 0 | — | — |
| 21:27:38.799 | agent:stockmarket | "Already covered the 09-11 session brief earlier today (QQQ/SPY/XIU.TO risk-on rally…" | 1 | `cba953f43b464fc29b9d8b5473788f39` | no |
| 21:27:40.597 | agent:news | "This one's already handled — OPS-1 was already worked earlier today…" | 1 | `7b151f238fde48b58aba85bc25709a6d` | **OPS-1** |
| 21:27:42.131 | agent:news-librarian | "No tickets assigned to me — none moved, nothing blocked…" | 1 | `200a8f27818945cc8f0d87cc7e3db657` | no (correctly) |
| 21:27:59.134 | agent:running | "Synced Strava since 2026-06-14 (42 activities) and rebuilt the weekly brief…" | 1 | `be9de46d7e0f40d5bb6dbc4aea7fc01f` | no |
| 21:28:00.349 | agent:platform-coder | "No ticket access was invoked and no edit requests landed in the last 24h — moved 0 tickets…" | 1 | `a3ca7f31b0884238ab0bc2c9a26e8426` | no (correctly) |
| 21:28:07.324 | agent:pai | "Standup for 🐢 pai: … Also opened **OPS-2**…" | 1 | `a133650a5abc4a6396618ea3b82fc640` | **OPS-2** |
| 21:28:14.175 | agent:stockmarket-data | "No tickets moved — none assigned to me, none touched. Data update: ran the daily sync…" | 1 | `3fe81072603b461db7b3a32e5f56be47` | no (correctly) |

Seven agents answered, every one at hop 1 (no agent answered another agent's
standup line). Both agents that actually held a ticket cited its key; the five
with no tickets said so explicitly, which is the v2 prompt working.
`health-monitor` did not answer — `@all` deliberately skips system agents.

### Stats, before → after the whole session

| metric | 21:19 | 21:32 |
| --- | --- | --- |
| relay `invocations_24h` | 14 | 24 |
| relay `messages_24h` | 20 | 39 |
| relay `agent_messages_24h` | 15 | 27 |
| relay `suppressed_24h` | 0 | 0 |
| relay `suppressed_by_reason` | `{hop_limit 0, budget 0, not_member 0, coalesced 4, facade_owns_turn 0}` | unchanged |
| relay budget `global_used_last_hour` / `global_per_hour` | 1 / 120 | 11 / 120 |
| tickets `open` / `review` | 0 / 0 | 1 / 1 |
| tickets `moved_24h` | — | `agent:news ×2` |
| tickets `budget.agents` | — | `pai: used 1, left 19` (of 20/h) |
| tickets `stale` / `orphaned` | 0 / 0 | 0 / 0 |

No guard fired anywhere in the session: nothing hop-capped, nothing
budget-refused, no agent wrote into a room it is not in.

### Screenshots (live, 1280×860, against the NUC)

- `scratchpad/live/tickets-board-1280.png` — the Tickets board on the NUC:
  five columns (Open 1 / In progress 0 / Blocked 0 / Review 1 / Closed·7d 0)
  with a "TODAY" strip reading `news 2`, OPS-2 sitting in Open assigned to
  `pai` and OPS-1 in Review badged `HIGH` with the news balloon face, plus the
  project / assignee / label filters and a New-ticket button.
- `scratchpad/live/tickets-detail-1280.png` — OPS-1's detail page: the
  left rail (state REVIEW with a Move-to picker, p1·high, assignee news,
  reporter you, project #ops, `news · succeeded` run link) beside a full
  ACTIVITY log, and on the right the ticket's Relay thread showing the assign
  mention, both italic system move rows, and news's own reply — one page that
  is both the record and the room.


### Round two — 2026-09-12, after R1/R2 (helm rev 53 + a backend/broker redeploy)

Commit `486c0d8` ("system agents act from per-run tokens; a bare assignee is an
agent or refused") was built at 18:04:09–18:05:35 local, imported into k3s
containerd, and rolled out over the rev-53 release: `ap-api`, `ap-dispatcher`,
`ap-recorder`, `ap-mcp-broker`, then `ap-mcp-facade` — every
`kubectl rollout status` returned "successfully rolled out", `### DONE-REDEPLOY`
/ `EXIT=0` at 18:06, and the relay router rejoined its group (generation 7) with
its topics assigned. Same forward, same script.

**(a) The runless `system:*` keys are gone (PASS).** `GET /api/api-keys` returns
2374 rows, of which **5 are live** — `app:news`, `app:running`,
`app:stockmarket`, `kyle-claude-code-mcp`, `wh-k-test2`, i.e. app and human
credentials, none of them a system key. Of the **25** rows named
`system:<agent>`, **0 are live**: the three that were still live before the
redeploy were revoked in one stroke at **22:06:03.114620Z** — the migration's
single timestamp — `system:change-summarizer`, `system:health-monitor` (minted
21:20:46, the very key that 403'd in round one) and `system:run-summarizer`
(minted 22:00:00). Every other `system:*` row carries an older `revoked_at`
from the dispatcher's own re-mint. `ApiKeyView` does not expose `run_id`, so
the check is by name and liveness — and by name, nothing runless survives.
(No token or prefix value is reproduced here.)

**(b) Scenario 2 re-run (PASS).** Same message in `#ops`, 22:07:04Z: "@health-monitor
open a ticket in #ops for the noisiest failing agent this week …, assign it to
pai, and put your reasoning in the ticket's thread." This time health-monitor
did it itself, in **15 seconds**, with no 403 anywhere in the run.

| when (UTC) | actor | event / message | hop | run id | outcome |
| --- | --- | --- | --- | --- | --- |
| 22:07:04.0 | user:admin | the summons in `#ops` | 0 | — | mentions `health-monitor` |
| 22:07:19.393 | agent:health-monitor | `🎫 OPS-3 · Noisiest failing agent this week: health-monitor (failure streak) — opened by health-monitor` | **1** | `3de5f74aa2854a259a4cd2c8c4ff41ce` | **OPS-3** created, `reporter = agent:health-monitor` — the R1 fix, live |
| 22:07:25.668 | agent:health-monitor | `@pai you've been assigned OPS-3` + event `assigned` `None → agent:pai` | 1 | `3de5f74a…` | the tool was given the bare `pai`; R2 normalised it to **`agent:pai`** and the mention actually fired |
| 22:07:30.886 | agent:health-monitor | event `commented` — "Reasoning (health check run just now …): - Noisiest failing agent this week: hea…" | 1 | `3de5f74a…` | reasoning in the ticket thread, as asked |
| 22:07:34.939 | agent:health-monitor | "Done. Created **OPS-3** … assigned to pai, with fu…" | 1 | `3de5f74a…` | reported back in the summons thread |
| 22:07:35 | agent:pai | `thinking` on `GET /api/tickets/OPS-3` | — | `5da8f39000ed45d285a55bea0cffa968` | **the assignment summoned pai** (+10 s) |
| 22:07:41.456 | agent:pai | event `commented` — "Closing as a duplicate of OPS-2 (same investigation, same reasoning — health-monitor failure_streak=8 at 19:00:03Z, corr…" | **2** | `5da8f390…` | |
| 22:07:43.608 | agent:pai | event `moved` `open → done`, reason "Duplicate of OPS-2" | 2 | `5da8f390…` | final state **done**, `assignee = agent:pai` |

Round-one's OPS-2 is still open, so pai reading OPS-3 as its duplicate is the
board working rather than a miss. Relay stats over the scenario:
`invocations_24h` **24 → 26 (+2)**, `suppressed_24h` still 0; the only movement
in `suppressed_by_reason` was `coalesced` 4 → 6, which is the guard
de-duplicating pai's two wakes (a second pai run, `ca0bbc78…`, was queued off
the comment), not a refusal.

**(c) A bare name is an agent or it is refused (PASS).** As admin, against
OPS-3:

| request | status | body / result |
| --- | --- | --- |
| `POST /api/tickets/OPS-3/assign {"to": "nobody-here"}` | **400** | `{"detail":"assignee must be agent:<name>, user:<name> or discord:<id>"}` |
| `POST /api/tickets/OPS-3/assign {"to": "agent:news", "notify": false}` | **200** | `assignee = agent:news` |
| `POST /api/tickets/OPS-3/assign {"to": "news", "notify": false}` | **200** | `assignee = agent:news` — a bare name that IS an enabled agent is normalised, not stored raw |

Board after round two: `open 1`, `review 1`, `done_24h 1`, `stale 0`,
`orphaned 0`; create budgets show `health-monitor: used 1, left 19` beside
`pai: used 1, left 19`. Relay: `invocations_24h 27`, `messages_24h 50`,
`agent_messages_24h 34`, `suppressed_24h 0`, budget 13/120 for the hour.

### What did not happen

- ROUND ONE ONLY, fixed by R1 (`486c0d8`) and re-verified above:
  `agent:health-monitor` never created a ticket itself — four
  `tickets action=create` calls were refused `403 this token has no run to act
  from` (defect 1 above). The scenario was salvaged by the agent delegating to
  `@pai`, which is behaviour worth keeping but was not the thing under test.
- ROUND ONE ONLY, fixed by R2 (`486c0d8`) and re-verified above: the
  `assignee: "pai"` on OPS-2 summoned nobody (defect 2 above); polled for
  3 minutes with no `thinking` and no pai activity from the assignment.
- OPS-2 was left open and unsummoned — it predates R2, so its assignee is
  still the bare string `pai`; round two opened OPS-3 rather than repairing it.
- No SSE/UI-push path was exercised in either round beyond the two
  screenshots; the board was read through the REST endpoints.
- The screenshots are round one's: the board they show does not include OPS-3.
- Scenarios 1 and 3 were not re-run or re-screenshotted after the redeploy;
  both passed on the rev-53 images and R1/R2 touch neither path.

## Handoff to Kyle

The loop hit no classifier refusal — the deploy and both redeploys ran through
Terminal.app. What is left is decisions, not commands:

- **`main` is 47 commits ahead of `origin/main`** and has been since before
  Relay. The loop commits directly to `main` (Kyle's workflow) and never
  pushes; Relay and Tickets both live only on this machine and on the NUC's
  images until someone pushes.
- **OPS-2 on the live board still carries the bare `pai` assignee.** It was
  opened in round one, before R2 normalised bare names, so its assignee is the
  string `pai` and summons nobody. R2 does not backfill; reassign it from
  `/tickets/OPS-2` (assigning to `pai` now stores `agent:pai` and fires the
  mention). It is also still open while OPS-3, its duplicate, is done.
- **The screenshots are round one's** (`scratchpad/live/tickets-board-1280.png`,
  `tickets-detail-1280.png`): the board they show predates OPS-3. Cosmetic —
  re-shoot if either is going anywhere public.
- **Two deferred items are security decisions, not cleanups** (both in
  "Deferred" above, both recorded in design 20's AS BUILT): an agent at
  `annotator` or above reaches `/api/relay/*` and `/api/tickets/*` server-side
  without holding the grant — only the runner's `--allowedTools` stops it — and
  `services/web/src/api.ts` surfaces raw JSON as error text, which is why the
  budget 429 reads as `429: {"detail":…}` on every page in the product.
- **The Discord mirror of a ticket card was never exercised.** No channel on
  the NUC is bound to a bridge (no `connector-discord-api` secret is set), so
  the card's plain-text body and the deliberate no-mirror on card edits are
  tested but not live-proven.
