# Plan — The engineer and the Workbench (design 24)

Design: `docs/design/24-coding-agent.md` (read it first; the sections named
in each task are pasted into that task's implementer prompt). Predecessors
built the same way: `2026-09-17-artifacts-and-image-studio.md` (the deploy
mechanics and the evidence tables that worked) and
`2026-09-12-tickets-agent-work-tracker.md` (the summons and the seeds).
The QA agent (design 25, plan `2026-09-18-qa-agent-and-tcms.md`) starts only
when this plan's "Definition of done" records PASS.

Kyle's ask, verbatim: "a coding agent that runs inside the platform. This is
probably a big ask, lots of ways to do a shitty job here, it'll need some
research, but i think i have the bones of what'd be needed to do it well in
place. Its outputs are tickets, slack conversations, wiki pages, and PRs
against the repo. I (or you, my laptop claude) approve those, at least for
now." ("slack conversations" = Relay.)

Acceptance criteria (each task names the ones it satisfies):

- **AC-1** An agent row with `role: dev` runs on the `runner-dev` image with
  an unattended shell, an anonymous clone on a branch named after its
  ticket, and no GitHub credential in its environment; every existing
  securityContext invariant holds; non-dev runs are byte-for-byte unchanged.
- **AC-2** At the end of a dev run the runner commits, runs `bin/ap-verify
  --changed`, bundles the branch and POSTs it; the API enforces the path
  policy (deny list, `push_path_globs`, `may_delete_tests`), pushes without
  force, opens or updates the PR with the captured verification, moves the
  ticket, posts the thread card and publishes `workbench.events`. A refused
  publish is a 422 and a sentence in the thread.
- **AC-3** `quota_ok` answers a structured boolean against the calling
  agent's own thresholds, editable through `agents_edit` and the editor.
- **AC-4** Assigning a ticket to `agent:engineer` produces, on pai, a PR
  whose body carries the runner's verification and whose ticket sits in
  `review` with the card in its thread; `/changes` lists it with its ticket
  chip; the engineer's reply links to its run.
- **AC-5** Deployed on pai (helm rev recorded), all suites green, live
  evidence recorded below.

## Loop protocol (read this every tick, follow it exactly)

You are the **orchestrator**. You do not write product code yourself. You
dispatch subagents, verify their evidence, commit, and update this file.

1. Re-read this plan top to bottom. Find the first `- [ ]` task in "Tasks".
   If there is none, run "Definition of done"; if it passes, stop the loop
   (ScheduleWakeup `stop: true`) after a PushNotification with the one-line
   outcome; if it fails, add a task under "Tasks → Repairs" and continue.
2. Dispatch **one opus implementer** (Agent tool, `model: "opus"`,
   `subagent_type: "general-purpose"`) with: the task text verbatim, the
   "Ground rules for implementers" block below verbatim, the design sections
   the task names pasted in full (not linked — the subagent has no
   conversation context), and the file paths from the task. Require it to
   work TDD: failing test → implementation → green, and to report the exact
   test commands it ran with their final summary lines. Tasks marked
   `[parallel with Tn]` may be dispatched in the same tick as that task, one
   implementer each; tell each implementer which paths the other owns and
   that it must not touch them. Tasks marked `[after Tn reports]` start the
   moment Tn's implementer reports (before Tn's review finishes).
