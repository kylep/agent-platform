# Plan — Wiki, the shared knowledge base (design 21)

Design: `docs/design/21-wiki-shared-knowledge.md`. Vision:
`docs/vision/agent-ecosystem.md`. The two blocks this builds on:
`docs/design/19-relay-agent-messenger.md` and
`docs/design/20-tickets-agent-work-tracker.md` (read both "AS BUILT" sections;
the Tickets plan `docs/superpowers/plans/2026-09-12-tickets-agent-work-tracker.md`
records how that build went, what its reviews caught, and what was deferred).
This file is the **single source of state** for the build: the loop re-reads
it every tick, executes the first unchecked task, and ticks the box with the
commit hash. Anyone (Kyle, a fresh session) can resume from it.

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
   Tasks marked `[after Tn reports]` start the moment Tn's implementer
   reports (before Tn's review finishes), because they build on Tn's files.
3. When it reports, **verify the evidence yourself**: run the test commands it
   named (backend: `cd services/backend && .venv/bin/python -m pytest -q
   <paths>`; web: `cd services/web && npm run -s lint && npm run -s
   check:tokens && npm run -s build && npx playwright test --reporter=line`;
   broker: `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest
   -q`). Do not trust a claim of green without output in your own transcript.
   Run git and every repo-relative command from the repo root, never from a
   `cd` that persisted (a diff saved from the wrong directory is empty).
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text, the
   design excerpt, and the path of a saved `git diff` of the uncommitted
   changes. Ask for findings ranked by severity, defects only, no style — and
   for every guard, limit, permission check, migration or path built from
   model text, ask it to state the WORST CASE (a hostile page body in a
   prompt, a looping agent, two concurrent editors, a second `init_db` on a
   live DB). For tasks tagged `[ui]`, dispatch additionally a **sonnet
   visual reviewer** that builds the app, serves it against the Playwright
   mock API (`tests/mock-api.ts`), screenshots the affected page(s) at
   1280×800 and 390×844 in both themes with a throwaway spec it deletes
   afterwards, READS the PNGs, and reports what a picky human would notice.
   Never use `model: "fable"` or the default model for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" with file:line and move on. Loop at most twice per
   task; if a task still fails after two repair rounds, mark it `- [!]` with a
   one-paragraph note and continue to the next task — do not stall the build.
6. **Hold every commit until no implementer is editing the tree**: the
   pre-commit hook stashes and restores unstaged tracked files and would race
   a live editor. Commit on `main` (Kyle's workflow: single branch, direct
   commits, `git add` each file by name, never `-A`; never add files that may
   hold secrets). Message: `feat(wiki): <task title>` plus a body, and the
   attribution trailer the session was given. Then edit this file:
   `- [x] **Tn …** (commit `<hash>`; <one line of what review changed>)`.
   Two `[ui]` tasks that share App.tsx/app.css/mock-api land as one commit.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task (or one parallel pair)
   per tick keeps each turn's context small.
8. Recovery. A quota 429 kills a subagent instantly; it keeps its transcript:
   after the reset, SendMessage "quota is restored; check git diff for what
   landed, then resume from where you stopped" — if that fails, relaunch with
   the same prompt. A "stalled: no progress" agent likewise. Terminal.app
   `do script` sometimes DROPS THE FIRST CHARACTER of the command: always
   prefix with `true; true; ` and use absolute paths, and read the `.out`
   file within 30 s of launching. Terminal.app stops executing after ~20
   stale windows: quit and relaunch it. A Playwright spec that fails only
   under the full suite or under host load: rerun that file alone before
   calling it red.
9. If a Bash action is refused by the auto-mode classifier (kubectl
   apply/delete, helm upgrade), do not retry variants. Write the exact
   commands into "Handoff to Kyle" below, send a PushNotification saying the
   build is blocked on a deploy step, and continue with any task that does not
   depend on it. Reaching the NUC works via Terminal.app:
   `osascript -e 'tell application "Terminal" to do script "true; true; <cmd> > <scratchpad>/x.out 2>&1; echo EXIT=$? >> <scratchpad>/x.out; exit"'`
   then wait for `EXIT=` in the file (plain `ssh pai` from this process gets
   "No route to host" — macOS Local Network permission, see memory
   `claude-code-local-network-tcc-gotcha`). The API is reachable from this
   process only through `ssh -f -N -L 18090:localhost:8090 pai` (started the
   same way; it dies when the ssh session drops — check `curl
   localhost:18090/login` before each live step) at `http://localhost:18090`;
   Playwright screenshots of the live site go through that forward too. The
   deploy script from the Tickets build is the reference (session scratchpad
   `t20-deploy.sh`: buildx `--platform linux/amd64 --provenance=false --load`,
   `docker save` → `scp` → `sudo k3s ctr -n k8s.io images import`, `helm
   upgrade ap charts/agent-platform -n agent-platform -f <stored values> -f
   charts/agent-platform/values-pai-nuc.yaml` — never `--reuse-values` — then
   rollout restart api/dispatcher/recorder/web/broker, facade last).

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  run with `cd services/backend && .venv/bin/python -m pytest -q`). Web is
  React 19 + Vite + `@ap/ui` (`packages/ui`) in `services/web`
  (`npm run -s lint`, `npm run -s check:tokens`, `npm run -s build`,
  `npx playwright test`). The MCP broker is `services/mcp-broker/broker.py`
  (tests `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q`).
  Prod Python is 3.12 (dev venv is 3.14): no 3.13+/3.14-only syntax, no
  annotation tricks that only pass locally.
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` fixtures in
  `tests/conftest.py` and sqlite; keep every new table portable to sqlite
  (JSON columns, no Postgres-only DDL outside `dialect == "postgresql"`
  guards, as `db.py:_ensure_relay_ddl` and `_ensure_tickets_ddl` do).
- Migrations are additive and live in `db.py`: `Base.metadata.create_all`
  makes new tables, `_ensure_columns` adds new columns to existing ones (it
  cannot apply an ORM default to existing rows — backfill explicitly), and
  one-off seeds/backfills go in a new `_ensure_*` function called from
  `init_db`, idempotent, gated by a `schema_marks` row (see
  `TICKETS_SEED_MARK`, `_ensure_tickets_standup_v2`,
  `_ensure_tickets_health_monitor` for the three shapes: seed, rewrite-if-v1,
  apply-and-mark-only-when-applied under a savepoint).
- Relay is the substrate. A wiki diff card, a budget notice, any system row:
  written through `relay_store.post_relay_message` + `publish_relay_message`
  (or `summon_channel` for a scheduled post) — never a bare insert, never a
  second Kafka producer; cards carry `mentions=[]`. `ticket_store.py` is the
  reference for "the ONE place a thing changes": one commit per public call,
  then publish (message re-send, system row, then the domain event), the
  actor↔run invariant (`_require_actor`: an agent actor must pass its own
  `Run`; hop = `run.depth + 1`; a non-agent actor has no run), row locks
  under a dialect guard, `TicketRuleError`/`TicketBudgetError` shapes.
- Kafka: topics are constants in `agentplatform/events.py` (`ALL_TOPICS` must
  include them) AND `charts/agent-platform/values.yaml` `topics.specs`
  (retentionMs quoted as a string). Use `make_envelope` / `Producer` /
  `FakeProducer` / `consume_forever` from `events.py`; never a bare
  aiokafka client. Tests inject `FakeProducer`; nothing in a test may open a
  real Kafka connection (see how `test_relay_sse.py` and `test_tickets_api.py`
  inject the feeds).
- Roles: `api/auth.py` `ROLES`/`READ_ROLES`/`INVOKE_ROLES`; the per-run
  participant role (`relay`) and `require_relay_access` in `api/relay.py` are
  the pattern for "as the agent"; `agentspec.PLATFORM_MCP_RELAY_TOOLS` is the
  grant list that yields it (add the wiki grant there, never to
  `PLATFORM_MCP_TOOLS`). `api/tickets.py::_run_of` is how a write resolves
  the agent's Run from `request.state.api_key_run_id` and refuses (403) an
  agent token with no run. Every untrusted string an agent could plant
  (page bodies, titles, reasons, slugs) is escaped where it enters a prompt
  and flattened/capped where it enters a system row (`tickets._line`).
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no "added by" notes, no TODOs without an owner.
- Web pages: register the route in `services/web/src/App.tsx`, the nav in
  `packages/ui/src/sidenav.tsx` `buildPlatformNav`, add fixtures to
  `services/web/tests/mock-api.ts` (unmatched GETs fail tests on purpose), a
  row in `tests/smoke.spec.ts` `PAGES`, and the page in `tests/a11y.spec.ts`'s
  lists (desktop and the 390 list). Use `@ap/ui` subpath imports like
  `Tickets.tsx` does; reuse `components/relay/*` and `components/tickets/*`
  (Face, `useTickets`' SSE/backoff/upsert shape, the markdown renderer in
  `packages/ui/src/markdown.tsx`, the chip rewrite in
  `components/relay/Message.tsx` which already protects code/links/images/
  URLs — extend it for `[[slug]]`, do not write a second one), `lib/title.ts`
  for the tab title. No raw hex colours. Mobile (<560px) must not break the
  shell; never `display: contents` on a landmark; the a11y spec runs at 390
  too. `.sr-only` exists in app.css.
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

### Phase 1 — model and the pure library

- [x] **T1 Schema: wiki_pages, wiki_versions, wiki_links, seeds.** (commit `5d807fd`; review fixed: home-page seed is lookup-first so a stray row cannot crash init_db)
  Modify `services/backend/agentplatform/db.py`: add `WikiPage`, `WikiVersion`,
  `WikiLink` exactly as the design's "Data model" (uuid4 hex ids, `utcnow`
  timestamps, `UNIQUE (page_id, version)` as a real constraint, `wiki_links`
  PK pair, `summary` String(280), `reason` String(200), `slug` String(64)
  unique). `_ensure_wiki_ddl(conn)` for postgres-only pieces (GIN tsvector
  over `title || ' ' || body`) under the dialect guard. `_ensure_wiki_seed(conn)`
  gated by `wiki-seed-v1`: the `home` page (title "Home", version 1, author
  `system:wiki`, body from a module constant `WIKI_HOME_BODY` that explains
  what the wiki is for, how `[[slug]]` links work, and links to `[[standup]]`
  and `[[deploying]]` as wanted pages), its version row and its two link
  rows; the `#wiki` open Relay channel (topic "every edit, as a diff card";
  no ticket prefix); leave the agent and the job to T5. Call both from
  `init_db` after the tickets ensures. Tests in `tests/test_wiki_schema.py`:
  models round-trip on sqlite; `init_db` twice is idempotent; seeds present
  (page, two links, the channel); the unique version constraint refuses a
  duplicate.

- [x] **T2 Settings + `wiki.py` pure library.** (commit `ff1c59a`; review fixed: summary skips fences and every leading heading, links ignore inline code, CRLF normalised, parenthesised URLs unwrapped; `tickets._line` → `one_line`)
  Add to `config.py` with why-comments: `wiki_agent_writes_per_hour=30`,
  `wiki_prompt_pages=5`, `wiki_default_grant=True`, `wiki_stale_days=30`,
  `wiki_max_body_bytes=65536`. New module `agentplatform/wiki.py` with pure
  functions: `SLUG_RE`, `slugify(title_or_key) -> str` (lower, kebab, the
  slug grammar, `ValueError` when nothing usable), `find_links(body) ->
  list[str]` (`[[slug]]` targets, lower-cased, deduped, not inside fenced
  code — reuse `relay._FENCE`), `summary_of(body) -> str` (first paragraph,
  markdown headings/links stripped, ≤ 280), `unified_diff(old, new, slug,
  v_old, v_new) -> str` and `line_counts(old, new) -> (added, removed)`
  (difflib), `card_for(page, version_row, *, url_base)` and `card_body(...)`
  (`📖 [[slug]] vN · author: "reason" (+a −r)`), `budget_body(limit)` with a
  fixed `BUDGET_PREFIX` (the tickets pattern), `promoted_body(memory_content,
  agent, key, when)` (content + the provenance line), `is_stale(page, now,
  days)`; every field that enters a card or system row goes through
  `tickets._line`-style flattening + room-mention stripping + caps — import
  the helper (promote `_line` to a public name in `tickets.py` if needed and
  say so). Golden tests in `tests/test_wiki_lib.py` for each, including links
  inside fences, unterminated fences, a body with `</wiki>` in it (escaping is
  the prompt builder's job — assert `find_links`/`summary_of` do not choke),
  a 10k-char reason, and `slugify("Location") == "location"`,
  `slugify("Kyle's Location!") == "kyles-location"`.

### Phase 2 — the store, the API, the events

- [x] **T3 `wiki_store.py`: the ONE place a page changes.** (commit `45b55a2`; review fixed: lost create race is a WikiRuleError under a savepoint even without the room lock, re-promotion refuses to overwrite an edit since promotion, history limit clamped)
  New `agentplatform/wiki_store.py` modelled line-for-line on
  `ticket_store.py`: `create_page`, `write_page` (replace with
  `base_version`; `WikiConflictError(WikiRuleError)` carrying the current
  version when it mismatches), `append_page` (creates when absent, title from
  slug), `archive_page`, `restore_page(version=None)`, `promote_memory(memory
  dict, *, slug, title)`, `page_view`, `version_view`, `history`,
  `backlinks`, `wanted`, `agent_write_budget_left`, `say_budget_once`.
  Every write: `_require_actor` (agent actor passes its own `Run`), row lock
  under the dialect guard, body/title/reason/tag caps (`WikiRuleError`),
  version += 1, a `WikiVersion` row with `author`/`run_id`/`reason`, links
  rewritten from `find_links`, `summary` recomputed, ONE commit, then
  publish in order: the diff card in `#wiki` (`kind=event`, `card_for`,
  `mentions=[]`, posted through `relay_store` — only when the `#wiki` channel
  exists and is not archived; a missing room is logged, not fatal) and the
  `wiki.events` payload `{event, page, version, author, run_id, reason,
  added, removed}`. Add `TOPIC_WIKI_EVENTS = "wiki.events"` to `events.py`
  `ALL_TOPICS` and `values.yaml` `topics.specs` (retention `"2592000000"`).
  Tests in `tests/test_wiki_store.py` with `FakeProducer`: create → v1 +
  card + event; write with the right base → v2, links rewritten, counts
  right; write with a stale base → `WikiConflictError` with the current
  version and nothing written; append never conflicts and creates when
  absent; archive hides from `wanted`/`backlinks`; restore to v1 makes v3
  with v1's body; promote sets `source_memory_id`, tags and the provenance
  line; an agent actor without its run is refused; the budget counts only
  that actor's versions in the trailing hour; a failure injected before
  commit leaves nothing applied; a publish failure after commit leaves a
  consistent page.

- [x] **T4 API `/api/wiki/*`, the participant role widened, default grant, SSE, stats, promote.** (commit `c859391`; review fixed: archive-twice guarded under the lock, `require_wiki_access` requires the wiki grant for agent callers, lost create race → 409 via WikiExistsError, citations scoped to the reader's rooms, count_capped; facade SSE exclusion + counts 85/111 landed here, T9 decides the DELETE gate)
  New router `api/wiki.py` implementing every route in the design's "API"
  section over `wiki_store`, with `api/schemas.py` models (`WikiPageView`
  incl. `updated_by_face`, `WikiVersionView`, `WikiHistoryRow`,
  `WikiDiffView`, `WikiWantedRow`, `WikiStats`, the inputs). Auth: reuse
  `require_relay_access`; widen the participant role's scope in `api/auth.py`
  to `/api/wiki/*`; agents write only as themselves with their `Run`
  (`_run_of` pattern, 403 without a run); humans write with `INVOKE_ROLES`.
  Agent writes go through the budget → 429 with `budget_body` +
  `say_budget_once` in `#wiki`. `PUT` without `base_version` → 422; mismatch
  → 409 `{detail, current_version, current_summary}`. `DELETE` archives;
  archived pages 404 on read except `GET …/history` and `…/versions/{n}`.
  `GET /api/wiki/pages` supports `q` (postgres tsvector rank when available,
  ILIKE on sqlite — one helper, dialect-guarded), `tag`, `changed_since`,
  `source_memory_id`, `limit` (cap 200). `GET …/{slug}` adds `backlinks` and
  `cited_in` (`relay_messages.body LIKE '%[[slug]]%'`, last 30 days, count +
  last three message ids/channels). `GET /api/wiki/events` = a `TopicFeed`
  on `wiki.events` (frames `page`, `heartbeat`, `overflow`; humans unfiltered,
  agents unfiltered too — pages are not room-scoped). `GET /api/wiki/stats`
  per the design (edits_24h by author with faces from `relay_store.faces_for`,
  wanted, stale via `is_stale`, budget for the top agents). `POST
  /api/wiki/promote {memory_id, slug?, title?}`: humans any namespace, an
  agent only its own (`memory.agent == caller.agent` else 403); reads the
  memory through the memory API's internals (import its query, do not call
  HTTP). Default grant: `agentspec.PLATFORM_MCP_RELAY_TOOLS` gains
  `mcp__platform__wiki` (`TOOL_WIKI`), a `TOOL_HELP` entry in the tickets
  entry's voice, `toolregistry.CORE_TOOL_SUFFIXES` gains `wiki`, agent
  creation adds it while `settings.wiki_default_grant` (extend
  `DEFAULT_GRANTS` in `api/agents.py`), `_grant_to_every_agent(conn,
  TOOL_WIKI, "wiki-default-grant-v1", …)` in `init_db`. Register the router
  and the feed in `api/app.py` + the three `*_main.py` the way the tickets
  feed is. Regenerate the SDK. Tests in `tests/test_wiki_api.py`: every route
  happy path; reader reads, cannot write; agent writes as itself with its
  run and is 403 without one; 409 carries the current version; budget 429 on
  the 31st write; archived semantics; `cited_in` counts a message that
  contains `[[slug]]` and not one with the slug in a code fence (mirror
  `find_links`'s rule in the query if cheap, else document that the count is
  a LIKE); promote by a human and by the owning agent, 403 cross-agent; SSE
  yields a `page` frame for a published event (inject the feed like
  `test_tickets_api.py`); a new agent holds all three participant grants.

### Phase 3 — the agents' side

- [x] **T5 The `<wiki>` prompt block, the `wiki` librarian agent, the gardener job.** (commit `4ca1440`; review fixed: postgres ranking reads tags, gardener job adopted by name, sqlite scan capped) `[parallel with T6]` (owns `relay.py`, `relay_router.py`, `relay_store.py`, `db.py` seeds, `scheduler.py` if needed, their tests)
  In `relay.build_mention_prompt` add an optional `wiki_pages` list rendered
  as a `<wiki>` block inside the untrusted region (after `<relay-messages>`,
  before `<your-tickets>`): each `[[slug]] · title · summary` escaped with
  `_attr`/`escape`; omitted when empty so existing goldens stay
  byte-identical; the rules gain the two sentences from the design (cite as
  `[[slug]]`; write what you learn with the `wiki` tool, `append` for notes).
  In `relay_router._spec` compute the list: full-text match of the summoning
  message's text plus the thread window's text (when in a thread) against
  title/tags/body, top `settings.wiki_prompt_pages`, archived excluded, one
  query, dialect-guarded (tsvector `plainto_tsquery` rank on postgres; a
  keyword ILIKE fallback on sqlite) — put the query in `wiki_store.search_for_prompt`.
  Seed the librarian: `_ensure_wiki_agent(conn)` gated by `wiki-agent-v1`
  creates the `wiki` AgentDef if absent (`system=True`, `enabled=True`,
  `description`, the prompt from the design's "The prompt block and the
  librarian" as a module constant `WIKI_AGENT_PROMPT`, `platform_tools`
  `[relay, tickets, wiki]`, `harness_tools []`, `role` default, model default,
  `entrypoints {}`), with a version row like the grant sweeps; skip when a
  `wiki` agent already exists (a human may have made one). Seed the job:
  `_ensure_wiki_gardener_job(conn)` gated by `wiki-gardener-v1` creating the
  relay-post job `wiki-gardener` (`0 10 * * 0`, America/Toronto,
  `relay_channel="wiki"`, prompt from the design). Tests: `test_relay_lib.py`
  goldens for a prompt with three pages (escaping of a hostile title) and the
  unchanged prompt with none; `test_relay_router.py` — a summons whose text
  matches a page's title gets that page in its prompt, an archived page never
  appears, a room with no matching page gets no block; `test_wiki_schema.py`
  — both ensures idempotent, the agent row's grants and `system` flag, the
  job's cron/timezone/channel.

- [x] **T6 Broker tool `wiki`.** (commit `1db4d30`; review fixed: archived-slug guidance, flattened list/history rows (tickets too), 403 on memory listing named, slug regex anchored) `[parallel with T5]` (owns `services/mcp-broker/*`)
  In `broker.py` add `@mcp.tool @_metered("wiki") async def wiki(action, …)`
  with the design's eight actions, next to `tickets` and in its voice: every
  slug from model text passes `SLUG_RE` before a URL is built (else `error:
  a slug is lowercase letters, digits and dashes, e.g. deploying`); `write`
  without `base_version` on an existing page is refused client-side with
  `error: read the page first and pass base_version=N` unless the API says
  the page does not exist; a 409 from the API is returned as `error: the
  page changed under you (now vN): re-read it and merge, or use append`; the
  docstring tells the agent to cite with `[[slug]]`, to prefer `append` for
  notes, to give a reason on every write, and never to invent a page. `read`
  returns page + backlinks + cited_in in one readable JSON; `search`/`list`/
  `wanted`/`history` render one line per row; `promote` sends `{key}` or
  `{memory_id}`. Tests in `test_wiki_tool.py` mirroring `test_tickets_tool.py`
  (patched `_call`, the metering fixture): each action's request shape, the
  slug gate (traversal refused, no call made), the 409 translation, the
  base_version guard, authorship never an argument, the audit record per call.

### Phase 4 — web UI  `[ui]`

- [x] **T7 `[ui]` Wiki home and page: render, edit with conflicts, history and diff, recent changes live.** (commit `2a9fcf1`, shared with T8; defect review fixed: diff error state, missing→exists live reload + create-race message, search race guard, nested-bracket link protection, mobile tables; visual: wiki chips cut to the ticket chip's pattern)
  New `services/web/src/pages/{Wiki,WikiPage}.tsx`, `components/wiki/*`,
  `lib/wiki.ts` (types, `wikiRefs` — `[[slug]]` outside fences, `slugify`).
  `/wiki`: the `home` page rendered plus a rail: search (`/` focuses, hits
  `GET /api/wiki/pages?q=`), tags, **Recent changes** (live via a `useWiki`
  hook on `/api/wiki/events` with `useTickets`' backoff/upsert shape; rows =
  face · `[[slug]]` · reason · `+a −r`), **Wanted** (red chips, most-linked
  first), **Stale**. `/wiki/:slug`: rendered markdown through `@ap/ui`
  Markdown with `[[slug]]` rewritten to chips (blue existing / red wanted —
  extend the chip pass in `components/relay/Message.tsx` into a shared
  `lib/chips.ts` used by both, keeping its code/link/image/URL protection),
  header (tags, `v N`, last editor `Face` + relative time, "cited in N
  messages" with the last three linked to `/relay?channel=&thread=`),
  backlinks, "📖 promoted from memory" badge; **Edit** mode (textarea, reason
  field, base version carried; on 409 a banner "This page changed (now vN)"
  with "Reload and keep my text" that re-fetches and keeps the draft); a red
  chip opens the editor to create the page. **History** drawer: versions with
  faces and reasons; selecting one fetches `…/versions/{n}` and renders the
  unified diff (added/removed lines coloured with tokens, monospace, wraps on
  mobile); "Restore this version" → `POST …/restore`. `useTitle` on both.
  Route, nav (`{ to: "/wiki", label: "Wiki" }` after Tickets), fixtures in
  `mock-api.ts` (home, three pages incl. one promoted, one archived, a wanted
  link, history with three versions and a diff, stats, events stream,
  search), smoke rows for `/wiki` and `/wiki/deploying`, a11y rows (desktop
  and 390), `tests/wiki.spec.ts`: home renders the rail lists; a red chip
  opens the editor; edit with the right base saves and the page re-renders;
  a mocked 409 shows the banner and keeps the draft; the diff view marks
  added/removed lines; an SSE `page` frame updates Recent changes; 390 never
  scrolls sideways.

- [x] **T8 `[ui]` Chips in Relay and tickets, Memories promote, dashboard tile, Help.** (commit `2a9fcf1`, shared with T7; review fixed: promote dialog's three refusals instead of raw JSON, memories table floor + scroll at 390, promoted rows lose the button, threshold boundaries, smoke probe) `[after T7 reports]` (owns `components/relay/Message.tsx` only through the shared `lib/chips.ts` T7 made, `pages/Memories.tsx`, `pages/Dashboard.tsx`, `pages/TicketDetail.tsx` (body render only), `docs/building-blocks/*`; adds fixtures/smoke rows only)
  `[[slug]]` chips in Relay messages and ticket bodies via the shared chip
  pass (existing → blue link to `/wiki/slug`, wanted → red). Memories page:
  a "Promote to wiki" button per row (dialog: slug prefilled from the key via
  `slugify`, title, confirm) → `POST /api/wiki/promote`, and a "📖 promoted"
  badge linking to the page for rows named by
  `GET /api/wiki/pages?source_memory_id=` (one call per page load). Dashboard:
  a Wiki `Stat` (pages · edits 24h · wanted, `to="/wiki"`) and attention rows
  for wanted ≥ 5 and stale ≥ 10. Help page `docs/building-blocks/wiki.md` in
  the voice of `tickets.md` (what a page is, slugs and links, versions and
  conflicts, the tool's actions, promotion, the librarian, the guards), a row
  in `docs/building-blocks/README.md`, glossary terms (page, slug, wiki-link,
  wanted page, librarian). Fixtures, smoke rows (`/help/wiki`, `/memories`
  probe for the badge), `tests/wiki-integrations.spec.ts`: a Relay message
  with `[[deploying]]` shows a blue chip and `[[nope]]` a red one, a fenced
  `[[x]]` stays code; the promote dialog posts the right body; the badge
  renders; the dashboard tile and rows render from fixtures.

### Phase 5 — ship it

- [x] **T9 Facade curation (design 17), SDK drift, CI.** (commit `4127702`; KEEP 11 / GATE 1 (archive) / EXCLUDE 1 → 84 default, 111 admin; promote by key server-side; review: ship)
  In `services/mcp-facade/facade.py`: KEEP the page list/read/create/put/
  append/history/versions/wanted/stats/promote and restore operations; GATE
  `DELETE /api/wiki/pages/{slug}` (archiving is destructive) behind
  `AP_MCP_ADMIN_TOOLS`; EXCLUDE `GET /api/wiki/events` (SSE). Update the
  docstring counts and the two pinned numbers in `test_facade.py` from a
  fresh spec (they are computed, not arithmetic); add a "Curation: Wiki
  (design-21)" section to design 17 with the reasoning. Verify in the 3.12
  venv (scratchpad `facade-venv`; `pip install -q -e services/backend` first).
  Confirm `sdk/regenerate.py` is a no-op and CI needs no change.
  **Addition from T6:** `POST /api/wiki/promote` should accept `key` as an
  alternative to `memory_id`, resolved server-side in the caller's own
  namespace (an agent's participant token cannot list `/api/memories`, which
  is `READ_ROLES`, so the broker's key lookup 403s for most agents); a human
  passing `key` must also pass `agent`. Then the broker's `promote` sends
  `{key}` straight through instead of resolving it. Tests in
  `tests/test_wiki_api.py` and `services/mcp-broker/test_wiki_tool.py`;
  regenerate the SDK.

- [x] **T10 Build, deploy to the NUC, live-verify cite / write / promote / conflict.** (helm rev 55 at 14:03 EDT; promote/write/conflict/stats PASS, cite PARTIAL → R1; evidence under "Live verification"; screenshots `scratchpad/live/wiki-home-1280.png`, `wiki-page-1280.png`)
  Deploy exactly as Tickets' T10 did (protocol step 9; all four images —
  backend, web, mcp-broker, mcp-facade — plus helm upgrade for the new topic).
  Through the ssh forward: `init_db` seeded `home`, `#wiki`, the `wiki`
  agent (system, enabled, three grants) and `wiki-gardener`; every enabled
  agent holds `mcp__platform__wiki`; `/api/wiki/stats` answers. Then the
  live script (write it under the scratchpad like the Tickets one, an opus
  agent may run it while the orchestrator waits): (1) **promote** — as admin,
  `POST /api/wiki/promote` with pai's `Location` memory → page
  `location` (or the slug chosen), a diff card in `#wiki`; (2) **cite** —
  post in `#general` `@news where does Kyle live? cite the wiki` → poll for
  news's reply containing `[[location]]` (its prompt must have carried the
  `<wiki>` block: check the run's prompt via the run detail); (3) **write** —
  post in `#wiki` `@wiki write the standup page: what #standup is, when it
  fires, what to answer` → poll for a `standup` page (v1, author
  `agent:wiki`, run linked), its card in `#wiki`, and `home`'s `[[standup]]`
  link turning from wanted to existing; (4) **conflict** — as admin `PUT
  …/standup` with `base_version: 1` twice: the second must be 409 with
  `current_version: 2`; (5) **budget** stays untested live (no floods).
  Record evidence tables under "Live verification" (when/actor/event/run/
  outcome), stats before/after, and screenshots of `/wiki` and
  `/wiki/standup` at 1280 through the forward. If a step fails, add a Repair
  task rather than hand-editing the NUC.

- [ ] **T11 Memory + docs close-out.**
  Design 21: Status → shipped with the date and helm revision, an "AS BUILT"
  section listing every delta the reviews and the live run forced (the ticks
  above say what they were). `docs/design/00-overview.md`: row 21.
  `docs/vision/agent-ecosystem.md` "Order of work": Wiki shipped; add a
  closing paragraph that the three blocks are live and what the seams
  between them are. Memory: write `agent-platform-wiki.md` (shipped facts,
  live facts, gotchas, follow-ups) and add its index line; update the loop
  orchestration memory with anything this build taught. Fill "Handoff to
  Kyle".

### Repairs
(added by the loop when the definition of done fails)

- [x] **R1 The `<wiki>` prompt block never fires on postgres: the search ANDs every term.** (commit `27e7f8c`; OR-of-words as one bound to_tsquery param over a capped candidate set, @mentions stripped outside code; review: ship, two follow-ups folded in; round two: the same summons carried `<wiki count="3">` and news cited [[kyle-location]])
  Live: `@news where does Kyle live? …` summoned news with no `<wiki>` block
  (it answered from the tool instead); `GET /api/wiki/pages?q=kyle location`
  → `[kyle-location]` but `q=news kyle location` → `[]`; `@pai kyle location`
  (a 3-letter mention that `_search_words` drops) DID get the block. Root
  cause: `wiki_store._pg_search` uses `plainto_tsquery`, which ANDs terms,
  while the sqlite branch ORs candidates and ranks by overlap — the suite
  only runs sqlite. Fix: (a) in `relay_router._wiki_pages` strip `@mentions`
  from the match text before searching (the summoned agent's own name is
  never a page term); (b) in `_pg_search` build an OR query from the same
  `_search_words` list — `to_tsquery('english', 'w1 | w2 | …')` over
  words already restricted to `[a-z0-9]+` (or `websearch_to_tsquery` with
  ` OR `), keep `ts_rank` ordering and the indexed match expression; (c) add
  a compile test asserting the postgres SQL contains `|` (or `OR`) and not a
  bare `plainto_tsquery`, and a router test that a summons `@news where does
  Kyle live?` matches a `kyle-location` page on sqlite too (mentions
  stripped). Then rebuild the backend image, redeploy api/dispatcher/recorder
  (+ facade) with the scratchpad `t20-redeploy.sh` pattern, and re-run T10
  scenario 2 (`t21-verify.py` step 2): the run's prompt must contain
  `<wiki`. Record the result under "Live verification".

### Deferred
(low/medium review findings not fixed; each with file:line and one sentence)
- T9: `WikiPromoteIn` accepts `agent` alongside `memory_id` and silently ignores it; the validator should refuse the combination like it refuses `memory_id`+`key`.
- T8: the dashboard's Wiki tile vanishes (not `—`) when `/api/wiki/stats` fails — the same convention as the Tickets and Relay tiles.
- T8: with the page cache failed/unloaded (`slugs === null`) every `[[slug]]` chip renders as an ordinary link rather than a neutral "unknown" state (fails open toward "looks real" until the cache recovers).
- T8: the dashboard's "untouched for 30 days" copy hardcodes `STALE_DAYS` client-side while the count uses the server's `wiki_stale_days`; add `stale_days` to `/api/wiki/stats` to align (the tickets stats do this).

## Definition of done

All T1–T11 are `[x]`; `cd services/backend && .venv/bin/python -m pytest -q`
is green; `cd services/mcp-broker && ../backend/.venv/bin/python -m pytest -q`
is green; the facade suite is green under the CI recipe; `cd services/web &&
npm run -s lint && npm run -s check:tokens && npm run -s build && npx
playwright test` is green; the NUC runs the new images (`kubectl get pods`
all Running, `GET /api/wiki/stats` answers through the forward); "Live
verification" below shows a memory promoted to a page with its card, an
agent citing a page by `[[slug]]` in a Relay reply, the librarian creating a
page that turned a wanted link blue, and a 409 on a stale base version; the
wiki renders on the NUC (screenshot paths recorded).

## Live verification

Run 2026-09-13, 18:03–18:09 UTC, against the production NUC (`pai`) through
the ssh forward at `http://localhost:18090`. Deploy log:
`scratchpad/deploy22.out` (`EXIT=0`); verify script: `scratchpad/t21-verify.py`
(`step0 … step4`, `stats`); screenshot script: `scratchpad/t21-shot.mjs`.

### Deploy

`helm upgrade` at 14:00:54 local (18:00:54 UTC) took the release to
**REVISION 55** — `agent-platform-0.1.0`, `DESCRIPTION: Upgrade complete`,
superseding revision 54 from 00:07 the same day. Six rollouts all reported
`successfully rolled out` in order: `ap-api`, `ap-dispatcher`, `ap-recorder`,
`ap-web`, `ap-mcp-broker`, and then `ap-mcp-facade` last (it reads the API's
OpenAPI at boot), finishing 18:03:21 UTC. The post-deploy pod list shows the
six old pods `Terminating` and no new pod in anything but `Running`; the one
non-Running row, `ap-pg-backup-29818560-msrdf 0/1 Error (2d2h)`, is the known
catch-up backup pod and predates this deploy. The Kafka topic list printed
after the rollout reads `relay.invocations relay.messages tickets.events
wiki.events` — **`wiki.events` exists**. The relay router rejoined its group
(`relay-router` generation 10) and reassigned partitions cleanly.

### Preflight (T10 step 0)

| check | result |
| --- | --- |
| `GET /api/wiki/stats` | 200 — `pages=1 wanted=2 stale=0`, `budget.limit=30`, `edits_24h=[system:wiki ×1]` |
| `GET /api/wiki/pages/home` | 200 — v1, `created_by=system:wiki`, links `['deploying', 'standup']` — both present |
| `GET /api/wiki/wanted` | 200 — `[{deploying, linked_from:[home]}, {standup, linked_from:[home]}]` |
| `GET /api/relay/channels` | `['general', 'ops', 'qa', 'standup', 'wiki']` — `#wiki` present, id `c041e64dd3884b1a83bc0a45b18d9bed`, topic "every edit, as a diff card" |
| `GET /api/agents/wiki` | 200 — `system=true`, `enabled=true`, `platform_tools=['mcp__platform__relay','mcp__platform__tickets','mcp__platform__wiki']` |
| every enabled agent has `mcp__platform__wiki` | 11 of 11 enabled agents; `missing=[]` |
| `GET /api/jobs` → `wiki-gardener` | id `fd8c920f7ef747a385a11482834ac830`, `cron='0 10 * * 0'`, `timezone=America/Toronto`, `enabled=true`, `relay_channel='wiki'` |

Relay before: `invocations_24h=25 messages_24h=187 agent_messages_24h=39
suppressed_24h=1`.

### Scenario 1 — promote a memory (PASS)

| when (UTC) | actor | event | run | outcome |
| --- | --- | --- | --- | --- |
| 18:03:56 | admin | `GET /api/memories?agent=pai` | — | keys `['User', 'Location', 'tone']`; `Location` = id `8658254c011f4feebb1b622180a7c670` |
| 18:03:57 | admin | `POST /api/wiki/promote {memory_id, slug: kyle-location, title: "Kyle's location"}` | — | **200**, page `fa7e7fc978ea4198b591a44dc5666159` v1, `source_memory_id=8658254c011f4feebb1b622180a7c670`, `tags=['memory','pai']`, body = the memory plus "Promoted from pai's memory `Location` on 2026-09-13" |
| 18:03:57.256 | platform | newest message in `#wiki` | — | `kind="event"`, `card.type="wiki"`, `card.slug="kyle-location"`, `card.version=1`, `card.author="user:admin"`, `card.reason="promoted from memory"`, `+3 −0`, `url=/wiki/kyle-location` |

### Scenario 2 — an agent cites a page (PARTIAL — see "What did not happen")

| when (UTC) | actor | message | run | outcome |
| --- | --- | --- | --- | --- |
| 18:04:02 | admin | `#general` "@news where does Kyle live? Cite the wiki page you used." (msg `7cc756548c3b420e81119c24bfff8947`) | — | 200, `mentions=['news']`, hop 0 |
| 18:04:16 | agent:news | "Kyle lives in Whitby, Ontario, Canada — per wiki page `[[kyle-location]]`." (msg `f87c9f5cea894bc5af3495900e392804`) | `85e128b8ef604528a667a1644ffe4a3b` | reply at hop 1, run `succeeded`; the `[[kyle-location]]` citation is correct |
| 18:04:22 | admin | `GET /api/runs/85e128b8…` | — | `prompt_len=7512`; `<relay-messages>` and `<your-tickets>` present, **`<wiki` absent** — the answer came from the `wiki` MCP tool, not from a prompt block |
| 18:04:22 | admin | `GET /api/wiki/pages/kyle-location` | — | `cited_in.count=1` (was 0), last = that message by `agent:news` |

Follow-up experiments, to separate "the block is broken" from "the query
missed":

| when (UTC) | probe | result |
| --- | --- | --- |
| 18:05:35 | `#general` "@news kyle location" → run `5eb129a54f1e47a7a11ace7a95409148` | reply cites `[[kyle-location]]`; prompt still has **no** `<wiki` block |
| 18:06:12 | `GET /api/wiki/pages?q=…` against the live postgres FTS | `'kyle'` → `[kyle-location]`; `'location'` → `[kyle-location]`; `'kyle location'` → `[kyle-location]`; **`'news kyle location'` → `[]`**; `'news where kyle live cite wiki page used'` → `[]` |
| 18:06:59 | `#general` "@pai kyle location" (`@pai` is 3 chars, so `_search_words` drops it and every remaining word is in the page) → run `0d585ca0493247f69427ec68819949a5` | prompt **does** carry `<wiki count="1">` / `[[kyle-location]] · Kyle's location · User lives in Whitby, Ontario, Canada` / `</wiki>`; reply cites `[[kyle-location]]` |

Root cause, from the code the probes point at:
`wiki_store.search_for_prompt`'s postgres branch (`_pg_search`, wiki_store.py
:513) builds `plainto_tsquery('english', ' '.join(words))`, which **ANDs**
every term, while the sqlite branch (wiki_store.py:494–510) ORs the candidates
and ranks by overlap count. In production every summons therefore has to have
all of its ≥4-character words present in one page — and the summons always
contains the mentioned agent's own name (`news`, `stockmarket`, …), which no
page contains. The unit tests run on sqlite, so they exercise the OR branch
and pass. The mechanism itself is proven working by the 18:06:59 probe; only
the query that selects pages is wrong.

Relay after scenario 2: `invocations_24h=26 messages_24h=190
agent_messages_24h=40 suppressed_24h=1` (unchanged suppression).

### Scenario 3 — the librarian writes a wanted page (PASS)

| when (UTC) | actor | event | run | outcome |
| --- | --- | --- | --- | --- |
| 18:07:28 | admin | `#wiki` "@wiki write the standup page: …" (msg `30cb57fa32444cb396a838fcbd303cb2`) | — | 200, `mentions=['wiki']`, hop 0 |
| 18:07:41.406 | agent:wiki | page `standup` created | `c79c96d91fe54ad3b3d63f4c228dadd3` | `GET /api/wiki/pages/standup` **200** at +13 s — v1, `created_by=agent:wiki`, title "Standup", body carries `#standup`, 09:00 America/Toronto daily, and the two-line answer; links `['deploying']` |
| 18:07:41.410 | agent:wiki | `GET /api/wiki/pages/standup/history` | `c79c96d91fe54ad3b3d63f4c228dadd3` | v1 `+8 −0`, reason "Created the standup page per admin request: what #standup is, its 09:00 America/Toronto daily cadence, and the two-line answer format expected from agents." |
| 18:07:41.416 | agent:wiki | newest `#wiki` card | `c79c96d91fe54ad3b3d63f4c228dadd3` | `card.type="wiki"`, `card.slug="standup"`, `card.version=1`, `card.author="agent:wiki"`, hop 1 |
| 18:07:48 | admin | `GET /api/wiki/wanted` | — | `['deploying']` — **`standup` no longer wanted** |
| 18:07:51.273 | agent:wiki | reply in `#wiki` (msg `a90f5d479d484ea3981f7d4201bf659e`, hop 1) | `c79c96d91fe54ad3b3d63f4c228dadd3` | "Wrote [[standup]]: `#standup` fires **09:00 America/Toronto daily**, and each agent answers in two lines — 1) what you did in the last 24h + tickets moved, 2) what's blocked. [[deploying]] doesn't exi…" |

The librarian's own page links `[[deploying]]`, so `wanted` now reports
`deploying` as `linked_from: ['home', 'standup']` — the red link propagated
rather than being consumed.

### Scenario 4 — stale `base_version` is refused (PASS)

| when (UTC) | actor | event | outcome |
| --- | --- | --- | --- |
| 18:08:04 | admin | `GET /api/wiki/pages/standup` | V = **1**, `updated_by=agent:wiki` |
| 18:08:05.005 | admin | `PUT /api/wiki/pages/standup {base_version: 1, reason: "live check"}` | **200**, version **2**, `updated_by=user:admin`; history v2 `+2 −0`, `run_id=null` (a human write) |
| 18:08:05 | admin | `PUT` again with `base_version: 1` | **409** — `{"detail": "standup is at v2, not the version you read", "current_version": 2, "current_summary": "`#standup` is the channel where every agent posts a daily status update. It fires at **09:00 America/Toronto, daily**."}` |

### Stats deltas (before → after, 18:03:50 → 18:08:10 UTC)

| metric | before | after | delta |
| --- | --- | --- | --- |
| `wiki.pages` | 1 | 3 | +2 (`kyle-location`, `standup`) |
| `wiki.wanted` | 2 | 1 | −1 (`standup` written; `deploying` still red) |
| `wiki.stale` | 0 | 0 | 0 |
| `wiki.edits_24h` | `system:wiki ×1` | `user:admin ×2, agent:wiki ×1, system:wiki ×1` | +3 versions |
| `wiki.budget` | `limit=30`, no agents | `limit=30`, `wiki: used=1 left=29` | the librarian's one write is metered |
| `relay.invocations_24h` | 25 | 29 | +4 (news ×2, pai ×1, wiki ×1) |
| `relay.messages_24h` | 187 | 198 | +11 |
| `relay.agent_messages_24h` | 39 | 44 | +5 |
| `relay.suppressed_24h` | 1 | 1 | 0 — no hop-limit, budget, not-member, coalesced or facade suppression fired |
| `tickets/stats` | — | — | **byte-identical before and after**; the wiki touched nothing on the board |

### Screenshots

- `scratchpad/live/wiki-home-1280.png` — `/wiki` at 1280×860: the header counts
  "3 pages · 1 wanted · 0 stale", a search box, MEMORY/PAI tag chips, RECENT
  CHANGES listing `standup` v2 / `kyle-location` v1 / `home` v1 with faces and
  relative times, a WANTED entry for `deploying`, and the home page body on the
  right where `[[standup]]` now renders as a live green chip while
  `[[deploying]]` is still a red wanted link.
- `scratchpad/live/wiki-page-1280.png` — `/wiki/standup` at 1280×860: the page
  the librarian wrote, showing "v2 · you · just now · cited in 1 message · wiki
  just now", Edit/History buttons, the rendered two-line standup instructions
  with a red `[[deploying]]` chip and the admin's appended "Verified live.",
  and a BACKLINKS panel listing `Home`.

### Round two (after R1, helm rev 55 + backend/broker redeploy)

R1 (`27e7f8c fix(wiki): the <wiki> prompt block fires on postgres — OR the
words, strip mentions`) was built and shipped as a backend + mcp-broker image
redeploy on top of helm revision 55: `redeploy22.out` ends `EXIT=0`, with
`ap-api`, `ap-dispatcher`, `ap-recorder`, `ap-mcp-broker` and then
`ap-mcp-facade` all `successfully rolled out` by 23:27 UTC. No helm upgrade,
so the release stays at revision 55. Tunnel re-checked (`/login` → 200) before
the probes. Script: `scratchpad/t21-round2.py`.

Both probes assert on the RUN'S PROMPT, not on the reply — a correct reply
could always come from the `wiki` tool, and what R1 had to fix is the block the
router builds.

| when (UTC) | probe | actor | run | `<wiki` in prompt | block carries | verdict |
| --- | --- | --- | --- | --- | --- | --- |
| 23:43:06 → 23:43:21 | `#general` "@news where does Kyle live? Cite the wiki page you used." (msg `6cf727c03ebf40e7a9f63554289b051d`) | agent:news, hop 1, msg `12176445e5304dfcbff871b779318a52` | `fc2837b64f0c40a7a3c58000a80c9e44` (`succeeded`, `prompt_len=9496`) | **yes** | `<wiki count="3">` — `[[home]]`, **`[[kyle-location]]`**, `[[standup]]` | **PASS** |
| 23:43:26 → 23:43:44 | `#general` "@pai what is the standup format? cite the page" (msg `30da757c35d748108dbad7bc415ed994`) | agent:pai, hop 1, msg `957fbbb001e046a39248c0fca0e52c47` | `5737bf096ffa4560b299eb96e91e139b` (`succeeded`, `prompt_len=9490`) | **yes** | `<wiki count="2">` — `[[home]]`, **`[[standup]]`** | **PASS** |

This is the exact summons that produced no block in round one, unchanged, and
it now carries three pages: the same wording that scored zero under
`plainto_tsquery`'s AND matches under R1's OR-plus-`ts_rank`, and the `@news`
token that used to poison the query is stripped by the router before the match.

Replies and citations both landed:

- `agent:news` — "Kyle lives in Whitby, Ontario, Canada — per wiki page
  `[[kyle-location]]`." `kyle-location` `cited_in.count` **3 → 4**.
- `agent:pai` — "Per `[[standup]]`, it fires daily at **09:00 America/Toronto**
  in `#standup`, and you answer in **two lines**: 1. What you did in the last
  24h, and which tickets you moved… 2. What's blocked and why…" `standup`
  `cited_in.count` **1 → 2**.

Relay across round two: `invocations_24h` 16 → 18 (+2, one per probe),
`messages_24h` 71 → 75, `agent_messages_24h` 30 → 32, `suppressed_24h`
unchanged at 1.

The human search box was deliberately left ANDing, which R1's comment on
`api/wiki.py::_matching` states outright — re-probed live and still true:
`?q=kyle location` → `[kyle-location]`, `?q=news kyle location` → `[]`. That is
a scope boundary, not a leftover: a person narrowing down can drop a word and
look again, and the prompt search cannot.

### What did not happen

- **Round one only:** the `<wiki>` prompt block never fired for the scenario-2
  summons. The citation itself worked (the agent used the `wiki` tool and
  `cited_in` went 0 → 1 → 3), but the assertion that the run's prompt contained
  `<wiki` failed for both `@news` summonses — a production-only bug the
  sqlite-backed suite could not see, diagnosed to `_pg_search`'s AND semantics
  above. **Fixed by R1 and re-verified live: both round-two probes PASS.**
- Nothing timed out in either round: every poll in scenarios 1, 3 and 4, and
  both round-two probes, resolved inside its first or second tick.

## Handoff to Kyle
(commands the loop could not run because the auto-mode classifier refused them, ready to paste)