3. When it reports, **verify the evidence yourself**: run the test commands
   it named (backend: `cd services/backend && .venv/bin/python -m pytest -q
   --timeout=120 <paths>`; runner: `cd services/runner &&
   ../backend/.venv/bin/python -m pytest -q`; broker: `cd services/mcp-broker
   && ../backend/.venv/bin/python -m pytest -q`; facade: its 3.12 venv as in
   the artifacts plan; web: `cd services/web && npm run -s lint && npm run -s
   check:tokens && npm run -s build && npx playwright test --reporter=line`;
   chart: `helm template charts/agent-platform -f
   charts/agent-platform/values-pai-nuc.yaml --set env.AP_SESSION_SECRET=x
   --set env.AP_INTERNAL_SECRET=y` with `spire.enabled` true and false; SDK:
   `python sdk/regenerate.py && git diff --exit-code sdk/` in a python:3.12
   container as the artifacts plan's T3 did). Do not trust a claim of green
   without output in your own transcript. Run git and every repo-relative
   command from the repo root, never from a `cd` that persisted.
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text,
   the design excerpt, and the path of a saved `git diff` of the uncommitted
   changes. Ask for findings ranked by severity, defects only, no style — and
   for every guard, limit, permission check, migration, git operation or
   path built from model text, ask it to state the WORST CASE: a credential
   plane leak (a GitHub token or the App key reaching a pod env, a log, a
   transcript frame, a PR body or a tool result); a prompt-trust boundary
   crossed (ticket text or `.ap/pr.md` content landing outside the untrusted
   region, an HTML comment surviving into the PR body, model text choosing a
   branch name or a path); a path segment or glob from model output (a branch
   of `../x`, a glob of `**`, a path with `..` in the bundle's diff, a symlink
   in the bundle); a bundle that is 200 MB, that rewrites `main`'s history,
   whose head is not a descendant, or that touches 5000 files; two dev runs
   publishing the same branch at once; a second `init_db` on a live DB; a
   non-dev run that gained a tool it did not have yesterday. For tasks tagged
   `[ui]`, dispatch additionally a **sonnet visual reviewer** that builds the
   app, serves it against the Playwright mock API (`tests/mock-api.ts`),
   screenshots the affected page(s) at 1280×800 and 390×844 in both themes
   with a throwaway spec it deletes afterwards, READS the PNGs, and reports
   what a picky human would notice. Never use `model: "fable"` or the default
   model for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" with file:line and move on. Loop at most twice per
   task; if a task still fails after two repair rounds, mark it `- [!]` with a
   one-paragraph note and continue to the next task — do not stall the build.
6. **Hold every commit until no implementer is editing the tree**: the
   pre-commit hook stashes and restores unstaged tracked files and would race
   a live editor. Commit on `main` (Kyle's workflow: single branch, direct
   commits, `git add` each file by name, never `-A`; never add files that may
   hold secrets — `exports.sh`, anything under `secrets/*/` other than
   `secret.yaml`/`verify_*.py`). Message: `feat(workbench): <task title>`
   plus a body, and the attribution trailer the session was given. Then edit
   this file: `- [x] **Tn …** (commit `<hash>`; <one line of what review
   changed>)`. Two `[ui]` tasks that share App.tsx/app.css/mock-api land as
   one commit.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task (or one parallel set)
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
   calling it red. Under host load the full backend suite takes 15–20 min
   instead of 3; do NOT kill a long run — run with `--timeout=120` so a true
   hang fails by test name. If sonnet subagents stall repeatedly, run that
   one review on opus rather than losing hours — note it in the tick.
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
   same way; check `curl localhost:18090/login` before each live step) at
   `http://localhost:18090`. (On 2026-09-18 plain `ssh pai`, `helm` and
   `kubectl` worked directly from this process with
   `KUBECONFIG=$HOME/.kube/pai-nuc.yaml` and `DOCKER_HOST=unix://$HOME/.rd/docker.sock`
   exported — try that first; fall back to Terminal.app on "No route to
   host". `helm` is not on pai's PATH over ssh: run it from the Mac.) The
   deploy script is checked in as
   `docs/superpowers/plans/reference-deploy-pai.sh` (copy it to the
   scratchpad, set `SCRATCH`, edit the image list; it does: buildx `--platform linux/amd64
   --provenance=false --load`, `docker save` → `scp` → `sudo k3s ctr -n
   k8s.io images import`, `helm upgrade ap charts/agent-platform -n
   agent-platform -f <stored values> -f charts/agent-platform/values-pai-nuc.yaml`
   — fetch the stored values fresh with `helm get values ap -n agent-platform
   > <scratchpad>/ap-stored-values.yaml`, never `--reuse-values` — then
   rollout restart api/dispatcher/recorder/web/broker, facade last). **This
   build adds the `runner-dev` image** (built from the REPO ROOT: `-f
   services/runner/Dockerfile.dev .`; it is several GB — build it once,
   early, in the background while other tasks run, and import it before T11;
   a Job picks it up with no restart). Push `main` to GitHub before the
   deploy (the synced checkout must carry `bin/ap-verify`, `.gitignore` and
   the docs), and again after T12.
10. **Hands Kyle may be asked for mid-build** (PushNotification, keep
    working meanwhile): none are required for this plan's DoD. The GitHub
    App's permissions (`contents: write`, `pull_requests: write`) are
    already what the wizards use; T11 confirms them live and records the
    answer in "Handoff to Kyle" if anything is missing.

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  run with `cd services/backend && .venv/bin/python -m pytest -q
  --timeout=120`). Web is React 19 + Vite + `@ap/ui` (`packages/ui`) in
  `services/web` (`npm run -s lint`, `npm run -s check:tokens`, `npm run -s
  build`, `npx playwright test`). The MCP broker is
  `services/mcp-broker/broker.py` (tests `cd services/mcp-broker &&
  ../backend/.venv/bin/python -m pytest -q`; it stubs fastmcp). The runner
  is `services/runner/runner.py` (tests `cd services/runner &&
  ../backend/.venv/bin/python -m pytest -q`; it has a `bare_and_clone`
  fixture that builds a local bare git remote — every git test uses a local
  bare repo, never the network). The launcher is
  `services/backend/agentplatform/joblauncher.py` (`build_job` is pure and
  tested by asserting on the `V1Job` it returns — `tests/test_joblauncher.py`
  shows the shape). The GitHub client is `agentplatform/github.py` (stdlib
  urllib; test it by monkeypatching `_send` or `urllib.request.urlopen`,
  never with a real request). Prod Python is 3.12 (dev venv is 3.14): no
  3.13+/3.14-only syntax, no annotation tricks that only pass locally. New
  backend deps go in `services/backend/pyproject.toml`.
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` / `token_client`
  fixtures in `tests/conftest.py` and sqlite; keep every new table and
  column portable to sqlite (JSON columns, no Postgres-only DDL outside
  `dialect == "postgresql"` guards, as `db.py:_ensure_wiki_ddl` does).
- Migrations are additive and live in `db.py`: `Base.metadata.create_all`
  makes new tables, `_ensure_columns` adds new columns to existing ones (it
  cannot apply an ORM default to existing rows — backfill explicitly), and
  one-off seeds go in a new `_ensure_*` function called from `init_db`,
  idempotent, gated by a `schema_marks` row (`_ensure_artist_seed` is the
  agent-row seed shape, `_ensure_art_channel` the channel seed,
  `_ensure_wiki_gardener_job` the job seed; a row that already exists is
  ADOPTED, never overwritten).
- An agent definition field is five things or it is nothing:
  `agentdefs.DEF_FIELDS`, the `AgentDef` column, `api/schemas.py`
  (`AgentDefIn`/`AgentDefOut`), `api/agents.py` `GRANT_FIELDS` or
  `EDIT_FIELDS`, and `services/mcp-broker/agenttools.py`'s mirror
  (`GRANT_LIST_FIELDS`/`GRANT_FIELDS`/`API_EDIT_FIELDS`); lockstep tests pin
  each pair. After any route or schema change regenerate the SDK
  (`python sdk/regenerate.py` — CI fails on drift; the artifacts plan did
  it inside `python:3.12-slim` because a 3.14 venv emits a different form)
  and re-pin the facade's tier counts in `services/mcp-facade/test_facade.py`
  (a new route changes KEEP/GATE/EXCLUDE; run-scoped and SSE routes go in
  `EXCLUDED_PATHS`).
- Relay is the substrate. A thread card, a refusal notice, any system row:
  written through `relay_store.post_relay_message` + `publish_relay_message`
  (or `summon_channel` for a scheduled post) — never a bare insert, never a
  second Kafka producer; cards are `kind="event"` and carry `mentions=[]`
  (a text row's `@mentions` are re-parsed by the router). Untrusted strings
  (ticket titles, branch names, paths, agent notes) are flattened and capped
  with `tickets.one_line` before they enter a system row. Ticket moves go
  through `ticket_store.move_ticket` with the agent actor and its run (the
  `_run_of` invariant: an agent actor with no run is refused).
- Kafka: topics are constants in `agentplatform/events.py` (`ALL_TOPICS`
  must include them) AND `charts/agent-platform/values.yaml` `topics.specs`
  (retentionMs quoted as a string). Use `make_envelope` / `Producer` /
  `FakeProducer` from `events.py`; never a bare aiokafka client. Tests inject
  `FakeProducer`; nothing in a test may open a real Kafka connection. An SSE
  feed follows `api/tickets.py`'s `TopicFeed` wiring in `api/app.py` /
  `api_main.py`.
- Roles and grants: `api/auth.py` `ROLES`/`READ_ROLES`/`INVOKE_ROLES`;
  `agentdefs.AGENT_ROLES` must stay a subset of `auth.ROLES` (a test pins
  it); the run-scoped `session` role reaches only `/api/runs/{id}/*` routes
  that check `api_key_run_id == run_id` (`api/runs.py::get_agentdef` is the
  pattern — copy its dependency, never widen it); `agentspec.
  PLATFORM_MCP_RELAY_TOOLS` is the grant list for participant-rung tools
  (add `quota_ok` there, never to `PLATFORM_MCP_TOOLS`); every grantable
  tool needs a `TOOL_HELP` entry (the lockstep test enforces broker ↔
  agentspec agreement). `_SENSITIVE_TOOLS` and the `--disallowedTools` path
  in `runner._permission_args` for non-dev, non-self-edit runs must not
  change by a byte — `test_permission_args_*` pin it; add cases, do not
  edit them.
- Git in the API and the runner: the token travels only through
  `GIT_ASKPASS` (`gitservice.GitWriter._auth_env`, `runner._git_env`) and
  never in a URL, argv, log line, exception text or transcript frame; every
  subprocess uses `capture_output=True` and re-raises with the command's
  name, not its output, when output could contain the token. Never `+` a
  refspec in new code. A branch name is validated against
  `^(coder|qa)/[a-z0-9][a-z0-9-]{0,40}$` at the API before it is used
  anywhere.
- Chart: any new env the backend reads is added to the `backendEnv` helper
  in `charts/agent-platform/templates/_helpers.tpl` (and `values.yaml`), so
  api, dispatcher and recorder all see it; `helm template` must render with
  `spire.enabled` true and false. Runner images are values under `images.`.
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no "added by" notes, no TODOs without an owner.
- Web pages: register routes in `services/web/src/App.tsx`, nav in
  `packages/ui/src/sidenav.tsx` `buildPlatformNav`, fixtures in
  `services/web/tests/mock-api.ts` (unmatched GETs fail tests on purpose;
  the `def()` helper is where new agent fields get their defaults), a row in
  `tests/smoke.spec.ts` `PAGES` for a new page, and the page in
  `tests/a11y.spec.ts`'s lists. Use `@ap/ui` subpath imports like
  `Tickets.tsx` does; reuse `components/relay/Message.tsx`'s card rendering
  (extend the `card.type` switch, never a second renderer). No raw hex
  colours. Mobile (<560px) must not break the shell; the a11y spec runs at
  390 too.
- Never widen scope, never commit, never push; report the exact test
  commands you ran with their final summary lines and the list of files you
  touched.

## Tasks

### Phase 0 — the definition and the policy (T1 ∥ T2)

- [x] **T1 Agent fields: role `dev`, the two grant fields, the two quota fields.** `[parallel with T2]` (AC-1, AC-3 precondition) (commit `a1e0491`; review: strict ints on the pct fields, empty glob segments refused)
  Design sections: "Data model", "Naming".
  Files: `agentdefs.py` (`AGENT_ROLES` gains `"dev"`; `DEF_FIELDS` gains
  `push_path_globs`, `may_delete_tests`, `quota_5h_max_pct`,
  `quota_7d_max_pct`; `AgentDefModel` validators: globs are a list of ≤ 32
  strings each ≤ 200 chars matching `^[A-Za-z0-9_./*?-]+$` with no `..`
  segment and no leading `/`; pct fields 0..100), `api/auth.py` (`ROLES`
  gains `"dev"` — it joins NO endpoint allow-list), `db.py` (`AgentDef`
  columns with defaults `[]`, `False`, `80`, `50`; `_ensure_columns` picks
  them up; an explicit backfill `UPDATE … WHERE quota_5h_max_pct IS NULL`
  in the `_ensure_tickets_seed` shape), `api/schemas.py`
  (`AgentDefIn`/`AgentDefOut`), `api/agents.py` (`GRANT_FIELDS` +=
  `push_path_globs`, `may_delete_tests`; the quota fields fall into
  `EDIT_FIELDS` automatically), `services/mcp-broker/agenttools.py`
  (`GRANT_LIST_FIELDS` += `push_path_globs`; `GRANT_FIELDS` +=
  `may_delete_tests`; `API_EDIT_FIELDS` += the two quota fields; the
  `agents_grant` tool accepts a bool for `may_delete_tests` the way it
  accepts `can_invoke`), `services/web/src/api.ts` (`AgentDef` type),
  `services/web/tests/mock-api.ts` (`def()` defaults), `sdk/` regenerated,
  `services/mcp-facade/test_facade.py` if any count moved (it should not —
  no route changed). Tests: the DEF_FIELDS ↔ schema ↔ agenttools lockstep
  tests pass with the new names; `PUT /api/agents/x` with
  `push_path_globs: ["services/backend/tests/**"]` by an `agents_edit`-only
  token is 403 and by admin is 200; a glob of `../x` or `/abs` is 422; a
  `role: dev` definition validates; a second `init_db` on a DB that already
  has the columns is a no-op; existing rows read back 80/50. Acceptance:
  backend + broker suites green; SDK diff clean; web `lint` + `build` green
  (no UI yet — T10 owns the editor).

- [x] **T2 `testpaths.py`: the glob matcher, `TEST_PATH_GLOBS`, the deny list, the policy.** `[parallel with T1]` (AC-2) (commit `31f0e0b`; review: regex ReDoS → linear wildcard walk with 200-char/16-segment bounds, copy status handled, NUL/backslash/old-path-less rename refused)
  Design sections: "Trust boundaries and guards" (the path policy,
  `TEST_PATH_GLOBS`, never force).
  Files: new `services/backend/agentplatform/testpaths.py` — `match(pattern,
  path) -> bool` (`**` = zero or more whole segments, `*`/`?` within a
  segment, anchored, case-sensitive, implemented by translating to a regex
  once and caching), `TEST_PATH_GLOBS` exactly as the design lists them,
  `PUBLISH_DENY_GLOBS`, `is_test_path(path)`, and `check_policy(changes:
  list[Change], *, push_path_globs: list[str], may_delete_tests: bool) ->
  PolicyVerdict` where `Change = (path, status: A|M|D|R, old_path|None,
  additions, deletions)` and the verdict is `ok` or `refused(reason)` with
  the design's three rules in order plus `tests_removed: [path]` (a `D` or
  an `R` away from a test path) and `test_lines_removed: int` for the
  flags. Pure, IO-free, no imports from `db`. Tests
  (`tests/test_testpaths.py`): a table of (pattern, path, expected) that
  pins `**` across zero and many segments, `*` not crossing `/`,
  case-sensitivity, anchoring (`tests/**` does not match `x/tests/a.py`),
  every `TEST_PATH_GLOBS` entry against one real path from the tree and one
  near-miss (`services/web/playwright.config.ts`, `.github/workflows/ci.yaml`,
  `bin/ap-verify`); policy: deny list wins over everything; empty globs
  allow any non-denied path; non-empty globs refuse the first miss by name;
  a deleted test refused without the flag and allowed with it; a rename
  from a test path to a non-test path counts as removed; a path containing
  `..` or starting with `/` is refused before any rule. Acceptance: suite
  green; the module has no dependency on the ORM.

### Phase 1 — the run (T3 ∥ T4 ∥ T6; T5 after T4 reports; T7 after T2 and T3 are committed)

- [x] **T3 `GET /api/quota/ok` and the `quota_ok` broker tool.** `[parallel with T4 and T6; after T1 is committed]` (AC-3) (commit `5f4a76f`; review: `grant=True` on the broker tool)
  Design sections: "Broker tools" (`quota_ok`), "Data model" (the quota
  fields).
  Files: `api/quota.py` (`GET /api/quota/ok`, `VIEW` roles; reads the
  snapshot; if `stale`, one `refresh` through the existing lock and
  short-circuit; thresholds from the caller's agent row when
  `request.state.api_key_agent` is set, else the column defaults; response
  `{ok, five_hour_pct, seven_day_pct, five_hour_max_pct,
  seven_day_max_pct, stale, reason}` where `reason` is one sentence naming
  the window that failed or "ok"; a snapshot with null utilization → `ok:
  false`, reason "no reading yet"), `api/schemas.py` (`QuotaOk`),
  `agentspec.py` (`TOOL_QUOTA_OK = "mcp__platform__quota_ok"` appended to
  `PLATFORM_MCP_RELAY_TOOLS`; `TOOL_HELP` entry "Quota gate" with the
  design's wording), `services/mcp-broker/broker.py` (`quota_ok()` core
  tool beside `get_quota_usage`, `_metered("quota_ok")`, returns the JSON
  line then the sentence; a 503 falls back to the cached read with `ok`
  computed from it and `stale: true`), `sdk/` regenerated, facade test
  counts (KEEP +1). Tests: `tests/test_quota_api.py` — under/over each
  threshold, an agent token gets its row's thresholds, a human gets the
  defaults, stale triggers exactly one probe (the existing fake), no
  reading → `ok: false`; broker test — the tool's request path and the
  shape of its answer; the TOOL_HELP lockstep test. Acceptance: backend,
  broker and facade suites green; SDK diff clean.

- [x] **T4 Launcher: the dev profile, the dev image, settings and chart wiring.** `[parallel with T3 and T6; after T1 is committed]` (AC-1) (commit `39b60be`; review: `AP_PUBLISH_MAX_BYTES` wired into the dev env)
  Design sections: "The dev run, step by step" (step 2), "Trust boundaries
  and guards" (the pod holds no repository credential; pod hardening is
  unchanged), "Data model" (settings).
  Files: `config.py` (`runner_dev_image`, `dev_max_turns`,
  `dev_verify_timeout_seconds`, `dev_workspace_size_limit`,
  `dev_shm_size_limit`, `publish_max_bytes`, `web_internal_url`),
  `joblauncher.py` (`_is_dev(manifest)`: `manifest.role == "dev"`;
  `_is_self_edit` unchanged and false for dev; `build_job(…, dev: bool)`:
  image, resources `2Gi/500m` → `6Gi/3`, `workspace` emptyDir gains
  `size_limit`, a `dshm` emptyDir `medium="Memory"` with `size_limit`
  mounted at `/dev/shm`, env `AP_WORKSPACE=dev`, `AP_GIT_REMOTE_URL`,
  `AP_DEFAULT_BRANCH`, `AP_MAX_TURNS`, `AP_VERIFY_TIMEOUT`,
  `AP_WEB_URL`, `PLAYWRIGHT_BROWSERS_PATH=/ms-playwright`; NO
  `AP_GITHUB_TOKEN`/`AP_SELF_EDIT`; `launch` passes `dev=self._is_dev(m)`
  and never mints a token for it), `charts/agent-platform/values.yaml`
  (`images.runnerDev.{repository: agent-platform-runner-dev, tag: dev}`),
  `templates/_helpers.tpl` `backendEnv` (`AP_RUNNER_DEV_IMAGE`,
  `AP_WEB_URL: http://{{ .Release.Name }}-web:{{ .Values.web.service.port }}`),
  `docs/deployment.md` (a row for the image, built from the repo root; the
  dispatcher launches it, so a new image applies to the next dev run).
  Tests (`tests/test_joblauncher.py`): a `Manifest(role="dev")` Job uses the
  dev image, the resources, the two emptyDirs with limits, the env set and
  NOT the token vars; its securityContext asserts are the same block the
  existing hardening test uses (copy the assertions, they must all hold); a
  `Manifest(role="coder")` Job is byte-identical to before (snapshot the
  existing test's expectations); `launch` with a GitHub App configured and
  `role: dev` does not call `installation_token` (a fake that raises if
  called). Acceptance: backend suite green; `helm template` renders in both
  spire modes with the two new env vars.

- [x] **T5 Runner: `workbench.py` — prepare, permissions, finalize, publish.** `[after T4 reports]` (AC-1, AC-2) (commit `e8c7ee8`; review: remote_url validated + `--`, npm env allowlist, fork-point deepening, refused publish fails the run, max-turns default, POST retry, lazy import for the seam test, CI runs the runner dir)
  Design sections: "The dev run, step by step" (steps 3–6), "Trust
  boundaries and guards" (the pod holds no repository credential; untrusted
  text; evidence is captured), "The engineer" (only the `<workbench>` block
  wording is needed here: branch, base, commits ahead, the open PR, the
  four rules — commit as you go, never push, run `bin/ap-verify --changed`
  before you finish, write `.ap/pr.md`).
  Files: new `services/runner/workbench.py` — `fetch_workbench()` (`GET
  /api/runs/{id}/workbench` via the runner's `_api_req`, session token),
  `prepare(repo_dir, wb, env)` (anonymous `git clone --depth 50 --branch
  <base> <remote>`; if `existing`: `git fetch --depth 50 origin <branch>`
  + `checkout -B <branch> origin/<branch>`, else `checkout -b <branch>`;
  `$HOME/.gitconfig` with `user.name=<agent>`, `user.email=<agent>@agent-platform.local`;
  copy `/opt/npm-cache` → `$HOME/.npm` when present; `npm ci
  --prefer-offline --no-audit --no-fund` at the repo root when
  `package-lock.json` exists — its failure is a transcript frame, not a
  run failure; returns the `<workbench …>` block text built ONLY from
  computed facts, every value passed through `html.escape`),
  `finalize(repo_dir, wb, env, run_id)` (`git add -A` + commit `"<agent>:
  checkpoint at run end"` only when the tree is dirty; `git rev-list
  --count <base>..HEAD` — zero → `{"published": false, "reason": "no
  changes"}`; run `["python3", "bin/ap-verify", "--changed", "--base",
  f"origin/{base}", "--out", "/workspace/verify"]` with
  `timeout=AP_VERIFY_TIMEOUT`, capturing `verify.json` or `{"ok": false,
  "error": "verify timed out"|"verify crashed: <exit>"}`; `git bundle
  create /workspace/publish.bundle origin/<base>..HEAD`; refuse to send a
  bundle over `AP_PUBLISH_MAX_BYTES` (env from the launcher, default 16
  MiB) with a clear frame; read `.ap/pr.md` if present (≤ 32 KiB, else
  truncated with a note); `POST /api/runs/{id}/publish`; return the API's
  body or `{"published": false, "status": <code>, "reason": <body text ≤
  2 KiB>}`), `runner.py` (`_permission_args(self_edit, has_api_token,
  agent, dev=False)`: the `dev` case returns `["--permission-mode",
  "acceptEdits", "--strict-mcp-config", "--allowedTools", "Bash", "Read",
  "Edit", "Write", "NotebookEdit", "Glob", "Grep", *declared harness tools,
  "mcp__platform__*"]` (the flag BEFORE the variadic list, so nothing can
  read it as a tool name) — the `self_edit` and default cases untouched;
  `_run`: when `AP_WORKSPACE == "dev"` call `prepare` before `claude`, append
  the block to the prompt, add `--max-turns $AP_MAX_TURNS`, set `cwd` to the
  clone; after `rc == 0` call `finalize` and emit a transcript frame
  `{"type": "workbench", **result}` — a failed finalize is a frame AND
  `state = "failed"` exactly as `self_edit` does), both Dockerfiles copy
  `workbench.py`. Tests (`services/runner/test_workbench.py`, with
  `bare_and_clone`-style local remotes and a fake `_api_req`): prepare on
  a new branch and on an existing remote branch (the local commit is
  present after checkout); the gitconfig has no token and no remote
  credential; the `<workbench>` block escapes a ticket key of
  `<script>` and never contains the API response's free text; finalize:
  clean tree + zero commits → no POST; dirty tree → checkpoint commit then
  POST with a bundle that a fresh clone can `git bundle verify` and fetch;
  verify timeout → `ok: false` with the reason and the POST still happens;
  bundle over cap → no POST and the frame says so; `.ap/pr.md` over 32 KiB
  truncated; `_permission_args` dev case pinned, and the three existing
  cases pinned unchanged (do not edit their tests). Acceptance: runner suite
  green.

- [x] **T6 `bin/ap-verify`, `pytest-cov`, `.ap/` ignore, `chromiumSandbox`.** `[parallel with T3 and T4]` (AC-2) (commit `7a493e8`; review: verifier on the deny list, runner dir, sdk-drift + storybook suites, helm dependency build, streamed output, relative files)
  Design sections: "The dev run, step by step" (the `bin/ap-verify`
  paragraph).
  Files: new `bin/ap-verify` (executable, `#!/usr/bin/env python3`, stdlib
  only, importable as a module for tests via `bin/ap_verify.py` + a
  two-line shim, or a single file loaded with `importlib` in the test —
  choose one and say which): the suite table from the design as data; `--changed`
  (default), `--all`, `--base <ref>` (default `origin/main`), `--out <dir>`
  (default `./.ap/verify`), `--list` (print the suites that would run, no
  execution), `--timeout <s>` per suite (900); runs each suite with
  `subprocess.run(capture_output=True, timeout=…)`, `cwd` per suite,
  `PYTHONPATH` set for suites that import `agentplatform` (facade, and the
  backend when not installed), `PLAYWRIGHT_JUNIT_OUTPUT_NAME` /
  `PLAYWRIGHT_JSON_OUTPUT_NAME` pointing into `--out` for the web suite
  with `--reporter=line,junit,json`; backend gets `--junitxml=<out>/junit-
  backend.xml --cov=agentplatform --cov-report=xml:<out>/coverage-backend.xml`
  only when `pytest_cov` imports, else plain; writes `<out>/verify.json`
  `{ok, base, head, changed: [...], suites: [{name, cmd, cwd, exit,
  seconds, skipped_reason, tail}], files: [...]}` with `tail` = last 40
  lines of combined output; exit 0 iff `ok`; never modifies the tree; a
  suite whose tool is missing (`helm`, `docker`) is `skipped_reason`, not a
  failure; `charts/**` → `helm lint …` when on PATH), `services/backend/
  pyproject.toml` (`pytest-cov`, `pytest-timeout` in `[dev]`), `.gitignore`
  (`.ap/`), `services/web/playwright.config.ts` (`use.chromiumSandbox:
  !process.env.AP_WORKSPACE` — CI keeps the sandbox), `docs/building-blocks/
  README.md` untouched (T12 documents). Tests (`services/backend/tests/
  test_ap_verify.py`, loading the script as a module): the path → suite
  mapping table (one path per suite, `docs/` → none, a mixed change picks
  both), `--list` output, a fake suite (`["python3", "-c", "exit(1)"]`
  injected through the table) → `ok: false` with exit 1 and a tail, a
  timeout → `skipped_reason` absent and exit `None` with "timed out",
  `verify.json` shape, the JUnit flags present for backend. Acceptance:
  `bin/ap-verify --list` on the repo prints the full table; `bin/ap-verify
  --changed --base HEAD~1` on the laptop runs the suites for the last commit
  and writes `verify.json` (paste the summary); backend suite green with
  `pytest-cov` installed in the venv.

- [x] **T7 API: `workbench` and `publish` routes, `workbench.py` publish service, `GitHubClient` additions, the topic.** `[after T2 and T3 are committed; after T5 reports]` (AC-2) (commit `1e0ac44`; review: runner-attested publish nonce (one-shot from GET /workbench, hash on the run row, held in runner memory only), body bounded before parsing, @handles/#refs neutralised in notes, per-run lock)
  Design sections: "The dev run, step by step" (step 7), "Trust boundaries
  and guards" (publish is the single door; the path policy; never force;
  evidence is captured; untrusted text), "API", "Relay", "Kafka".
  Files: new `services/backend/agentplatform/workbench.py` — `branch_for(run,
  ticket) -> str` (`coder/<key.lower()>` or `coder/run-<id[:12]>`; a QA
  prefix is design 25's — leave a `prefix` argument defaulting to `coder`),
  `workbench_view(run, ticket, existing, open_pr)` (`existing` from `git
  ls-remote --heads <git_remote_url> <branch>` run in a thread with no
  credential, `open_pr` from `gh_client.find_open_pull_request`), `publish(session_factory,
  producer, settings, github_app_token, gh_client, *, run, agent_def,
  bundle: bytes, head_sha, base_sha, verify, notes_md) -> PublishResult`:
  clone with `GitWriter` into a `TemporaryDirectory`; `git bundle verify`;
  `git fetch <bundle> <branch>:refs/bundle/head` (refuse a bundle whose
  `list-heads` names anything but the one branch); ancestry: when the remote
  branch exists, `merge-base --is-ancestor origin/<branch> refs/bundle/head`
  else `--is-ancestor origin/<base> refs/bundle/head`, else 409; `git
  diff --name-status -M` and `--numstat` between `origin/<base>` and the
  head → `Change`s (a path with `..`, a leading `/`, or a symlink in the
  tree at the head — `git ls-tree -r` mode `120000` — is refused);
  `testpaths.check_policy` → 422 with the reason; > 200 files → 422;
  `git push origin refs/bundle/head:refs/heads/<branch>` (no force) → a
  non-zero exit is 409 "branch moved"; PR: `find_open_pull_request(branch)`
  → `update_pull_request` else `open_pull_request` with title `<agent>:
  <KEY> <ticket title one_line>` (or `<agent>: run <id[:12]>`), body =
  the platform header (ticket link, run link, agent, branch, commit count),
  the files table (status, path, +/−, `⚠️ test` on removed test lines), the
  verification table from `verify` ("captured by the runner" / "verify did
  not run: <reason>"), then `### <agent>'s notes (agent-authored)` with
  `notes_md` after stripping `<!-- … -->` (and any line starting with the
  summary `MARKER`) and capping 32 KiB; when `agent_def.push_path_globs` is
  non-empty, `enable_auto_merge(pr["node_id"])` and record `auto_merge:
  true|false` (a GraphQL error is a warning string, not a failure); ticket
  move via `ticket_store.move_ticket` (`review` when `verify.ok` or verify
  is null-with-no-suites, `blocked` with reason `verify failed: <first
  failing suite>` otherwise), actor `agent:<name>`, the run; the thread card
  (`kind="event"`, `card={type: "publish", …}`, body per the design's
  Relay section, `mentions=[]`, posted in the ticket's thread or `#eng`);
  the `workbench.events` envelope (`workbench.event`, best-effort
  post-commit); on a refusal the card is the `⛔` row and the envelope is
  `refused` — pushed nothing; `api/runs.py` (`GET /api/runs/{id}/workbench`
  and `POST /api/runs/{id}/publish`, both with the `get_agentdef`
  dependency and run-id check; the POST body is JSON with `bundle_b64`,
  decoded after a length check against `publish_max_bytes`; 413 over cap;
  the agent def is loaded from `agent_store`; the App token from
  `gitedit._github_app_token`; a run whose agent is not `role: dev` → 403),
  `github.py` (`update_pull_request`, `enable_auto_merge` via `POST
  /graphql`), `events.py` (`TOPIC_WORKBENCH_EVENTS = "workbench.events"`
  in `ALL_TOPICS`), `charts/agent-platform/values.yaml` (`{name:
  workbench.events, partitions: 3, retentionMs: "2592000000"}`), `api/
  workbench_feed.py` (`GET /api/workbench/events`, a `TopicFeed` over the
  topic, the tickets shape) + `api/app.py` + `api_main.py` wiring, `api/
  pulls.py` (`list_pull_requests` includes heads starting with `coder/`
  OR `qa/`; each row gains `ticket_key` parsed from the branch with the
  ticket-key regex, `agent` parsed from the body header line, `auto_merge`
  from `pr["auto_merge"]` is not None), `api/schemas.py`, `sdk/`, facade
  `EXCLUDED_PATHS` (the two run-scoped routes and the SSE). Tests
  (`tests/test_workbench.py` with a local bare remote as the "GitHub"
  remote — `settings.git_remote_url` pointing at it, `_build_writer`'s
  no-auth path — a `FakeGitHubClient` recording calls, `FakeProducer`, and
  bundles built by a helper that commits into a clone): happy path opens a
  PR, moves the ticket to `review`, posts the card, publishes the envelope
  with the file list; an existing PR is updated not duplicated; verify
  failed → `blocked` with the suite name and a draft-marked title prefix
  `[verify ✗]`; deny-list path → 422, nothing pushed, `⛔` card, `refused`
  envelope; globs non-empty and a miss → 422 naming the path; a deleted test
  without `may_delete_tests` → 422, with it → 201 and `tests_removed`
  listed; head not descending from `main` → 409; remote branch moved → 409;
  a bundle with two heads → 422; a symlink → 422; 201 files → 422; a `..`
  path → 422; notes with `<!-- ap:ai-summary sha=x -->` and a hidden
  comment → both gone from the body; a non-dev agent's run → 403; a session
  token for another run → 403; `push_path_globs` non-empty →
  `enable_auto_merge` called once; a GraphQL failure → warning in the card,
  201; `GET /api/pull-requests` lists a `qa/qa-3` head with `ticket_key`
  `QA-3`; SSE replays a `published` envelope. Acceptance: backend + facade
  suites green; SDK diff clean; `ALL_TOPICS` and `values.yaml` agree.

- [x] **T8 The `runner-dev` image.** `[parallel with T7; after T5 reports]` (AC-1) (commit `c6444a8`; review: manifests-only stage keeps pip/npm cached, uid asserted, exact mcp pin, root .dockerignore; amd64 chrome/claude smoke deferred to T11 — qemu on this Mac segfaults the lean image identically, the arm64 build passed all four)
  Design sections: "The `runner-dev` image".
  Files: new `services/runner/Dockerfile.dev` (base
  `mcr.microsoft.com/playwright:v<X>-noble` with `<X>` copied from
  `services/web/package.json` `@playwright/test` (strip the caret; confirm
  the tag exists on mcr.microsoft.com before writing it — WebFetch the tag
  list — and record what you found); `apt-get install git python3-venv
  python3-pip`; `npm i -g @anthropic-ai/claude-code@<the exact tag in
  services/runner/Dockerfile>` and `@playwright/mcp@0.0.81` (verify the
  current version on registry.npmjs.org and pin what you verified);
  `/opt/chromium/chrome` symlinked to the resolved
  `/ms-playwright/chromium-*/chrome-linux*/chrome`; `python3 -m venv
  /opt/venv`; `pip install services/backend[dev] pytest-cov pytest-timeout
  -r services/runner/requirements.txt -r services/mcp-broker/requirements.txt
  -r services/mcp-facade/requirements.txt -r services/tool-executor/
  requirements.txt -r services/connector-discord/requirements.txt` plus
  `find tools -name requirements.txt -exec pip install -r {} \;` (copy only
  the requirement files and pyproject before this layer so the layer caches
  across source changes); a `.pth` in the venv's site-packages naming
  `/workspace/repo/services/backend` and `/workspace/repo/sdk`; `npm ci`
  in `/opt/npm-warm` from the root `package.json` + `package-lock.json` +
  every workspace `package.json`, with `npm_config_cache=/opt/npm-cache`,
  then `rm -rf /opt/npm-warm`; `useradd -m -u 1001 runner`; `/workspace`
  owned by it; `COPY services/runner/runner.py services/runner/workbench.py
  /app/`; same `ENTRYPOINT`), `services/backend/tests/test_runner_dev_image.py`
  (parses the Dockerfile: the base tag equals the `@playwright/test` version
  in `package.json`; the claude-code tag equals the lean Dockerfile's; the
  `@playwright/mcp` pin is present; no `ANTHROPIC_API_KEY`/`sk-ant-`
  strings), `.github/workflows/ci.yaml` (NO build job for this image — CI
  runners cannot afford it; the test above is the drift guard; add a
  one-line comment saying so next to the `runner` job), `docs/deployment.md`
  (build command, from the repo root). Then BUILD IT locally in the
  background (`docker buildx build --platform linux/amd64 --provenance=false
  --load -t agent-platform-runner-dev:dev -f services/runner/Dockerfile.dev
  .`) and run three smoke commands in the built image as uid 1001 with a
  read-only rootfs and the three emptyDir-like tmpfs mounts (`docker run
  --read-only --tmpfs /home/runner --tmpfs /workspace --tmpfs /tmp
  --user 1001`): `python3 -c "import agentplatform, pytest_cov"` with the
  `.pth` resolving against a checked-out `/workspace/repo`; `node -e
  "require('@playwright/test')"` after `npm ci --prefer-offline` from the
  warmed cache (report the seconds); `/opt/chromium/chrome --headless
  --no-sandbox --dump-dom about:blank`; and `claude --version`. Record the
  image size. Acceptance: the backend test passes; the four smoke commands
  succeed in the container with their output quoted in the report; the
  image is loaded locally for T11.

- [x] **T9 Seeds: `#eng`, the engineer, `eng-queue`.** `[after T7 is committed]` (AC-4) (commit `16d8f5b`; review: ship — the ticket's review/blocked move is the publish route's, not the prompt's)
  Design sections: "The engineer (seeded row)", "Data model" (Seeds).
  Files: `db.py` (`ENG_CHANNEL_MARK = "eng-channel-v1"` — the `#eng` open
  channel with `ticket_prefix="ENG"` and a welcome row "Assign a ticket to
  @engineer and it opens a PR; the platform publishes, humans merge";
  adopt an existing `#eng` and only set the prefix when it is null;
  `ENGINEER_SEED_MARK = "engineer-seed-v1"` — the row exactly as the design's
  section specifies, with `ENGINEER_PROMPT` written IN FULL in the file
  from the design's paragraph (process, unconditional rules, hand-back),
  `ENGINEER_DESCRIPTION`, `role="dev"`, `timeout_seconds=5400`,
  `quota_5h_max_pct=95`, `quota_7d_max_pct=90`, `platform_tools=[TOOL_RELAY,
  TOOL_TICKETS, TOOL_WIKI, TOOL_QUOTA_OK, TOOL_ARTIFACTS]`, `harness_tools=
  ["Glob", "Grep"]`, `changed_via="seed"`; `ENG_QUEUE_MARK =
  "eng-queue-job-v1"` — the ScheduledJob in `WIKI_GARDENER_JOB`'s shape,
  `relay_channel="eng"`, `cron="0 7 * * 1-5"`, `timezone="America/Toronto"`;
  all three called from `init_db` after the grant sweeps and the artist
  seed), `docs/building-blocks/agents.md` (the seeded-agents list gains the
  engineer — the rest of the docs are T12's). Tests: the three seeds are
  idempotent (a second `init_db` adds no version and no second channel or
  job); the row has the grants, the role and the thresholds; the prompt
  contains "never remove or weaken a test" and "never `git push`"; the
  engineer is summonable — assigning a ticket to `agent:engineer` in a test
  channel yields a run spec with `ticket_id` (the tickets assign test
  shape) whose manifest role is `dev`; `#standup`'s `@all` reaches it (not
  system). Acceptance: backend suite green.

### Phase 2 — the web (T10; starts when T7 reports)

- [x] **T10 `[ui]` Agent editor fields, `/changes` with both prefixes and live rows, the publish card, the `workbench` run frame.** `[after T7 reports]` (AC-3, AC-4) (commit `864fc59`; code review ship; visual review: agent tab strip scrolls itself and Changes actions cell is sticky at 390; runner refusal reason unwrapped in `5445ba4`)
  Design sections: "Web", "Relay", "API" (`GET /api/pull-requests`).
  Files: `services/web/src/components/AgentForm.tsx` (two `NumberField`s
  "Quota gate: 5-hour max %" / "7-day max %" beside Timeout with a one-line
  hint "`quota_ok` refuses above these"; the role `Select` gains `dev` with
  the design's description; the grants panel — wherever `can_invoke` is
  edited — gains **Push path globs** (a `Textarea`, one per line, split and
  trimmed on save) and **May delete tests** (a checkbox), gated by the same
  authority check as `can_invoke`), `pages/Settings.tsx` `ROLE_DESC` (`dev`),
  `pages/Changes.tsx` (a `Chip` linking the ticket key to `/tickets/<key>`,
  the agent's `Face`, an "auto-merge" chip when `auto_merge` is true; rows
  refresh on a `published` frame from `GET /api/workbench/events` through
  the same `EventSource` pattern `useTickets` uses; the merge/close buttons
  unchanged), `components/relay/Message.tsx` (`card.type === "publish"`: a
  chip row — branch in code, `PR #n ↗` (external link), `n files`, a red
  "removes tests" chip when `tests_removed.length`, "verify ✓/✗", and the
  existing "view run ↗"; the `⛔` refused variant renders the reason in the
  body as today's system rows do), `pages/RunDetail.tsx` (the `workbench`
  transcript frame renders like the `self_edit` frame: branch, PR link, or
  the refusal — find how `self_edit` frames are rendered and extend the
  same switch), `src/api.ts` (types), `tests/mock-api.ts` (`/api/pull-
  requests` rows gain `ticket_key`, `agent`, `auto_merge`; `/api/workbench/
  events` fulfilled empty SSE; a `publish` card message in the relay
  fixture; a run with a `workbench` frame), new `tests/workbench.spec.ts`
  (editor shows and saves the four fields with the right payload; `dev` is
  selectable; Changes shows the ticket chip and the auto-merge chip; the
  Relay publish card renders the PR link and the red chip; the run page
  shows the branch), `tests/agent-editor.spec.ts` if its field count is
  asserted. Acceptance: web suite green (lint, tokens, build, playwright);
  the visual reviewer's screenshots of `/agents/<name>` Config, `/changes`
  and a Relay thread with a publish card at 1280 and 390, both themes, with
  no overflow.

### Phase 3 — ship it (T11 → T12)

- [ ] **T11 Deploy and live verification.** `[after T9 and T10 are committed]` (AC-5, and the proof of AC-1…AC-4)
  The orchestrator does this task itself with the Terminal.app mechanics
  from protocol step 9, dispatching an **opus verify agent** for the
  through-the-forward steps if its own context is tight. First `git push
  origin main`. Build and import backend, runner (lean — `workbench.py` is
  copied into it too), **runner-dev** (already built in T8; `docker save`
  + import takes minutes — start it first), web, mcp-broker, mcp-facade;
  fetch the stored values fresh; `helm upgrade` with stored values + the
  pai overlay; rollouts; facade last. Confirm: `kubectl get pods` all
  Running; the topics job created `workbench.events`; the dispatcher log
  shows the `#eng`, engineer and eng-queue seeds ran once; `GET
  /api/agents/engineer` shows `role: dev` and the thresholds; `GET
  /api/quota/ok` answers through the forward. Then record in "Live
  verification" below:
  1. **Quota gate (AC-3).** `GET /api/quota/ok` as admin: quote the body.
     Lower `engineer`'s `quota_7d_max_pct` to 1 via `PUT`, call again with
     an engineer-scoped check (the broker test double is not live — use
     `agents_edit` from the laptop MCP or the API as admin and read the
     defaults), restore it. Quote both.
  2. **A real ticket (AC-1, AC-2, AC-4).** In `#eng` create `ENG-1`:
     "`bin/ap-verify --list` should print each suite's cwd beside its name;
     add it and a test" (a small, real change under `bin/` and
     `services/backend/tests/`). Assign it to `agent:engineer` from the
     board. Record: the run id and its pod (`kubectl get pod -l
     app.kubernetes.io/component=runner -o yaml` → the image is
     `runner-dev`, the env has NO `AP_GITHUB_TOKEN`, resources and the
     `dshm` mount are present, securityContext unchanged — quote the
     fields); the transcript's `<workbench>` block; the plan comment in
     the thread; the `workbench` frame; the PR number, its body's
     verification table (quote it), the ticket in `review`, the thread
     card (screenshot at 1280 dark), the `workbench.events` envelope
     (kafka console consumer, last 1); `/changes` with the chip
     (screenshot); the engineer's reply with "view run ↗". Time from
     assignment to PR.
  3. **Refusal (AC-2).** In `#eng` create `ENG-2`: "add a comment to
     `.github/workflows/ci.yaml` explaining the runner job" and assign it to
     `agent:engineer`. Two outcomes pass, record which: (a) the engineer
     declines by prompt — quote its thread reply and the ticket's state; or
     (b) it edits the file and the API refuses the publish — quote the
     422 body from the `workbench` transcript frame, the `⛔` thread card,
     the `refused` envelope, and confirm no `coder/eng-2` branch exists on
     GitHub. If (a), ALSO prove the 422 from the laptop: run the backend
     test `test_workbench.py::test_deny_list_path_refused` and quote its
     pass line (the session token of a finished run is gone with its pod,
     so a live HTTP call against it is not possible).
  4. **Resume (AC-2).** Comment on `ENG-1`'s thread "@engineer also print
     the timeout" → a second run fetches `coder/eng-1` (the transcript's
     `<workbench existing="true" commits_ahead=…>`), the same PR is
     updated (not a second one), the branch is not force-pushed (`git
     reflog` on the laptop's fetch of the branch shows a fast-forward).
  5. **Non-dev runs unchanged.** `@pai` in `#general` still answers; its
     pod is the lean image with `--disallowedTools` in its args (quote
     from the pod's transcript frame or `kubectl logs`).
  6. **Merge (AC-4).** Kyle or the orchestrator merges `ENG-1`'s PR from
     `/changes` (admin); the ticket is moved to `done` by hand; note that a
     merge does not move tickets (deferred).
  7. **Cluster.** `helm history` revision; `kubectl top pod` for the dev pod
     at its peak; the dev pod's wall time; `kubectl get job` TTL cleanup.
  Then set the design's Status to shipped with the helm revision. Acceptance:
  items 1–7 recorded with commands, timestamps and screenshot paths; the
  "Definition of done" passes.

- [ ] **T12 Docs.** `[after T11]` (all ACs)
  Files: `docs/design/24-coding-agent.md` (Status → shipped with the rev;
  "AS BUILT" from the ticked tasks and the implementer reports; a
  "Deferred" refresh), new `docs/building-blocks/workbench.md` (what a dev
  run is, what the pod holds and does not, the branch naming, publish and
  the path policy in plain words, `bin/ap-verify`, the PR body's sections,
  the thread card, `quota_ok`, how to make another dev agent, how to change
  the engineer's thresholds and globs), `docs/building-blocks/README.md`
  (index row: Workbench — Postgres (+ Kafka) + GitHub — how dev agents
  change the code), `docs/building-blocks/glossary.md` (Workbench, Dev run,
  Publish, Path policy, Verify, Engineer, `#eng`; the runner row gains "two
  images: the lean one, and `runner-dev` for `role: dev`"; Platform agents
  gains the engineer is NOT a system agent — say why), `docs/building-blocks/
  agents.md` (role `dev`, the four fields), `docs/building-blocks/changes.md`
  (a section "Dev-run PRs" — `coder/<ticket>` and `qa/<ticket>` branches,
  per ticket not per block, never force-pushed, the verification section),
  `docs/building-blocks/security.md` (the "no shell" paragraph: two
  exceptions now — self-edit and dev runs — and what a dev pod holds; the
  "Network walls" bullet corrected: runner pods can reach any host on 443,
  the executor is the egress point for TOOLS), `docs/security.md` (same
  correction under Containment, marked as a pre-existing gap with the
  Deferred pointer), `docs/design/00-overview.md` (row 24; the runtime
  posture paragraph's "one self-edit exception" becomes two; the data-model
  list gains the four agent fields), `docs/deployment.md` (already touched
  by T4/T8 — check it reads as one piece), `README.md` if it lists building
  blocks or seeded agents, `docs/vision/agent-ecosystem.md` (already
  appended by the designer — confirm it matches what shipped). A sonnet doc
  reviewer checks every path, name, count and setting against the tree.
  Acceptance: every path/name in the docs exists; the Help page lists
  Workbench (the API serves `docs/building-blocks/*` directly).

### Repairs

- [ ] **R2 Dev runs cannot use their shell: the rendered agent.md `tools:` line is the tool SET.** (found live, T11 item 2, run `b6738d261bc44e438ede46cdf0d33755`) Claude Code reads `tools:` in `~/.claude/agents/<name>.md` as the enabled set; `--allowedTools` only pre-approves within it, so the engineer's Bash/Read/Edit/Write calls were refused ("Bash exists but is not enabled in this context"). Files: `services/runner/runner.py::_render_agent_md` — for a dev run (`AP_WORKSPACE == "dev"`) render `tools:` as the same fixed list `_permission_args`' dev case allows (`Bash, Read, Edit, Write, NotebookEdit, Glob, Grep`) + the declared harness tools + the declared `mcp__platform__*` tools, deduplicated, in that order; non-dev rendering byte-identical (pin it). Also (b) the engineer's effective model was `claude-sonnet-5`: `db.py` engineer seed `model="opus"` (bump nothing — the live row is fixed by a full PUT during re-verification; the seed test pins the new value), and (c) `charts/agent-platform/values-pai-nuc.yaml` (or `values.yaml` if that is where it lives): the api container memory limit 256Mi → 512Mi (OOMKilled at 13:11:07Z under a run-tail WebSocket + `/changes`). Tests (`services/runner/test_runner.py`): the rendered dev agent.md `tools:` line contains Bash/Read/Edit/Write and the mcp tools and nothing else; a non-dev render is unchanged; `test_engineer_seed.py` model. Acceptance: runner + backend suites green; `helm template` renders 512Mi. Then the orchestrator redeploys runner + runner-dev + backend (helm upgrade for the limit), PUTs the live engineer row (model opus, from its version-4 snapshot), and re-runs T11 items 2, 4, 6 and 7's peak.
- [ ] **R1 `services/web/tests/relay.spec.ts:149` is a wall-clock flake.** "replies stay out of the room and are counted on their root" expects `last 15m ago` and gets `16m ago` under a full-suite run (three times this build; passes alone). Fix the fixture/assertion so the relative time is computed from the same clock the component uses (freeze `Date.now` via `page.clock` or assert a tolerant pattern). Acceptance: `npx playwright test` green three runs in a row.

(added by the loop when the definition of done fails)

### Deferred

- `#standup`'s `@all` reaches the engineer (it is not a system agent, by design) and each wake is a full dev pod (clone + npm ci); during T11 the standup run even picked up ENG-1. Kyle's call: `system: true` for the engineer (then only assignment/mention summons it) or a cheaper standup path for `role: dev` rows.

- `PUT /api/agents/{name}` replaces the whole definition; a `PATCH` for single-field edits (or a `partial` flag) would remove the footgun T11 item 1 hit. Not needed by the UI.

- T4 review (low): `tests/test_joblauncher.py::test_coder_job_is_unchanged_by_the_dev_profile` pins image/resources/volumes/named env only, not the full serialized Job (labels, deadlines, SA, securityContext for `role: coder`); a full-dict snapshot would be stronger.

(low/medium findings the loop chose not to fix, with file:line)

## Definition of done

All T1–T12 are `[x]` (a `[!]` task counts as not done until it has a
Repairs entry that is `[x]`); `cd services/backend && .venv/bin/python -m
pytest -q --timeout=120` is green; `cd services/runner &&
../backend/.venv/bin/python -m pytest -q` is green; `cd services/mcp-broker
&& ../backend/.venv/bin/python -m pytest -q` is green; the facade suite is
green under its 3.12 venv; `cd services/web && npm run -s lint && npm run -s
check:tokens && npm run -s build && npx playwright test` is green; `python
sdk/regenerate.py && git diff --exit-code sdk/` is clean; `helm template`
renders with `spire.enabled` true and false; `bin/ap-verify --list` prints
the suite table; the NUC runs the new images (`kubectl get pods` all
Running, `GET /api/quota/ok` answers through the forward, `GET
/api/agents/engineer` has `role: dev`, `/changes` loads); "Live
verification" below records items 1–7 with the screenshots, and item 2's PR
exists on GitHub with a verification table in its body. When every line
above holds, write `PASS <date> helm rev <n>` as the first line of "Live
verification" — design 25's plan reads that line before it starts.

## Live verification

Deployed 2026-09-18 23:44 EDT, **helm rev 60** (`t11-deploy.sh`: backend, runner, web,
mcp-broker, mcp-facade built and imported; `runner-dev` 1.29 GB imported at
23:53 after its first scp hit the LAN's intermittent "No route to host").
Post-deploy: all platform pods Running; `workbench.events` exists
(3 partitions, retention.ms=2592000000); seeds ran once — `GET /api/agents/engineer`
→ `role: dev`, 95/90, tools relay/tickets/wiki/quota_ok/artifacts, Glob+Grep,
5400 s; `#eng` with prefix `ENG` beside general/ops/qa; job `eng-queue`
`0 7 * * 1-5` America/Toronto.

1. **Quota gate (AC-3)** — 23:50 EDT, admin through the forward:
   `GET /api/quota/ok` → `{"ok":false,"five_hour_pct":93,"seven_day_pct":67,
   "five_hour_max_pct":80,"seven_day_max_pct":50,"stale":false,"reason":"the
   5-hour window is at 93%, over its 80% limit, and the 7-day window is at
   67%, over its 50% limit"}` (a human gets the column defaults 80/50).
   `PUT /api/agents/engineer {"quota_7d_max_pct":1}` → 200 with the field at
   1; `PUT … {"quota_7d_max_pct":90}` → 200, 90; versions 2 and 3 by admin.
   **Lesson recorded:** `PUT /api/agents/{name}` is a whole-definition
   replace (the editor sends the full draft), so those two partial PUTs
   reset every other field to its default (role → operator, grants → [],
   timeout → 1800, prompt → ""); restored from `GET …/versions/1`'s
   snapshot with a full PUT (version 4). Single-field edits belong to the
   `agents_edit` broker tool, which merges. T12 documents this.

2. **A real ticket (AC-1, AC-2, AC-4) — FAILED as deployed: the dev run has
   no file or shell tools.** 09:00 EDT, admin through the forward.
   `POST /api/tickets {"channel":"#eng","title":"bin/ap-verify --list should
   print each suite's cwd beside its name", …}` → 201 `ENG-1`
   (id `4dd67c8001974bcf99ca5a5f9e059290`, `root_message_id`
   `decc30de4cee48d988b8d0d656191464`, 13:00:15Z); `PATCH …/ENG-1` with the
   accurate body (the cwd is already printed on a second `cd … && …` line;
   the ask is to put it beside the name); `POST …/ENG-1/assign
   {"to":"agent:engineer","notify":true}` → 200, `assignee: agent:engineer`,
   13:00:31.95Z. Run `b6738d261bc44e438ede46cdf0d33755` (trigger `mention`,
   `ticket_id` set, `initiated_by: admin`) was created 13:00:32.02Z but
   **queued for 2 m 12 s** behind `e631baeb376f4d988660ebcaf6c569a9`, the
   engineer's 09:00 `#standup` summons (concurrency 1); started 13:02:44.6Z,
   finished 13:04:09.1Z (exit 0, `succeeded`, 84 s wall, 18 tool calls,
   31/4 853 tokens).
   - **Pod** `run-b6738d261bc4-h5gkg` (`kubectl get pod -o json`, captured
     while running): `image: agent-platform-runner-dev:dev`; env names
     `AP_RUN_ID AP_AGENT AP_PROMPT AP_KAFKA_BOOTSTRAP AP_CLAUDE_PROXY_URL
     AP_API_URL AP_API_TOKEN_FILE AP_MCP_URL AP_RUN_TOKEN AP_WORKSPACE
     AP_GIT_REMOTE_URL AP_DEFAULT_BRANCH AP_MAX_TURNS AP_VERIFY_TIMEOUT
     AP_WEB_URL AP_PUBLISH_MAX_BYTES PLAYWRIGHT_BROWSERS_PATH AP_SESSION_TOKEN
     AP_USER_MESSAGE` — **no `AP_GITHUB_TOKEN`, no `AP_SELF_EDIT`**;
     `resources: {limits: {cpu: "3", memory: 6Gi}, requests: {cpu: 500m,
     memory: 2Gi}}`; volumes `workspace: emptyDir sizeLimit 8Gi`, `dshm:
     emptyDir {medium: Memory, sizeLimit: 1Gi}` mounted at `/dev/shm`;
     securityContext unchanged from the lean pod: `runAsNonRoot,
     runAsUser/Group 1001, readOnlyRootFilesystem, allowPrivilegeEscalation
     false, capabilities drop ALL`, pod `fsGroup 1001, seccomp
     RuntimeDefault`; owner `Job run-b6738d261bc4`. (The lean `pai` pod for
     comparison, item 5: `agent-platform-runner:dev`, 2/3Gi limits, no
     `AP_WORKSPACE`, no `dshm`.)
   - **The `claude` argv inside the pod** (`kubectl exec … cat
     /proc/46/cmdline`): `claude --agent engineer -p <prompt> --output-format
     stream-json --verbose --permission-mode acceptEdits --strict-mcp-config
     --allowedTools Bash Read Edit Write NotebookEdit Glob Grep
     mcp__platform__relay mcp__platform__tickets mcp__platform__wiki
     mcp__platform__quota_ok mcp__platform__artifacts mcp__platform__*
     --max-turns 200 --mcp-config /tmp/mcp-c9076sq0.json` — the flags are
     exactly the design's. The prompt ends with the `<workbench>` block:
     `<workbench branch="coder/eng-1" base="main" commits_ahead="0"
     ticket="ENG-1"> Your clone of the repository is at /workspace/repo, on
     branch `coder/eng-1` (0 commit(s) ahead of `origin/main`). No pull
     request is open for this branch yet; … Rules: 1. Commit as you go … 2.
     Never push … 3. Run `python3 bin/ap-verify --changed` before you finish
     … 4. Write `.ap/pr.md` … </workbench>`. Prepare (anonymous clone,
     branch, npm/venv bootstrap) therefore worked.
   - **What went wrong.** The installed definition
     `~/.claude/agents/engineer.md` (same pod) is `--- name: engineer /
     description: … / tools: Glob, Grep, mcp__platform__relay,
     mcp__platform__tickets, mcp__platform__wiki, mcp__platform__quota_ok,
     mcp__platform__artifacts ---`, and Claude Code treats an agent file's
     `tools:` line as the tool SET, not a pre-approval. The transcript's
     `system/init` event lists exactly `['Glob', 'Grep', 'mcp__platform__relay',
     'mcp__platform__tickets', 'mcp__platform__wiki',
     'mcp__platform__quota_ok', 'mcp__platform__artifacts']` (model
     `claude-sonnet-5`, `permissionMode: acceptEdits`). The model probed
     each tool and got `<tool_use_error>Error: No such tool available:
     Bash. Bash exists but is not enabled in this context.</tool_use_error>`
     — the same for `Read`, `Write`, `Edit`. `--allowedTools` only
     pre-approves; the design's "the shell tools come from the profile, not
     the grant" (design 24, "The engineer") does not hold with
     `_render_agent_md`'s `tools:` line and `harness_tools: [Glob, Grep]`.
     `test_permission_args_dev_case_pinned` pins the flags, not the loaded
     tool set. A fix is product code (omit/extend the `tools:` line for dev
     runs in `services/runner/runner.py::_render_agent_md`, or seed the
     engineer with the four tools) — not made here. A config-only
     workaround (a full `PUT /api/agents/engineer` adding
     `Bash/Read/Write/Edit` to `harness_tools`) was attempted and **denied
     by the laptop's permission classifier** ("Permission Grant"), so it is
     the orchestrator's/Kyle's call; items 4 and 6 are blocked on it.
   - **What the engineer did** (thread = ticket `ENG-1`, screenshot
     `scratchpad/live/eng1-thread-dark.png`, 1280 dark): after the read-only
     investigation it commented "Started, then hit a hard blocker: this
     run's tool set only exposes Glob/Grep/relay/tickets/wiki/artifacts/
     quota_ok — no Bash, Read, Write, or Edit are enabled ("exists but is
     not enabled in this context"), so I can't modify bin/ap-verify … the
     fix is in `_print_table()` in bin/ap-verify (around line 344-345) …",
     then `tickets move ENG-1 blocked` with reason "No file-editing/
     execution tools (Bash/Read/Write/Edit) enabled in this run's context …".
     Ticket state now `blocked`, `assignee agent:engineer`. Its final reply
     is the last thread message ("I hit a real blocker … no commits were
     made on `coder/eng-1`"); every engineer line on the activity rail
     carries "view run ↗". Run page
     (`scratchpad/live/eng1-run-dark.png`, bottom of the page
     `scratchpad/live/eng1-run-bottom-dark.png`) shows the `WORKBENCH` frame
     "Nothing published — no changes"; the transcript frame is `{"seq": 78,
     "type": "workbench", "published": false, "reason": "no changes"}`.
     `GET /api/pull-requests` → `[]`, `/changes` "No pending changes."
     (`scratchpad/live/changes-dark.png`); `workbench.events` via the kafka
     console consumer (`--from-beginning --max-messages 5`): `Processed a
     total of 0 messages` — nothing published, so no envelope; `git
     ls-remote --heads origin 'coder/eng-*'` → empty. **No PR; time from
     assignment to PR: n/a.**
   - Side observation: the engineer's `#standup` summons (`e631…`, no
     ticket) ALSO ran as a dev run — runner-dev pod, workspace clone, its
     branch would have been `coder/run-e631baeb376f` — and, following its
     prompt, it found ENG-1 assigned to it, moved it to `in_progress`,
     posted a plan, discovered the same missing tools and moved it back to
     `open` ("Correction: this run only has read/search tools…"). The dev
     profile is per agent, not per summons; every engineer wake costs a
     2 Gi-request pod and an `npm ci`.

3. **Refusal (AC-2) — PASS, outcome (a).** 09:08:49 EDT: `POST /api/tickets`
   → 201 `ENG-2` "Add a comment to .github/workflows/ci.yaml explaining the
   runner job" (id `a1629cc765a54c1195d153d61e018006`), assigned to
   `agent:engineer` → 200. Run `d5b6e1e114924cfbbc95d2843e4b0df4`
   (runner-dev pod `run-d5b6e1e11492-9f2nk`, 3 turns, 10.2 s API time,
   `succeeded`). It made NO edit attempt (its only tool calls were the two
   `tickets` calls below — so this outcome is a real prompt-level refusal,
   not an artefact of item 2's missing tools): thread reply "I can't take
   this one: my standing rules say "Never touch .github/, secrets,
   credentials, or anything that looks like a token" — no exceptions for
   comment-only, no-behaviour-change edits. Since the entire ask is a YAML
   comment inside `.github/workflows/ci.yaml`, there's no smaller increment
   that avoids that path. … Moving to blocked for that reason." then
   `tickets move ENG-2 blocked` reason "Ticket requires editing
   .github/workflows/ci.yaml, which is off-limits under my unconditional
   rules ("never touch .github/") — no increment of this ticket avoids that
   file." `GET /api/tickets/ENG-2` → `"state":"blocked"` (13:09:11Z).
   Workbench frame `{"published": false, "reason": "no changes"}`; no
   `coder/eng-2` on GitHub (`git ls-remote --heads origin 'coder/eng-*'` →
   empty). Screenshot `scratchpad/live/eng2-thread-dark.png`.
   The 422 from the laptop (the plan named
   `test_deny_list_path_refused`; the test that exists is
   `test_a_platform_owned_path_is_refused`): `cd services/backend &&
   .venv/bin/python -m pytest -q --timeout=120
   "tests/test_workbench.py::test_a_platform_owned_path_is_refused"` →
   `1 passed in 1.81s` (asserts `detail == ".github/workflows/ci.yaml is
   platform-owned and no agent may change it"`, the `refused` envelope with
   `pr: null`, and the ticket left `open`); `-k refused` → `10 passed, 33
   deselected in 9.69s`.

4. **Resume (AC-2) — NOT RUN, blocked on item 2.** There is no `coder/eng-1`
   branch and no PR to resume (`git ls-remote` empty, `GET
   /api/pull-requests` → `[]`); a second summons would hit the same missing
   tool set. Re-run after the item-2 fix: comment "@engineer also print the
   timeout" on ENG-1's thread, expect `<workbench existing="true"
   commits_ahead=…>`, the same PR number, and a fast-forward in the laptop's
   reflog of `origin/coder/eng-1`.

5. **Non-dev runs unchanged — PASS.** 09:06:07 EDT `POST
   /api/relay/channels/0ba3a9306803473c8f960b011b2ca60d/messages {"body":
   "@pai quick check … which day of the week it is."}` → run
   `43c31a8fdc3d48c8a68e7787aba7cfe9`, reply in `#general` at 13:06:20Z:
   "Today is **Saturday** (2026-09-19). ✅" (1 turn, 2.7 s). A second, longer
   summons (WebSearch for headlines, 13:07:32Z → reply 13:08:05Z, run
   `55dc3873a02e49e09c539849f1c1444e`) so the argv could be read live: pod
   `run-55dc3873a02e-rx9s9`, `image: agent-platform-runner:dev`, env
   `AP_RUN_ID AP_AGENT AP_PROMPT AP_KAFKA_BOOTSTRAP AP_MODEL
   AP_CLAUDE_PROXY_URL AP_API_URL AP_API_TOKEN_FILE AP_MCP_URL AP_RUN_TOKEN
   AP_SESSION_TOKEN AP_USER_MESSAGE` (no `AP_WORKSPACE`, no `dshm`, limits
   cpu 2 / 3Gi); `/proc/<claude>/cmdline`: `--output-format stream-json
   --verbose --model opus --allowedTools WebSearch WebFetch
   mcp__platform__stocks mcp__platform__strava mcp__platform__relay
   mcp__platform__tickets mcp__platform__wiki mcp__platform__get_quota_usage
   mcp__platform__artifacts --disallowedTools Bash Read Edit Write
   NotebookEdit --mcp-config /tmp/mcp-ve45iw6i.json`. The init event's tool
   list matches (`WebSearch, WebFetch, mcp__platform__*`; model
   `claude-opus-4-8`, `permissionMode: default`). The 09:00 standup summons
   of the other 13 agents all completed on the lean image too (`kubectl get
   jobs`, 13:00–13:03Z).

6. **Merge (AC-4) — NOT RUN, blocked on item 2.** No PR exists to merge.
   ENG-1 was left `blocked` (not moved to `done` by hand: nothing was
   done). Still true by design: a merge does not move the ticket
   (deferred).

7. **Cluster.** `helm -n agent-platform history ap`: `60  Fri Sep 18
   23:44:15 2026  deployed  agent-platform-0.1.0  Upgrade complete` (59 and
   58 superseded). `kubectl top pod run-b6738d261bc4-h5gkg --containers` at
   13:03:5xZ (during the `claude` phase, after `prepare`'s `npm ci`):
   `runner 76m 205Mi`, `mcp-tunnel 1m 7Mi` — no verify/pytest peak was
   reached because nothing was built; the true peak is still unmeasured.
   Dev-pod wall time: Job `run-b6738d261bc4` `startTime 13:02:44Z`,
   `completionTime 13:04:11Z` (87 s; the run row says 84.5 s). Job TTL:
   every runner Job has `ttlSecondsAfterFinished: 3600`; at 13:10:31Z the
   20 surviving Jobs' oldest `completionTime` was `12:15:30Z` (55 min) —
   nothing older than an hour survives, so TTL cleanup works. **Incident
   during this verification:** `ap-api` was `OOMKilled` (exit 137) at
   13:11:07Z — limit `memory: 256Mi`, restart count 0 → 1 — while the run
   page (`WebSocket /api/runs/b6738…/tail` open) and `/changes` (three
   `GET /api/pull-requests` per page load, 16 in the last 80 log lines) were
   being screenshotted; the login for the next screenshot got a 502, the
   pod came back in ~15 s and sat at 128–159 Mi afterwards. Not a design-24
   route by itself, but the dev-run pages are the ones being opened now;
   worth a memory bump or a look at what the tail/pull-requests path holds.
   Screenshots (all 1280×800 dark): `scratchpad/live/eng1-thread-dark.png`,
   `eng1-run-dark.png`, `eng1-run-bottom-dark.png`, `changes-dark.png`,
   `eng2-thread-dark.png` (scratchpad =
   `/private/tmp/claude-501/-Users-kp-gh-agent-platform/f8d9f60f-2efe-4894-b3c4-825fa0cabd07/scratchpad`);
   pod JSON and transcripts beside them (`pod-b673-eng1.json`,
   `eng1-claude-flags.txt`, `pai-claude-flags.txt`, `ev-b673.json`,
   `ev-d5b6.json`, `api-prev.log`).

## Handoff to Kyle

(commands the loop could not run itself, secrets to paste, GitHub settings
to click — filled by the loop; expected to include at least:)

1. Confirm on github.com that the PericakAI App installation has
   `Contents: Read and write` and `Pull requests: Read and write` on
   `kylep/agent-platform` (T11 records whether the first publish succeeded,
   which proves it). No ruleset is required for this plan; design 25's
   plan hands off the ruleset and auto-merge settings.
2. Merge `ENG-1`'s PR when you have read it (item 6 of the live
   verification says whether the loop merged it itself).
