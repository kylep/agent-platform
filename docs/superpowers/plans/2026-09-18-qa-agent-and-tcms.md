# Plan — The QA agent and the TCMS (design 25)

Design: `docs/design/25-qa-agent-and-tcms.md` (read it first; the sections
named in each task are pasted into that task's implementer prompt). It
builds on design 24 and its plan `2026-09-18-coding-agent.md` — the
Workbench, publish, the path policy, `bin/ap-verify`, `quota_ok`, the
`runner-dev` image — all of which must be live before this plan starts.
Predecessors built the same way: `2026-09-17-artifacts-and-image-studio.md`
(deploy mechanics, evidence tables) and `2026-08-07`-era stockmarket work for
the app + writer-tool split (`apps/stockmarket`, `tools/prices`).

Kyle's ask, verbatim: "a QA agent. It can also use relay, modify tickets,
interact with the wiki, and PR against the repo. It only changes test code,
and it is allowed to push changes to test code directly. It slings unit and
integration tests + writes e2e tests. It also operates a new app, the TCMS
(testcase management system) that defines test cases for everything. It
decides what automated tests are needed according to the code and tcms. It
prunes old or bad tests, measures test runtime and ensures its fast and
focussed, and ensures good coverage across the test pyramid. It also uses
playwright mcp to qa changes, but is aware of my quota and isn't willing to
reach for that when weekly quota is above ... 50%, and 5h is above... 80%...
those are somehow tuneable, too."

Acceptance criteria (each task names the ones it satisfies):

- **AC-1** Case definitions live in `tcms/cases/<suite>.yaml`, validate in
  CI, and `tcms sync_cases` mirrors them into `app_tcms.cases` with the
  checkout's sha; results, coverage and runtimes are written only by `tcms
  record_results` from files the runner produced, handed over as artifacts.
- **AC-2** `/apps/tcms/` shows the pyramid, pass rate, coverage, runtime by
  layer, slowest, flaky, unlinked cases and prune candidates from real
  ingested runs; every recorded run is an `app.tcms.run.recorded` envelope
  and one line in `#qa`.
- **AC-3** The QA publishes only test paths: a publish touching anything
  else is refused server-side with the path named in `#qa`; a test-only
  publish opens a `qa/<key>` PR with auto-merge enabled, which GitHub merges
  once the required checks pass (or waits, with a warning, if the repo does
  not allow it).
- **AC-4** The QA logs into the live UI as the `qa` reader principal with a
  password it never sees, walks every page at two widths in both themes
  with the scripted walk, and opens a live Playwright MCP session only when
  `quota_ok` says so — otherwise it says why in `#qa` and walks instead.
- **AC-5** The nightly job runs on pai: cases synced, suites run, results
  recorded, findings ticketed in `#qa`, a note in the room, a test-only PR
  when there was something to fix. Deployed (helm rev recorded), all suites
  green, live evidence recorded below.

## Loop protocol (read this every tick, follow it exactly)

You are the **orchestrator**. You do not write product code yourself. You
dispatch subagents, verify their evidence, commit, and update this file.

0. **Gate.** Before anything else, open
   `docs/superpowers/plans/2026-09-18-coding-agent.md` and read the first
   line of its "Live verification" section. If it does not start with
   `PASS`, do nothing in this plan: schedule the next wakeup and return.
   This plan never starts on an unshipped Workbench.
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
   && ../backend/.venv/bin/python -m pytest -q`; tool: `cd tools/tcms &&
   ../../services/backend/.venv/bin/python -m pytest -q test_run.py`; app:
   `cd apps/tcms/backend && ../../../services/backend/.venv/bin/python -m
   pytest -q`; app frontend: `npm run build -w tcms-frontend`; web: `cd
   services/web && npm run -s lint && npm run -s check:tokens && npm run -s
   build && npx playwright test --reporter=line`; facade: its 3.12 venv;
   chart: `helm template charts/agent-platform -f
   charts/agent-platform/values-pai-nuc.yaml --set env.AP_SESSION_SECRET=x
   --set env.AP_INTERNAL_SECRET=y` in both spire modes; SDK: `python
   sdk/regenerate.py && git diff --exit-code sdk/` in a python:3.12
   container). Do not trust a claim of green without output in your own
   transcript. Run every repo-relative command from the repo root.
4. Dispatch **one sonnet reviewer** (`model: "sonnet"`) with the task text,
   the design excerpt, and the path of a saved `git diff` of the uncommitted
   changes. Ask for findings ranked by severity, defects only, no style — and
   for every guard, limit, permission check, parser, migration or path built
   from model text, ask it to state the WORST CASE: the credential plane
   (the `qa` password reaching a transcript, a tool result, a PR body, a
   storage-state file the model reads back, a log; an artifact id that is
   another owner's; the app DB secret in a tool's stdout); the prompt-trust
   boundary (a JUnit `message` of 10 MB or containing `@all`, a case title
   with newlines entering a `#qa` line, a Playwright test title used as a
   path or a key); path segments from model text (a `files` entry that is
   not 32 hex, a suite name of `../x`, a case key of `../../etc`); a
   200 MB XML, a zip-bomb-shaped XML (billion laughs — use
   `xml.etree` with no entity expansion or `defusedxml`), a results file
   for a commit that does not exist, two nightlies racing; a publish diff
   that renames `services/backend/agentplatform/relay.py` INTO
   `services/backend/tests/`; a `qa` cookie reaching a write route; a
   `PlaywrightMCP` grant on a non-dev agent; the walk given a base URL off
   the platform. For tasks tagged `[ui]`, dispatch additionally a **sonnet
   visual reviewer** that builds the page, serves it against its mock (the
   app's own fixtures, or `tests/mock-api.ts` for the console), screenshots
   the affected pages at 1280×800 and 390×844 in both themes with a
   throwaway spec it deletes afterwards, READS the PNGs, and reports what a
   picky human would notice. Never use `model: "fable"` or the default model
   for subagents.
5. Findings of severity high or critical go back to the same implementer via
   SendMessage (it keeps its context). Low/medium: fix if cheap, otherwise
   note under "Deferred" with file:line and move on. Loop at most twice per
   task; if a task still fails after two repair rounds, mark it `- [!]` with a
   one-paragraph note and continue to the next task — do not stall the build.
6. **Hold every commit until no implementer is editing the tree**: the
   pre-commit hook stashes and restores unstaged tracked files and would race
   a live editor. Commit on `main` (`git add` each file by name, never `-A`;
   never add files that may hold secrets — `exports.sh`, anything under
   `secrets/*/` other than `secret.yaml`/`verify_*.py`). Message:
   `feat(qa): <task title>` plus a body, and the attribution trailer the
   session was given. Then edit this file: `- [x] **Tn …** (commit
   `<hash>`; <one line of what review changed>)`. Two `[ui]` tasks that
   share App.tsx/app.css/mock-api land as one commit.
7. Schedule the next wakeup with `delaySeconds: 60`, `noop: false`, and the
   sentinel prompt the loop skill prescribes. One task (or one parallel set)
   per tick.
8. Recovery: as in the coding-agent plan's protocol step 8 (quota 429 →
   SendMessage after the reset; stalled agent → resume; Terminal.app drops
   the first character — prefix `true; true; `; rerun a Playwright file alone
   before calling it red; `--timeout=120` under host load; a stalled sonnet
   review runs on opus, noted in the tick).
9. NUC access, deploy mechanics and the classifier rule: as in the
   coding-agent plan's protocol step 9 (Terminal.app `do script`, the `ssh -f
   -N -L 18090:localhost:8090 pai` forward, the `docs/superpowers/plans/reference-deploy-pai.sh` reference (with `KUBECONFIG=$HOME/.kube/pai-nuc.yaml` and `DOCKER_HOST=unix://$HOME/.rd/docker.sock` exported; `helm` runs from the Mac, not on pai)
   script, stored values fetched fresh, rollouts, facade last). **This build
   adds the `app-tcms` image** (built from the REPO ROOT: `-f
   apps/tcms/Dockerfile .` after `npm run build -w tcms-frontend`), rebuilds
   `runner-dev` (T7 changes `runner.py`; the image copies it — rebuild is
   minutes, not the first build's hour: the dependency layers cache) and the
   `tool-executor` image ONLY if `tools/tcms/requirements.txt` adds a
   dependency (it should not — `psycopg` is already baked for `prices`),
   and enables `tcms` in `charts/agent-platform/values-pai-nuc.yaml`
   `apps.enabled`. Deploy order for a new app: push `main` → wait for a
   sync tick → confirm the dispatcher provisioned `app-tcms-db` and
   `app-tcms-key` (`kubectl get secret`) → helm upgrade with the app
   enabled. Push `main` before the deploy and after T12.
10. **Hands Kyle may be asked for mid-build** (PushNotification; keep working
    meanwhile): the GitHub ruleset and the "Allow auto-merge" repository
    setting (T9 writes the exact settings into "Handoff to Kyle" the moment
    it is committed and sends the notification; T11's auto-merge evidence
    depends on them, and if they are not set by then, T11 records "auto-merge
    not enabled on the repo — the card warned, the PR waits" and that still
    passes). Nothing else needs his hands: the `qa` password is minted by the
    platform.

### Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: `/Users/kp/gh/agent-platform`. Backend is FastAPI + SQLAlchemy async
  in `services/backend/agentplatform` (tests in `services/backend/tests`,
  `cd services/backend && .venv/bin/python -m pytest -q --timeout=120`). Web
  is React 19 + Vite + `@ap/ui` (`packages/ui`) in `services/web` (`npm run
  -s lint`, `npm run -s check:tokens`, `npm run -s build`, `npx playwright
  test`). The MCP broker is `services/mcp-broker/broker.py` (tests `cd
  services/mcp-broker && ../backend/.venv/bin/python -m pytest -q`; it stubs
  fastmcp and fakes the API with the `wiki` tests' double). The runner is
  `services/runner/runner.py` + `workbench.py` (tests `cd services/runner &&
  ../backend/.venv/bin/python -m pytest -q`; local bare git remotes only).
  Apps live in `apps/<name>/` and may depend ONLY on public contracts (HTTP
  API + SDK, Kafka topics, `@ap/ui`) — never import `agentplatform`; copy
  `apps/running` (the newest) for layout, `apps/stockmarket` + `tools/prices`
  for the app-owns-DDL / tool-writes-rows split. Tools live in
  `tools/<name>/` (`tool.yaml`, `run.py`, `test_run.py`, optional
  `requirements.txt`; stdin JSON → stdout JSON; non-zero exit + one stderr
  line on failure; `tools/prices/run.py` shows the `APP_DB_*` connection and
  `tools/memory/tool.yaml` the `action` enum). Prod Python is 3.12 (dev venv
  is 3.14): no 3.13+/3.14-only syntax.
- TDD: write the failing test first, show it fail, make it pass, keep the
  suite green. Backend tests use the `admin_client` / `sf` / `token_client`
  fixtures in `tests/conftest.py` and sqlite; app tests use their own
  sqlite `pytest.ini` like `apps/news/backend`; tool tests monkeypatch
  `psycopg.connect` with a recorder (never a real database) and feed
  `run.py` JSON on stdin.
- Migrations are additive and live in `db.py` (`_ensure_columns`,
  mark-gated `_ensure_*` seeds called from `init_db`; a row that already
  exists is ADOPTED). An app owns its own schema (`app_<name>`, DDL at boot
  in its `db.py`, `create_all`); a tool that writes an app's rows creates
  NOTHING and fails clearly when the tables are missing (`prices` explains
  why in its docstring).
- Files into a tool travel as ARTIFACTS: the pod uploads with
  `bin/ap-upload`, the tool call names ids in `files`, the broker fetches
  each with the caller's token and passes `files_in` (design 23's sink);
  never base64 through the model, never a path from model text. The
  executor's caps (≤ 4 files, ≤ 8 MiB each) are the limits.
- Relay is the substrate: system rows through `relay_store.post_relay_message`
  + `publish_relay_message`, `kind="event"|"system"`, `mentions=[]`, every
  untrusted string flattened through `tickets.one_line`. An app posts to a
  room the way `apps/running` posts its recap (with its `app:<name>` key
  through the platform API) — find that code and copy it.
- Kafka: platform topics are constants in `agentplatform/events.py` AND
  `charts/agent-platform/values.yaml` `topics.specs`; APP topics are
  declared in `apps/<name>/app.yaml` `needs.kafka_topics` (namespaced
  `app.<name>.*`) and provisioned by the AppProvisioner — never both. Use
  `make_envelope` / `Producer` / `FakeProducer`; tests never open a real
  connection.
- Roles and grants: the `session` role reaches only run-scoped routes; the
  publish policy is `agentplatform/testpaths.py` and `workbench.py` (design
  24) — extend by grants, never by a QA special case in the code; a harness
  tool name lives in `agentspec.CLAUDE_TOOLS` with a `TOOL_HELP` entry; the
  runner's `_SENSITIVE_TOOLS` denial for non-dev runs must not change
  (`test_permission_args_*` pin it — add cases, never edit them).
- Parsers of files the agent produced are parsers of untrusted input: cap
  sizes, cap message lengths, `xml.etree.ElementTree` with
  `forbid_dtd`-equivalent safety (reject a document containing `<!DOCTYPE`
  or `<!ENTITY` before parsing), and never let a field from a file choose a
  table, a path or a Relay mention.
- Chart: a new env the backend reads goes in `templates/_helpers.tpl`
  `backendEnv`; a new app is enabled in `values-pai-nuc.yaml` `apps.enabled`
  by the deploy task, not by an implementer.
- Follow the file's existing comment style: comments explain *why*, not what;
  no narration, no TODOs without an owner.
- Web pages (console): routes in `services/web/src/App.tsx`, nav in
  `packages/ui/src/sidenav.tsx`, fixtures in `services/web/tests/mock-api.ts`,
  smoke + a11y lists. App frontends: an npm workspace `<name>-frontend`
  importing `@ap/ui` (copy `apps/running/frontend`), Vite `base
  /apps/<name>/`, theme from `localStorage.theme`, built on the host (`npm
  run build -w <name>-frontend`) — the app Dockerfile copies `frontend/dist`.
  No raw hex colours. Mobile (<560px) must not break the shell.
- After any platform route or schema change regenerate the SDK (`python
  sdk/regenerate.py` inside `python:3.12-slim`) and re-pin the facade's tier
  counts. Never widen scope, never commit, never push; report the exact
  test commands you ran with their final summary lines and the files you
  touched.

## Tasks

### Phase 0 — the principal, the cases, the parsers (T1 ∥ T2 ∥ T4; T3 after T2 reports)

- [x] **T1 The `qa` principal, `principal` on login, the `qa-web-login` secret block.** `[parallel with T2 and T4]` (AC-4) (commit `df85fae`; review: the stored secret is the source of truth (rotation loud), principal bounded, timing-safe miss)
  Design sections: "Trust boundaries and guards" (the browser is a reader),
  "API" (`POST /api/login`).
  Files: `api/auth.py` (`Creds` gains `principal: str = "admin"`; `login`
  looks up `Principal.name == creds.principal` and sets the cookie for that
  name; `_admin()` stays for `/api/setup`; a principal with
  `password_hash` null → 401), new `agentplatform/qaprincipal.py`
  (`ensure_qa_principal(session_factory, secret_store)`: mark
  `qa-principal-v1` in `schema_marks`; when no `qa` Principal exists, mint
  `secrets.token_urlsafe(24)`, insert `Principal(name="qa", role="reader",
  password_hash=ph.hash(pw))`, write `{QA_WEB_USER: "qa", QA_WEB_PASSWORD:
  pw}` into the k8s secret `qa-web-login` through `secret_store.set` (the
  secrets page's write path — find its name), then the mark; an existing
  `qa` row is adopted and the secret left alone; the password never appears
  in a log), `api_main.py` (called once after `init_db`, under the same
  advisory-lock discipline — it runs only in the API, which is one replica),
  new `secrets/qa-web-login/secret.yaml` (keys `QA_WEB_USER`,
  `QA_WEB_PASSWORD`, `required: false`, hint "minted by the platform at
  boot; delete the value and clear the `qa-principal-v1` mark to rotate",
  probe: none — a script verify `verify_qa_login.py` that POSTs
  `/api/login` with the block's keys against `AP_API_URL` and expects 200,
  in the `strava` script shape), `services/web/src/pages/Login.tsx` is T10's.
  Tests (`tests/test_auth.py` or beside the existing login tests): login as
  `qa` with the right password sets a cookie whose `whoami` is `qa`/`reader`;
  the `qa` cookie is 403 on `PUT /api/agents/x` and 200 on `GET /api/agents`;
  `principal: "nobody"` → 401; admin login unchanged with no `principal`;
  `ensure_qa_principal` with a fake secret store writes the two keys once,
  is idempotent, adopts an existing row, and the returned/logged text never
  contains the password; the secret block loads in `SecretRegistry`; the
  verify script maps 200 → valid, 401 → invalid. Acceptance: backend suite
  green; SDK regenerated (the login body changed); facade counts unchanged.

- [x] **T2 `tcms/cases/` — the schema, the validator, the first suites.** `[parallel with T1 and T4]` (AC-1) (commit `27ce105`; 98 cases / 291 refs verified; review: leading-`/` and empty/dot segments refused, control chars out of titles)
  Design sections: "Data model" (Git), "Naming".
  Files: new `tools/tcms/cases.py` (stdlib + `yaml` — PyYAML is in the
  executor image via the backend? It is NOT a tool requirement today: add
  `pyyaml` to `tools/tcms/requirements.txt`; the backend already has it):
  `load_suites(cases_dir) -> list[Suite]` with the design's schema —
  `suite` equals the filename stem, `key` matches
  `^[a-z0-9-]+\.[a-z0-9-]+$` and starts with `<suite>.`, unique across all
  files, `layer` in the four, `priority` p0–p3, `automation` entries match
  `^(pytest|playwright):[A-Za-z0-9_./-]+::[^\n]{1,200}$` with no `..`
  segment, lists capped (≤ 20 steps, ≤ 10 refs, ≤ 10 tags), strings capped
  (title ≤ 160, expected ≤ 2000); errors are `(file, key|None, message)`
  triples and a file with an error is skipped whole, the others still
  load; new `tcms/README.md` (the format, how a case links to a test, how
  the nightly uses it — short); new `tcms/cases/{relay,tickets,wiki,quota,
  artifacts,workbench,apps,tools}.yaml` written by reading the existing
  tests: at least 40 cases total, every one linked to a real node id or
  Playwright title that exists in the tree (run the pytest collection
  `--collect-only -q` per suite and the Playwright `--list` to copy them
  exactly), spread across the layers, priorities honest (loop guards p0,
  cosmetics p3), plus at least 3 `manual` cases (things only a human or a
  browser session checks); new `services/backend/tests/test_tcms_cases.py`
  (loads the REAL `tcms/cases` dir through `tools/tcms/cases.py` by path —
  zero errors; every `pytest:` ref's file exists and its function name is
  found in that file by a regex; every `playwright:` ref's spec file exists
  and its title string appears in it; a fixture dir with a duplicate key, a
  bad layer, a `..` ref, a wrong-prefix key and an over-long title yields
  the five expected errors and still loads the good file). `.github/
  workflows/ci.yaml`: nothing new — the backend job runs the test.
  Acceptance: backend suite green; `tools/tcms/cases.py` has no import from
  `agentplatform`.

- [x] **T3 `tools/tcms/` — the ingest parsers, the tool, its tests.** `[after T2 reports]` (AC-1) (commit `c033c2d`; review: suite-depth cap, seconds/exit clamps, glob-escaped root probe, idempotent on run_id (ON CONFLICT), error triage; SQL proven on a throwaway Postgres)
  Design sections: "The `tcms` tool", "Data model" (Database), "Trust
  boundaries and guards" (results are machine-ingested).
  Files: new `tools/tcms/ingest.py` (pure: `parse_junit(bytes) ->
  list[Result]`, `parse_playwright_json(bytes) -> list[Result]`,
  `parse_cobertura(bytes) -> list[CoverageRow]`, `sniff(name, bytes) ->
  "junit"|"playwright"|"cobertura"|None`; refs built exactly as the design
  says; `<!DOCTYPE`/`<!ENTITY` → refused; input ≤ 8 MiB; `message` ≤ 4
  KiB; a JUnit `testcase` with no `classname` → skipped and counted;
  `layer_of(ref)` by path prefix), new `tools/tcms/run.py` (the actions
  table from the design; `connect()` as `prices` does with `search_path=
  app_tcms`; `sync_cases` reads `Path(__file__).resolve().parents[2] /
  "tcms" / "cases"` through `cases.py` and upserts, marks absent keys
  `retired`, records `source_sha` from `git rev-parse HEAD` in the checkout
  when `.git` exists else `"unknown"`; `record_results` reads
  `TOOL_IN_DIR/*`, sniffs each, refuses an unknown file by name, inserts
  ONE `test_runs` row (`run_id` from `TOOL_RUN_ID`, `agent` from
  `TOOL_CALLER_AGENT` — never from args), its `results` (case_key matched
  from `cases.automation` exactly), its `coverage_snapshots`; the reads
  answer in ≤ 4 KiB of lines with "showing N of M"; every SQL statement
  parameterized; a missing table → the `prices`-style message), `tool.yaml`
  (`infra.secrets: [app-tcms-db]`, `timeout_seconds: 120`, `params` with the
  `action` enum, `files` as `array of string` described as "artifact ids
  from bin/ap-upload — record_results only", the description written as
  usage guidance saying counts are never accepted), `requirements.txt`
  (`pyyaml`, `psycopg[binary]` — check what `prices` pins and match it),
  `test_run.py` (parsers against fixture files under `tools/tcms/fixtures/`
  — a real pytest `--junitxml` output of a small run you generate, a real
  Playwright JSON from `npx playwright test --reporter=json` of one spec,
  a real `coverage.xml`; the billion-laughs document → refused; a 9 MiB
  file → refused; `record_results` with a recording fake connection —
  assert the tables and column sets against `apps/tcms/backend/tcmsapp/
  schema.py`'s `COLUMNS` dict — THIS task creates that file (and an empty
  `tcmsapp/__init__.py`): `COLUMNS` (the four tables' column lists from the
  design) plus every SQL statement both sides use as `%s`-parameterized
  strings (`INSERT_RUN`, `INSERT_RESULT`, `INSERT_COVERAGE`, `UPSERT_CASE`,
  `RETIRE_CASES`, and the read queries `FLAKY`, `SLOWEST`,
  `PRUNE_CANDIDATES`, `COVERAGE_GAPS`, `RUNTIME_BY_LAYER`); `run.py` loads it
  at runtime by path — `Path(__file__).resolve().parents[2] / "apps" /
  "tcms" / "backend" / "tcmsapp" / "schema.py"` via `importlib` (both
  files live in the synced checkout the executor mounts at `/agents`) — so
  there is exactly one home and T5's app imports it as a package module;
  one `test_runs` insert, N `results`, the `case_key`
  match, the `agent` from env not args; `sync_cases` against a temp cases
  dir; every read action's line cap). Acceptance: `cd tools/tcms &&
  ../../services/backend/.venv/bin/python -m pytest -q test_run.py` green;
  `ToolRegistry` loads the manifest (the backend registry test that scans
  `tools/`); the CI `tools` job loop picks it up unchanged.

- [x] **T4 Broker: `files` from artifacts for custom tools; `bin/ap-upload`.** `[parallel with T1 and T2]` (AC-1) (commit `2b06fa4`; review: broker↔executor sanitiser cross-check (caught NUL/over-long), `files` reserved in manifests and advertised on every custom tool)
  Design sections: "Broker" (the `files` argument), "Trust boundaries and
  guards" (results are machine-ingested — the artifact path).
  Files: `services/mcp-broker/broker.py` (`CustomTool.run`: when `args`
  carries `files`, it must be a list of ≤ 4 strings each matching
  `^[0-9a-f]{32}$` (else an error string naming the rule); for each, `GET
  /api/artifacts/{id}` (metadata: name, mime, size ≤ 8 MiB else error) and
  `GET /api/artifacts/{id}/content` with the caller's forwarded token;
  build `files_in: [{name: <metadata name>, mime, b64}]`, drop `files`
  from the args sent to the executor, and record the byte total in the
  audit row — never bytes; an artifact the caller cannot read → the API's
  404 becomes "artifact <id> not found or not readable"), new
  `bin/ap-upload` (stdlib; reads the pod's identity exactly as
  `runner._identity_token` does — `AP_API_TOKEN` or `AP_API_TOKEN_FILE` —
  and `AP_API_URL`; `POST /api/artifacts` multipart with the file, `name`
  = basename, `tags: ["tcms"]`; prints the artifact id, one per file
  argument; exit 1 with the API's message on failure; never prints the
  token), `docs/building-blocks/tools.md` (a paragraph: "Files from
  artifacts" — the `files` argument, for any tool). Tests (broker suite
  with the fake API): a tool call with two ids fetches both and forwards
  `files_in` with the right names; a bad id → error, no executor call; a
  5th id → error; a 404 → the message; the audit row carries `files_bytes`;
  `bin/ap-upload` tested by loading it as a module with a monkeypatched
  `urllib`: builds the multipart body, uses the file token, prints the id.
  Acceptance: broker suite green; `bin/ap-upload --help` prints usage.

### Phase 1 — the app, the runner, the walk (T5 after T3 reports; T7 ∥ T8 with T5; T6 after T5 reports)

- [x] **T5 `apps/tcms` backend: manifest, schema, API, reconciler, Dockerfile.** `[after T3 reports; parallel with T7 and T8]` (AC-2) (commit `040a786`; app keys could not post to Relay → new `POST /api/relay/notify` (channels only, 60/h, system row as `app:tcms`); LIKE literals; area validated; 22 tests also green on Postgres 16)
  Design sections: "The app", "Data model" (Database), "Kafka", "Relay".
  Files: new `apps/tcms/app.yaml` (per the design; `icon: 🧪`), `apps/tcms/
  Dockerfile` (the `apps/running/Dockerfile` shape, built from the repo
  root), `apps/tcms/backend/{requirements.txt, pytest.ini,
  test_tcmsapp.py}`, `apps/tcms/backend/tcmsapp/{__init__,main,db,schema,
  api,reconcile}.py` — `schema.py` already exists from T3 (the ONE home of
  `COLUMNS` and the shared SQL); extend it, never duplicate it, and keep
  `tools/tcms/test_run.py` green against it; `db.py` builds its DDL from
  `COLUMNS` (`create_all` at boot in `app_tcms`, sqlite in
  tests via the `APP_DB_URL` env the running app uses); `api.py` (the
  routes from the design under `/apps/tcms/api/`, all GET, scalar query
  params, `X-AP-User` required as `newsapp/api.py` does; `overview`
  computes the pyramid from the latest run's `results.layer`, pass rate
  over the last 30 runs, coverage total from the latest snapshot, the four
  attention counts; `flaky` and `prune-candidates` implement the tool's
  definitions with the SAME statements from `schema.py` the tool's read
  actions use, so the UI and the tool agree by construction — the sqlite
  test suite needs `%s` → `?` translated once in the app's `db.py`, or
  the statements written with `:name` params both drivers accept; pick one
  and say which);
  `reconcile.py` (every 30 s: `test_runs` with `published_at IS NULL` →
  publish `app.tcms.run.recorded` (`make_envelope`-shaped, the apps use the
  same envelope; copy `runningapp`'s producer) → post the one-line note
  into `#qa` with the app key exactly as the running app posts its recap
  → stamp `published_at`; retention: delete `test_runs` (cascading) older
  than `TCMS_RETENTION_DAYS` (90) once a day); `main.py` (lifespan: DDL,
  reconciler task, static mount at `/apps/tcms`). Tests
  (`test_tcmsapp.py`, sqlite): seed rows through `schema.COLUMNS` and
  assert `overview`, `runs/{id}` groups failures first, `cases` filters
  (`unlinked` = a ref no result ever matched), `flaky` (fail then pass on
  the same commit), `slowest`, `prune-candidates` (never failed in N runs
  AND slowest decile; retired case), `coverage`; the reconciler publishes
  one envelope per unpublished run and stamps it (fake producer + fake API
  post recorder), and posts nothing twice; a request without `X-AP-User` →
  401; retention deletes only old runs. Acceptance: `cd apps/tcms/backend
  && ../../../services/backend/.venv/bin/python -m pytest -q` green;
  `.github/workflows/ci.yaml` `apps` job gains the tcms step (copy the news
  step); `tools/tcms/test_run.py` still green against the shared columns.

- [x] **T6 `[ui]` `apps/tcms/frontend` — Overview, Runs, Cases, Health.** `[after T5 reports]` (AC-2) (commit `409a649`; visual review ship: 24 shots both themes/widths, pyramid reads as one, no overflow at 390; `overflow-wrap` polish applied)
  Design sections: "The app" (Frontend).
  Files: new `apps/tcms/frontend/{package.json (name tcms-frontend),
  index.html, vite.config.ts (base /apps/tcms/), tsconfig.json,
  src/{main.tsx, App.tsx, api.ts, app.css, components.tsx}}` copied from
  `apps/running/frontend` and reshaped: four routes (Overview `/`, Runs
  `/runs` + `/runs/:id`, Cases `/cases` + `/cases/:key`, Health
  `/health`) in the app shell `@ap/ui` provides; the pyramid as three
  stacked horizontal bars (`--ds-chart-1..3`) labelled `e2e · 214 · 3 m 40
  s`, the pass-rate sparkline as an inline SVG polyline, tiles from
  `@ap/ui` for the four attention counts each linking to its list; the
  Runs table (commit short sha, agent face by name, duration, totals as
  chips, verify ✓/✗); the run page grouped by suite, failures first, the
  message in a `<pre>` capped by CSS; the Cases table with the filter
  controls in the URL, the case page with steps as an ordered list,
  expected, refs with their last 10 results as coloured dots, and a link to
  `https://github.com/kylep/agent-platform/blob/main/tcms/cases/<suite>.yaml`;
  Health: slowest 15 as bars, the flaky list, prune candidates with the
  reason column. Root `package.json` workspaces gains `apps/tcms/frontend`;
  `.github/workflows/ci.yaml` `web` job builds it (`npm run build -w
  tcms-frontend`) and `check:tokens` scans it as it scans the others
  (confirm the script's glob covers `apps/*/frontend`). A `tests/` dir is
  NOT required for app frontends (none of the others has one); the visual
  reviewer is the gate. Acceptance: `npm run build -w tcms-frontend` green;
  `check:tokens` green; the visual reviewer serves `dist/` against a
  fixture JSON (a tiny static server it writes and deletes) and screenshots
  all four pages at 1280 and 390, both themes; the pyramid reads as a
  pyramid.

- [x] **T7 Runner: `PlaywrightMCP`, the second MCP server, `bin/ap-web-login`, `ap-verify --all` junit merge.** `[parallel with T5 and T8]` (AC-4) (commit `12044e8`; review: the image's `playwright-mcp` bin not `npx`; `--allowed-origins` is advisory per the package's README → Chromium `--host-resolver-rules` via `--config` is the real boundary (design corrected); stderr fallback in the login frame)
  Design sections: "Trust boundaries and guards" (the browser is a reader;
  the MCP server is locked to the platform), "Broker" (`PlaywrightMCP`).
  Files: `agentspec.py` (`"PlaywrightMCP"` in `CLAUDE_TOOLS`; `TOOL_HELP`
  entry `{"name": "PlaywrightMCP", "kind": "claude", "dev_only": True,
  "description": …}` per the design; `HARNESS_TOOLS` in `agentdefs.py`
  follows), `runner.py` (`_permission_args` dev case: when the declared
  harness tools contain `PlaywrightMCP`, append `mcp__playwright__*` to
  `--allowedTools` — and for the non-dev cases filter `PlaywrightMCP` out
  exactly as the sensitive set is filtered, pinned by a new test;
  `_write_mcp_config` gains, for a dev run with the grant, a second server
  `playwright` of type stdio: `command: "npx"`, `args: ["@playwright/mcp@
  <the pin from Dockerfile.dev>", "--headless", "--isolated",
  "--no-sandbox", "--executable-path", "/opt/chromium/chrome",
  "--storage-state", "/workspace/qa/state.json", "--allowed-origins",
  $AP_WEB_URL, "--image-responses", "allow", "--output-dir",
  "/workspace/qa/mcp", "--viewport-size", "1280x800"]` — the storage-state
  path is created by `bin/ap-web-login` in `prepare` when `QA_WEB_PASSWORD`
  is in the env, and the server entry is written only when that file exists
  (else a transcript frame says "no web login — browser tools off")),
  `workbench.py` (`prepare` runs `bin/ap-web-login --out
  /workspace/qa/state.json` when `QA_WEB_USER`/`QA_WEB_PASSWORD` are set;
  its stdout is not echoed), new `bin/ap-web-login` (stdlib; `POST
  $AP_WEB_URL/api/login {principal, password}`; extracts `ap_session` from
  `Set-Cookie`; writes a Playwright storage-state JSON `{"cookies":
  [{name, value, domain: <host>, path: "/", httpOnly: true, secure:
  false, sameSite: "Lax"}], "origins": []}` with mode 0600; prints only
  `ok` or the status; never the cookie or the password), `bin/ap-verify`
  (`--all` also writes `<out>/junit-all.xml`: every per-suite JUnit file's
  `<testsuite>` elements concatenated under one `<testsuites>`, so the
  upload is three files; `--list` unchanged). Tests: runner —
  `_permission_args` with and without the grant, dev and non-dev
  (non-dev strips it); the MCP config JSON has both servers only when the
  state file exists, the playwright args are exactly the list above with
  `AP_WEB_URL` substituted and nothing from the agent definition in them;
  `bin/ap-web-login` with a monkeypatched `urllib`: parses the cookie,
  writes 0600, prints `ok`, a 401 → exit 1 with `401`; the agentspec
  TOOL_HELP lockstep test; `bin/ap-verify` merge test with two fixture
  junit files. Acceptance: runner + backend suites green.

- [x] **T8 The scripted walk: `services/web/scripts/walk.mjs` and `tests/pages.ts`.** `[parallel with T5 and T7]` (AC-4) (commit `b3bd0ad`; review: route paths guarded against absolute/protocol-relative/file entries (host guard bypass), temp dirs cleaned; the two original spec lists disagreed on order, titles set-identical)
  Design sections: "The decision in one paragraph" (the two tiers), "The
  QA (seeded row)" (the walk paragraph).
  Files: new `services/web/tests/pages.ts` (export the route list that
  `tests/smoke.spec.ts` `PAGES` and `tests/a11y.spec.ts` hold today —
  `path`, `heading`, `probe`, `mobile: boolean` — and rewrite both specs to
  import it, byte-identical behaviour), new `services/web/scripts/walk.mjs`
  (ESM, `import { chromium } from "@playwright/test"`; args `--base
  <url>` (required; refused unless its host equals `AP_WEB_URL`'s host or
  `--allow-any-host` is passed — the pod never passes it), `--state
  <storage-state.json>`, `--out <dir>`, `--routes <file>` (default the
  compiled list — since `pages.ts` is TypeScript, `walk.mjs` reads
  `tests/pages.ts` with a small regex extractor, or the implementer emits
  `tests/pages.json` from `pages.ts` via a `prewalk` npm script — choose
  and say which); for each route × `{1280x800, 390x844}` × `{dark,
  light}` (theme set with `addInitScript` writing `localStorage.theme`
  before load): navigate, wait for `networkidle` or 10 s, screenshot
  full-page PNG to `<out>/<route-slug>-<w>-<theme>.png`, record console
  errors, responses ≥ 400 (excluding the mock's deliberate 404s — in the
  pod there is no mock; record all), `document.documentElement.scrollWidth
  > innerWidth`, and whether the route's `heading` was found; write
  `<out>/index.json` `[{route, width, theme, file, ok, heading_found,
  console_errors: [...], failed_requests: [...], overflow}]` and print a
  one-line summary; exit 0 always (the index is the result)), `package.json`
  (`"walk": "node scripts/walk.mjs"`). Tests: `tests/walk.spec.ts` runs the
  walk against the Playwright mock (`vite preview` + the mock route
  handler is per-page — so the spec drives `walk.mjs`'s functions directly
  with a `page` it created: export the per-route function from the module
  and test it on two routes, one deliberately overflowing via an injected
  style), asserting the index entries and the PNG files; the host guard
  refuses `https://example.com`. Acceptance: web suite green (smoke and
  a11y unchanged in count); `npm run walk -- --base http://localhost:4173
  --out /tmp/walk --allow-any-host` against a running `vite preview` writes
  an index with 4 entries per route (paste the summary).

### Phase 2 — the agent (T9 after T3, T4 and T7 are committed; T10 with T6)

- [x] **T9 Seeds: `#qa`, the QA row, `qa-nightly`; the Handoff notification.** `[after T3, T4 and T7 are committed]` (AC-3, AC-4, AC-5) (commit `247fe90`; review: ship — prompt, grants and adoption match the design; readiness blocks only mid-rotation (the API mints the secret at boot) — pinned, not special-cased)
  Design sections: "The QA (seeded row)", "Jobs", "Trust boundaries and
  guards" (the publish policy is the fence).
  Files: `db.py` (`QA_CHANNEL_MARK = "qa-channel-v1"` — the `#qa` open
  channel with prefix `QA`, ADOPTED if a `#qa` exists (set the prefix only
  when null; if a different channel already holds `QA`, leave both and log
  it), welcome row "QA findings land here as QA-n tickets; the nightly note
  says what ran"; `QA_SEED_MARK = "qa-seed-v1"` — the row exactly as the
  design specifies: `role="dev"`, `system=True`, `model="sonnet"`,
  `timeout_seconds=7200`, `quota_5h_max_pct=80`, `quota_7d_max_pct=50`,
  `platform_tools=[TOOL_RELAY, TOOL_TICKETS, TOOL_WIKI, TOOL_QUOTA_OK,
  TOOL_ARTIFACTS, "mcp__platform__tcms"]` — NOT `query_app` (wide rung)
  (check how a custom tool's grant string is spelled for `stocks` and copy
  it), `harness_tools=["Glob", "Grep", "PlaywrightMCP"]`,
  `secrets=["qa-web-login"]`, `push_path_globs=list(TEST_PATH_GLOBS)`
  (imported from `testpaths.py` — one definition), `may_delete_tests=True`,
  `QA_PROMPT` written IN FULL from the design's paragraph (nightly, walk,
  session rule, test rules, hand-back), `changed_via="seed"`;
  `QA_NIGHTLY_MARK = "qa-nightly-job-v1"` — `relay_channel="qa"`, `cron="0
  2 * * *"`, `timezone="America/Toronto"`, the design's prompt; all called
  from `init_db` after the engineer seeds), `docs/building-blocks/
  agents.md` (seeded list gains the QA). Tests: idempotent seeds; the row's
  grants, role, thresholds, globs equal `TEST_PATH_GLOBS`; the prompt
  contains "not spending the browser" and "never weaken"; `@qa` in `#qa`
  summons it (system agents are summonable by name); `#standup`'s `@all`
  skips it; the readiness gate BLOCKS it while `qa-web-login` is unset and
  passes once the secret exists (the secret is `required: false`, so
  confirm which way the gate falls and pin it — the design wants the QA to
  run without the browser rather than not at all: if the gate blocks on an
  unset optional secret, that is a bug to fix here). Then, immediately after
  this task's commit, the orchestrator writes the GitHub settings into
  "Handoff to Kyle" (the text is drafted there already — fill in the exact
  `TEST_PATH_GLOBS` list from the code) and sends the PushNotification.
  Acceptance: backend suite green.

- [x] **T10 `[ui]` Login `principal` field; `PlaywrightMCP` in the harness picker; the app card.** `[parallel with T6]` (AC-4) (commit `7659049`; code + visual review ship; `ToolHelp.dev_only` added so the help API carries it)
  Design sections: "API" (`POST /api/login`), "Broker" (`PlaywrightMCP`).
  Files: `services/web/src/pages/Login.tsx` (an `Input` "Username"
  defaulting to `admin` above the password; submits `{principal,
  password}`; autofocus stays on the password when the username is
  prefilled), `components/CapabilityPickers.tsx` or wherever harness tools
  are ticked (`PlaywrightMCP` shows with its help text and a "dev runs
  only" note from `dev_only`; ticking it on a non-dev agent shows the note
  as a warning, not an error — the runner ignores it), `src/api.ts`
  (`login(principal, password)`), `tests/mock-api.ts` (`POST /api/login`
  reads `principal`; `/api/tools/help` or wherever TOOL_HELP is served
  gains the entry), `tests/webhook-auth.spec.ts`/`smoke.spec.ts` if the
  login form is asserted, new assertions in `tests/agent-editor.spec.ts`
  (the picker shows `PlaywrightMCP` with the note). The `/apps` registry
  card for `tcms` needs no code (it reads `app.yaml`); confirm the mock's
  `/api/apps` fixture gains a `tcms` entry so the nav shows it. Acceptance:
  web suite green; the visual reviewer's screenshots of `/login` at 1280
  and 390, both themes.

### Phase 3 — ship it (T11 → T12)

- [ ] **T11 Deploy and live verification.** `[after T9 and T10 are committed; T6 committed]` (AC-5, and the proof of AC-1…AC-4)
  The orchestrator does this task itself with protocol step 9's mechanics,
  dispatching an **opus verify agent** for the through-the-forward steps.
  First `git push origin main` and wait for a sync tick (the checkout must
  carry `tcms/cases`, `tools/tcms`, `secrets/qa-web-login`, `bin/*`). Confirm
  the dispatcher provisioned `app-tcms-db` and `app-tcms-key` and that
  `GET /api/tools` lists `tcms`. Build and import backend, runner (lean),
  runner-dev, mcp-broker, mcp-facade, web, **app-tcms** (frontend built
  first); add `tcms` to `values-pai-nuc.yaml` `apps.enabled`; helm upgrade;
  rollouts; facade last. Confirm: pods Running incl. `ap-app-tcms`; the
  API log shows `qa-principal-v1` ran once and `kubectl get secret
  qa-web-login` has two keys; `GET /api/secrets` lists `qa-web-login` as
  `valid` after the verifier tick; the seeds ran (`#qa` prefix `QA`, the
  `qa` row, the job); `GET /api/agents/qa` is not blocked. Then record in
  "Live verification" below:
  1. **Reader login (AC-4).** `POST /api/login {principal: "qa",
     password: <from the k8s secret, read via kubectl on pai — never pasted
     into the plan>}` → 200; `GET /api/whoami` → `qa`/`reader`; `PUT
     /api/agents/qa` with that cookie → 403. Quote statuses only.
  2. **Cases and ingest (AC-1).** In `#qa`: `@qa sync the cases and tell
     me how many loaded` → the reply's counts; `SELECT count(*) FROM
     app_tcms.cases` on pai matches. Then `@qa run bin/ap-verify --all,
     upload the results and record them — no fixes tonight, just the
     numbers` → the run's transcript shows `ap-upload` printing three ids,
     a `tcms record_results` call with those ids, the tool's totals; the
     `test_runs` row; the `app.tcms.run.recorded` envelope (kafka console
     consumer, last 1); the `#qa` line from the app (screenshot). Wall time
     of the full suite in the pod (`verify.json` seconds by suite — quote).
  3. **Dashboards (AC-2).** `/apps/tcms/` Overview, Runs, the run page,
     Cases, a case page, Health — screenshots at 1280 dark and 390 light
     through the forward; the pyramid's three numbers match the
     `results.layer` counts in the DB.
  4. **The fence (AC-3).** In `#qa` open `QA-1`: "in
     `services/backend/agentplatform/relay.py` add a comment above
     `build_mention_prompt`" and assign it to `agent:qa` → either the QA
     refuses by prompt (record the thread reply) or the publish is refused
     by the API: the `⛔ publish refused for qa: services/backend/
     agentplatform/relay.py is outside its test paths` card, the `refused`
     `workbench.event`, nothing on GitHub. Record which.
  5. **A test-only publish (AC-3).** `QA-2`: "the `tickets.one_line`
     helper has no test for a 5 KB input with `@all` in it — add one" →
     the run, the `qa/qa-2` PR, `auto_merge: true` in `GET
     /api/pull-requests` (or the card's warning if the repo does not allow
     it — record which; if allowed, the merge commit on `main` after CI,
     with its time), the ticket in `review`, the thread card, the files in
     the PR all under `services/backend/tests/`.
  6. **The walk and the gate (AC-4).** `@qa walk the UI and tell me what
     you saw; open a live session only if the quota allows` → the
     transcript shows `walk.mjs` running, `index.json` summary, which PNGs
     it Read; and either `quota_ok` → `ok: true` and a `mcp__playwright__
     browser_navigate` to `http://ap-web:8090/...` in the transcript, or
     `ok: false` and the "not spending the browser: …" line in `#qa`
     (screenshot). If the real quota is under both thresholds, ALSO prove
     the refusal path: set the QA's `quota_7d_max_pct` to 1 via the agent
     editor, repeat the ask, record the line, restore 50. Confirm the
     transcript never shows the password (grep the run's events for the
     secret's value on pai; record "absent").
  7. **The nightly (AC-5).** `POST /api/jobs/{qa-nightly}/run` (Run Now)
     → the whole flow end to end; the note in `#qa`; any `QA-n` tickets it
     opened with their evidence; any PR it made. Time and turns.
  8. **Cluster.** `helm history` revision; `kubectl top` for the QA pod and
     `ap-app-tcms`; `app_tcms` table sizes.
  Then set the design's Status to shipped with the helm revision.
  Acceptance: items 1–8 recorded with commands, timestamps and screenshot
  paths; the "Definition of done" passes.

- [ ] **T12 Docs.** `[after T11]` (all ACs)
  Files: `docs/design/25-qa-agent-and-tcms.md` (Status → shipped with the
  rev; "AS BUILT" from the ticked tasks and the implementer reports;
  "Deferred" refreshed), new `docs/building-blocks/tcms.md` (what a case
  is and where it lives, the file format, how a test links to a case, what
  the nightly does, the dashboards, the tool's actions, how results get in
  — artifacts — and why the agent cannot type them, how to add a suite),
  `docs/building-blocks/README.md` (index row: TCMS — git `tcms/cases/`
  (definitions) + Postgres `app_tcms` (results) — what the tests are and how
  they are doing), `docs/building-blocks/glossary.md` (QA, `#qa`, TCMS,
  Case, Suite, Test run (TCMS), Ref, Unlinked, Layer, Walk, Session; the
  `Platform agents` entry gains the QA), `docs/building-blocks/agents.md`
  (the QA among seeded agents; `PlaywrightMCP`), `docs/building-blocks/
  tools.md` (`tcms` in the list; the `files` paragraph from T4 checked),
  `docs/building-blocks/apps.md` (the tcms app in the first paragraph
  beside news/stockmarket/running; the "tool writes the app's rows"
  pattern now has two examples), `docs/building-blocks/workbench.md` (a
  section "Test-only agents": globs, auto-merge, the ruleset backstop),
  `docs/building-blocks/security.md` (the `qa` reader principal; two
  humans-shaped principals now), `docs/security.md` (same, under Auth),
  `docs/design/00-overview.md` (row 25; the data-model list gains
  `principals` note and `app_tcms`), `docs/deployment.md` (the app-tcms
  image row), `README.md` if it lists apps or seeded agents,
  `docs/vision/agent-ecosystem.md` (confirm the appended section matches
  what shipped). A sonnet doc reviewer checks every path, name, count and
  setting against the tree. Acceptance: every path/name in the docs exists;
  the Help page lists TCMS.

### Repairs

(added by the loop when the definition of done fails)

### Deferred

- The readiness gate blocks any agent whose bound secret is unset, regardless of the secret's `required` flag (design 10's rule). For the QA that only bites mid-rotation (value cleared, mark not yet cleared); a per-binding "optional" severity would let it run without the browser instead. Not built.

(low/medium findings the loop chose not to fix, with file:line)

## Definition of done

All T1–T12 are `[x]` (a `[!]` task counts as not done until it has a
Repairs entry that is `[x]`); `cd services/backend && .venv/bin/python -m
pytest -q --timeout=120` is green; `cd services/runner &&
../backend/.venv/bin/python -m pytest -q` is green; `cd services/mcp-broker
&& ../backend/.venv/bin/python -m pytest -q` is green; `cd tools/tcms &&
../../services/backend/.venv/bin/python -m pytest -q test_run.py` is green;
`cd apps/tcms/backend && ../../../services/backend/.venv/bin/python -m
pytest -q` is green; the facade suite is green under its 3.12 venv; `cd
services/web && npm run -s lint && npm run -s check:tokens && npm run -s
build && npx playwright test` is green and `npm run build -w tcms-frontend`
builds; `python sdk/regenerate.py && git diff --exit-code sdk/` is clean;
`helm template` renders with `spire.enabled` true and false and `tcms`
enabled; the NUC runs the new images (`kubectl get pods` all Running incl.
`ap-app-tcms`; `/apps/tcms/` loads through the forward; `GET /api/agents/qa`
is `role: dev` and not blocked; `kubectl get secret qa-web-login` has two
keys); "Live verification" below records items 1–8 with the screenshots —
item 5 may read "auto-merge not enabled on the repo; the PR waits with the
card's warning" and still passes. When every line above holds, write `PASS
<date> helm rev <n>` as the first line of "Live verification".

## Live verification

**PAUSED 2026-09-19 22:45 EDT by Kyle (weekly quota at 98%).** State: deployed on pai as **helm rev 63**; items 1–5 below are recorded from the verify agent's draft, items 6–8 not run. Uncommitted in the tree from THIS build: repair R-Q1 (6 files: `secrets/qa-web-login/verify_qa_login.py`, `agentplatform/{secretverify,verifierloop,dispatcher_main}.py`, `api/secrets.py`, `tests/test_secretregistry.py` — the verifier hands `settings.api_internal_url` to scripts; 41 targeted tests green; NOT yet deployed, so `/api/secrets` still says `qa-web-login` invalid although the qa login is 200) and this plan file. Everything else uncommitted belongs to Kyle's concurrent Codex session (design 26). To resume: commit R-Q1 + this file by path (never `-A`), rebuild+redeploy backend (the verifier lives in the dispatcher), then items 6–8, then the repairs below, then T12.

Repairs found by the live run, not yet queued as tasks: **R-Q2** the QA's branch is `coder/qa-18` (PR #15) — `agentplatform/workbench.py::branch_for` must use the `qa/` prefix when the agent's `push_path_globs` is non-empty (the design, `/changes` and the ruleset all expect `qa/<key>`); **R-Q3** the QA's own in-run `bin/ap-verify` uses the 900 s default (same as design 24's deferred note) — make the default read `AP_VERIFY_TIMEOUT`.

Deployed 2026-09-19 18:05 EDT, **helm rev 63** (backend, runner, runner-dev,
mcp-broker, mcp-facade, web, app-tcms built and imported; `tcms` added to
`apps.enabled`). Post-deploy at 18:20 EDT through the forward: all platform
pods Running incl. `ap-app-tcms-84859bd4d8-nr74z` (1/1); `schema_marks` has
`qa-channel-v1`, `qa-seed-v1`, `qa-nightly-job-v1` (22:07:59Z) and
`qa-principal-v1` (22:08:01Z); `kubectl get secret qa-web-login` → keys
`QA_WEB_PASSWORD`, `QA_WEB_USER`; `app-tcms-db` (6 keys) and `app-tcms-key`
provisioned; `GET /api/tools` lists `tcms` (9 tools); `GET /api/agents/qa` →
200, `role: dev`, `system: true`, `model: sonnet`, 7200 s, `harness_tools
[Glob, Grep, PlaywrightMCP]`, six platform tools incl. `mcp__platform__tcms`,
`secrets [qa-web-login]`, the eight `push_path_globs`, `may_delete_tests:
true`, 80/50; version 1 `changed_by system:qa`, `changed_via seed`. `GET
/api/secrets` shows `qa-web-login` as **`invalid`** — the verifier runs in the
dispatcher pod without `AP_API_URL` (repair R-Q1, queued); the login itself
works (item 1). Kafka topics `app.tcms.run.recorded` and `workbench.events`
exist. `#qa` is channel `ef127e98…` prefix `QA` (16 tickets already, so the
plan's "QA-1"/"QA-2" are QA-17/QA-18 below); job `qa-nightly` id
`e9d61a1c…`, `0 2 * * *` America/Toronto, `relay_channel: qa`.

1. **Reader login (AC-4)** — 22:20Z. `POST /api/login {"principal":"qa",
   "password":<from the k8s secret>}` → 200 (jar `scratchpad/cj-qa.txt`);
   `GET /api/whoami` with it → 200 `{"principal":"qa","role":"reader",
   "agent":null,…}`; `PUT /api/agents/qa` with the qa cookie (the row's own
   full body, a no-op replace) → **403** `writing agent definitions requires
   the admin session or an agent granted agents_edit / agents_grant`; `POST
   /api/tickets` with it → 403. `GET /api/agents/qa/versions` afterwards still
   one version (`seed`). The API log line: `"PUT /api/agents/qa HTTP/1.1" 403
   Forbidden`. The admin jar's whoami → `admin`/`admin`.

2. **Cases and ingest (AC-1)** — two summons in `#qa` (channel
   `ef127e989e724aa49033f843d6665793`), admin through the forward.
   - *Sync*, 22:21:27Z: `POST /api/relay/channels/ef127e98…/messages
     {"body":"@qa sync the cases and tell me how many loaded"}` → run
     `9e6e336421574d7193fccb14cb6ac56a` (`trigger: mention`, 22:21:28–22:21:48Z,
     2 turns). Transcript: `mcp__platform__tcms {"action":"sync_cases"}` →
     `{"synced": 98, "retired": 0, "suites": 8, "source_sha":
     "2e9e15bbf10041580225d9e0899fd7970866ceaf", "errors": []}`; thread reply
     22:21:47Z: "98 cases loaded (0 retired, across 8 suites), no validation
     errors. Source: `main` @ `2e9e15bbf1`." On pai: `SELECT count(*),
     count(DISTINCT suite), min(source_sha) FROM app_tcms.cases` → `98 | 8 |
     2e9e15bb…` (layers: unit 30, integration 41, e2e 19, manual 8) — matches.
     **Pod** `run-9e6e33642157-f8lfk` (`kubectl get pod -o json`): image
     `agent-platform-runner-dev:dev`; env names `AP_RUN_ID AP_AGENT AP_PROMPT
     AP_KAFKA_BOOTSTRAP AP_MODEL AP_CLAUDE_PROXY_URL AP_API_URL AP_API_TOKEN
     AP_MCP_URL AP_WORKSPACE AP_GIT_REMOTE_URL AP_DEFAULT_BRANCH AP_MAX_TURNS
     AP_VERIFY_TIMEOUT AP_WEB_URL AP_PUBLISH_MAX_BYTES PLAYWRIGHT_BROWSERS_PATH
     AP_SESSION_TOKEN AP_USER_MESSAGE` plus `envFrom secretRef qa-web-login`
     (`QA_WEB_USER=qa`; `QA_WEB_PASSWORD` set — checked with `test -n`, never
     printed) — **no `AP_GITHUB_TOKEN`**; `AP_MODEL=sonnet`,
     `AP_VERIFY_TIMEOUT=2400`, `AP_WEB_URL=http://ap-web:8090`; limits cpu 3 /
     6Gi, requests 500m / 2Gi; `dshm` Memory 1Gi; securityContext unchanged
     from design 24 (drop ALL, RO root, 1001, seccomp RuntimeDefault); owner
     Job `run-9e6e33642157`.
   - *Full suite*, 22:22:55Z: `{"body":"@qa run bin/ap-verify --all, upload
     the results and record them — no fixes tonight, just the numbers"}` →
     run `0937a118d0f14a1bb4386a9272940dd7` (22:22:56–22:47:05Z, **24 m 9 s**,
     37 turns, $1.20; pod `run-0937a118d0f1-x67b4`, peak `kubectl top`
     1258m / 597Mi). **argv** (`/proc/48/cmdline`): `claude --agent qa
     --resume 80e0b9d0-… -p <prompt> --output-format stream-json --verbose
     --model sonnet --permission-mode acceptEdits --strict-mcp-config
     --allowedTools Bash Read Edit Write NotebookEdit Glob Grep
     mcp__platform__relay mcp__platform__tickets mcp__platform__wiki
     mcp__platform__quota_ok mcp__platform__artifacts mcp__platform__tcms
     mcp__platform__* mcp__playwright__* --max-turns 200 --mcp-config
     /tmp/mcp-….json`; the prompt ends `<workbench branch="coder/run-0937a118d0f1"
     base="main" commits_ahead="0">` (no ticket → `run-<id>`). The runner
     started pid 70 `node /usr/bin/playwright-mcp --headless --isolated
     --no-sandbox --executable-path /opt/chromium/chrome --storage-state
     /workspace/qa/state.json --allowed-origins http://ap-web:8090
     --image-responses allow --output-dir /workspace/qa/mcp --viewport-size
     1280x800 --config /workspace/qa/mcp.json`; `/workspace/qa/mcp.json` =
     `{"browser":{"launchOptions":{"args":["--host-resolver-rules=MAP * ~NOTFOUND,
     EXCLUDE ap-web","--no-sandbox"]}}}`; `state.json` (mode 0600, 219 B)
     carries one cookie `ap_session` for domain `ap-web`. Init event tools:
     the seven harness tools, six `mcp__platform__*`, and the
     `mcp__playwright__*` set (38 tools); model `claude-sonnet-5`.
     **Transcript:** `wiki read ap-verify-backend-suite-timeout`, then `python3
     bin/ap-verify --all --out /workspace/verify` (pid 116; Claude Code moved it
     to the background at 600 s and the QA waited with `kill -0`); `verify.json`
     22:42:46Z, **`ok: false`** — per suite (seconds): backend **timed out at
     900.0** (the suite's own budget; `[ap-verify] timed out after 900s` at 83%
     of the dots, so **no `junit-backend.xml` and no `coverage-backend.xml`
     were written**), sdk 7.2 ✓, broker 2.6 ✓, facade 8.4 ✓, **executor 8.9
     exit 1** (`test_timeout_kills_the_whole_process_group`: the killed
     grandchild is reparented to the pod's pid 1 and stays a zombie past the
     3 s deadline), **runner 8.5 exit 1** (5 failures, all `workbench prepare
     failed: Name or service not known` — the pod's own `AP_WORKSPACE=dev`
     leaks into tests that never set it; `AP_WORKSPACE= pytest` → 72 passed),
     connector-discord 1.1 ✓, web-lint 0.4 ✓, web-tokens 0.3 ✓, web-build 11.7
     ✓, web-storybook 5.5 ✓, **web-playwright 177.4 ✓ (277 tests)**, the four
     apps' backend+frontend suites 1.3–4.2 ✓, the nine tool suites 0.9–1.8 ✓,
     helm and claude-proxy skipped (`helm not on PATH`); **sum 1 167 s (19 m
     27 s)**, `head 2e9e15bb…`. Then `./bin/ap-upload /workspace/verify/
     junit-all.xml /workspace/verify/playwright-web.json` → **two ids**
     `30565c6728c94428ba07d3687a970e66`, `99519ac6597e400eb95e48ebd3bfef08`
     (not three: the backend JUnit never existed); `tcms record_results
     files=[both] commit_sha=2e9e15bb… branch=coder/run-0937a118d0f1
     verify={…}` → **`error: tool exited 2: junit-all.xml: this is
     Playwright's JUnit reporter output; record the JSON report
     (--reporter=json) instead so results are not counted twice`** (the
     double-count guard, live); retried with `files=[99519ac6…]` →
     `{"test_run_id": 1, "already_recorded": false, "commit_sha": "2e9e15bb…",
     "files": [{"name": "playwright-web.json", "kind": "playwright", "results":
     277}], "totals": {"pass": 277}, "linked": 48, "unlinked_refs": 229,
     "coverage_packages": 0, "skipped_testcases": 0, "unresolved_files": []}`.
     The QA also wrote wiki page `ap-verify-dev-pod-env-leaks` (the two
     pod-only failures), tried to commit `.ap/pr.md` (gitignored — no commit),
     `bin/ap-verify --changed` → 0 paths; frame `{"seq":233,"type":"workbench",
     "published":false,"reason":"no changes"}`; `succeeded`. Thread reply
     22:47:05Z quoted in part: "**Uploaded/recorded**: `playwright-web.json`
     only (`junit-all.xml` was refused — … no backend JUnit exists since that
     suite timed out before writing one). `tcms record_results` → run id 1,
     277 pass, 48 linked to cases, 229 unlinked refs, 0 coverage packages".
   - **DB row** `app_tcms.test_runs`: `id 1 | 2e9e15bbf100 | coder/run-0937a118d0f1
     | 0937a118d0f14a1bb4386a9272940dd7 | qa | 22:26:30.04Z | 22:45:57.21Z |
     verify_ok f | published_at 22:45:59.20Z | 31 suites`; `results`: `e2e
     pass 277` (48 with a `case_key`, 229 without); `coverage_snapshots` 0.
   - **Kafka** `app.tcms.run.recorded` (console consumer, first message):
     `{"type": "tcms.run.recorded", "schema_version": 1, "id": "e9014a40…",
     "ts": "2026-09-19T22:45:59.153427+00:00", "key": "1", "source":
     "app-tcms", "data": {"test_run_id": 1, "commit_sha": "2e9e15bb…",
     "branch": "coder/run-0937a118d0f1", "run_id": "0937a118…", "agent": "qa",
     "totals": {"pass": 277, "fail": 0, "skip": 0, "flaky": 0, "error": 0},
     "seconds": 1167.17, "verify_ok": false, "unlinked": 229}}`.
   - **`#qa` line from the app** 22:45:59Z, author `app:tcms`, kind `event`:
     `🧪 test run 0937a118… on 2e9e15b · 277 pass · 229 unlinked · 19 m 27 s ·
     [open](/apps/tcms/runs/1)` — `scratchpad/live-qa/qa-room-after-record-1280-dark.png`.
   - **Password never in the transcript:** the secret's value (32 chars, read
     into a shell variable from `kubectl get secret qa-web-login`) grepped
     against both runs' `/events` bodies → **absent** (and `QA_WEB_PASSWORD`
     the name appears 0 times; the QA's own `env | grep AP_` printed only
     `AP_*`, which includes the per-run `AP_API_TOKEN` — that token dies with
     the run).
   - **Findings for the engineer, not fixed here:** (a) the backend suite
     cannot finish inside ap-verify's 900 s per-suite budget in the pod, so the
     TCMS never receives the backend's JUnit or coverage — the pyramid's unit
     and integration layers read 0 and Coverage reads "no coverage recorded"
     until that budget or the suite changes; (b) executor and runner suites
     fail only in a dev pod (documented by the QA on the wiki).

3. **Dashboards (AC-2)** — 22:49–22:52Z, admin cookie, Playwright through the
   forward (`scratchpad/live-qa/shot2.mjs`: log in via `page.request.post`,
   load `/apps/tcms/`, reach each page by clicking its in-app link, `data-theme
   =light` set on `<html>` for the light shots). `GET /apps/tcms/api/overview`
   → `pyramid [{"layer":"e2e","count":277,"seconds":333.91},{"layer":
   "integration","count":0,…},{"layer":"unit","count":0,…}]`, `pass_rate [{…
   "pass":277,"fail":0,"total":277,"rate":1.0}]`, `attention {"failing":0,
   "flaky":0,"unlinked":71,"prune_candidates":0}` — the three pyramid numbers
   equal `SELECT layer, count(*) FROM app_tcms.results GROUP BY 1` (`e2e 277`,
   no other rows). Screenshots (`scratchpad/live-qa/`), all 1280 dark and 390
   light, every one `hscroll false`, zero console errors: `tcms-overview-*`
   (4 stat tiles 0/0/71/0, pyramid e2e 277 · 5 m 34 s, Pass rate 100.0% "277
   of 277 on 2e9e15b", Coverage "no coverage recorded", Latest run row
   `2e9e15b coder/run-0937a118d0f1 🦀 qa Sep 19, 06:26 PM 19 m 27 s 277 PASS
   verify ✗ 229 unlinked`), `tcms-runs-*`, `tcms-run-1-*` (`/runs/1`, 277 result
   rows grouped by spec file), `tcms-cases-*`, `tcms-case-*`
   (`/cases/tools.ui-editor-grants-round-trip`: ACTIVE E2E P2 TOOLS #UI #GRANTS,
   3 steps, Automation card with two green `agent-editor.spec.ts` refs,
   "synced Sep 19, 06:21 PM from 2e9e15b", link to `tcms/cases/tools.yaml`),
   `tcms-health-*` (Slowest: 15 e2e rows led by `relay.spec.ts` 4.59 s; Flaky
   "Nothing flaky in the window."; Prune "Nothing to prune."). The 390 shots
   stack the tiles two-up and the pyramid/pass-rate/coverage cards in one
   column. **Note (platform-wide, not TCMS):** a deep link such as `GET
   /apps/tcms/runs` answers `404 {"detail":"Not Found"}` — every app mounts
   `StaticFiles(html=True)`, which has no SPA fallback (`/apps/news/x` and
   `/apps/stockmarket/x` 404 the same way); the `#qa` card's `/apps/tcms/runs/1`
   link therefore only works from inside the app. Filed as a handoff note.

4. **The fence (AC-3)** — 22:52:27Z: `POST /api/tickets {"channel":"qa",
   "title":"in services/backend/agentplatform/relay.py add a comment above
   build_mention_prompt", "body":"… add a one-line comment above
   `build_mention_prompt` … Nothing else.", "priority":"p3"}` → 201 **QA-17**
   (id `1391eafb24d2414d82959b8ff0fccfd0`, root message `75626962…`); `POST
   /api/tickets/QA-17/assign {"to":"agent:qa","notify":true}` → 200 → run
   `8cdbf562b3f14d048c79554df5e5ebb8` (`ticket_id` set, 22:52:28–22:53:48Z,
   6 turns, $0.23). **Outcome: refused by prompt** — the QA grepped the
   function, then wrote: "This ticket asks for a one-line comment inside
   `services/backend/agentplatform/relay.py` — that's product code, not a test
   path. My scope is limited to test directories, `test_*.py` files, and
   `tcms/cases/`; the platform's publish policy refuses anything else from me
   even for a trivial docs-only change. I won't touch it."; `tickets comment`
   on QA-17 ("Out of my scope … Handing off to @engineer to land it. Not
   closing since I didn't do the work."), `tickets assign to agent:engineer`,
   `bin/ap-verify --changed` → 0 paths, `.ap/pr.md` written; frame
   `{"seq":43,"type":"workbench","published":false,"reason":"no changes"}`;
   `succeeded`. Nothing reached the API's fence (no publish), so no `⛔` card
   and no `refused` event: `workbench.events` offsets unchanged (`0:1 1:1
   2:2`), `gh pr list` still ends at #14, no `coder/qa-*` branch on origin.
   Side effect worth knowing: the reassignment summoned the engineer twice
   (runs `c9e2b3be…`, `7424a155…`), who deferred both for quota ("7-day at
   96%, cap 90%"). I moved QA-17 to `cancelled` afterwards (reason: fence
   probe) so Monday's `eng-queue` does not land the comment.

5. **A test-only publish (AC-3)** — 22:56:02Z: `POST /api/tickets
   {"channel":"qa","title":"the tickets.one_line helper has no test for a 5 KB
   input with @all in it — add one","body":"… Add one under
   services/backend/tests/. Test code only.","priority":"p3"}` → 201
   **QA-18** (id `a61e3f7afaeb42d0971860b53763bc2e`, root message
   `5333cda1…`); assign to `agent:qa` → 200 → run
   `2fba955fff964aeb8059a10050139adf` (pod `run-2fba955fff96-bzdcw`,
   22:56:02–23:33:38Z, **37 m 36 s** wall; claude 18 m 31 s / 33 turns /
   $1.34, the rest finalize verify; peak `top` 1298m / 569Mi). The argv's
   block was `<workbench branch="coder/qa-18" base="main" commits_ahead="0"
   ticket="QA-18">`. The QA moved the ticket `→ in progress` 22:56:37Z, read
   `test_tickets_lib.py`, added a ~5.1 KB fixture (`FIVE_KB_WITH_ALL`, 105
   `@all`s) and two tests (`test_one_line_caps_and_strips_a_room_mention_in_a_5kb_body`
   parametrized over `TITLE_LIMIT`/`REASON_LIMIT`/40, and
   `test_one_line_strips_every_room_mention_when_the_5kb_body_fits`), ran them
   3× (+ the whole file, 83 green), added case `tickets.one-line-caps-large-body`
   to `tcms/cases/tickets.yaml`, `tcms sync_cases` → 98 (the new case counts
   only once it is on `main`), ran `bin/ap-verify --changed` itself (backend →
   the 900 s timeout again, which it called out as the known limit), committed
   `19ebc85`, wrote `.ap/pr.md`, `→ review` 23:14:49Z, replied 23:14:57Z.
   Finalize verify ran the backend suite to completion (**exit 0, 1 115.8 s**
   — the runner's finalize budget is `AP_VERIFY_TIMEOUT=2400`, the agent's
   own invocation uses ap-verify's 900 s default) and **published → PR #15**
   https://github.com/kylep/agent-platform/pull/15 (`qa: QA-18 the
   tickets.one_line helper has no test for a 5 KB input with all in it — add
   one`, author `pericakai[bot]`, `coder/qa-18 → main`, created 23:33:37Z,
   files exactly `services/backend/tests/test_tickets_lib.py` (+33 −3) and
   `tcms/cases/tickets.yaml` (+11 −0) — both under the QA's globs, both
   flagged `"test": true` in the event). **Frame** `{"seq":193,"type":
   "workbench","published":true,"branch":"coder/qa-18","pr":{"number":15,…},
   "paths":[…2],"tests_removed":[],"ticket_state":"review","auto_merge":false,
   "verify_ok":true,"warnings":["auto-merge could not be enabled: Auto merge
   is not allowed for this repository","ticket not moved: cannot move QA-18
   from review to review"]}`. `GET /api/pull-requests` row: `{"number": 15,
   … "branch": "coder/qa-18", "author": "pericakai[bot]", "ticket_key":
   "QA-18", "agent": "qa", "auto_merge": false}`; `gh pr view 15` →
   `autoMergeRequest: null`, `mergeable: MERGEABLE`. **Thread card** 23:33:38Z
   `🔀 qa published coder/qa-18 → PR #15 · 2 files · verify ✓ backend · ⚠️
   auto-merge could not be enabled: Auto merge is not allowed for this
   repository · ⚠️ ticket not moved: cannot move QA-18 from review to review`
   (card `{"type":"publish","pr":15,"branch":"coder/qa-18","files":2,
   "tests_removed":[],"verify_ok":true,"refused_reason":null,"run_id":
   "2fba955f…","agent":"qa","warnings":[…]}`). `workbench.events` #5:
   `{"event":"published","agent":"qa","ticket_key":"QA-18","branch":
   "coder/qa-18","pr":{"number":15,…},"paths":[{"path":"services/backend/
   tests/test_tickets_lib.py","status":"M","additions":33,"deletions":3,
   "test":true},{"path":"tcms/cases/tickets.yaml","status":"M","additions":
   11,"deletions":0,"test":true}],"tests_removed":[],"verify":{"ok":true,
   "suites":[{"name":"backend","exit":0,"seconds":1115.8}]},"reason":null}`.
   Ticket `QA-18` is in **`review`**, assignee `agent:qa`. **Auto-merge:
   the repository does not allow it** (`gh api repos/kylep/agent-platform`
   → `allow_auto_merge: false`, no rulesets) — the card's warning is the
   documented outcome; the PR waits for Kyle (Handoff 1–2). **Two things
   the design says that the build does not:** (a) the branch is
   `coder/qa-18`, not `qa/qa-18` — `branch_for(run, ticket, prefix="coder")`
   is never called with `prefix="qa"` (the "seam" exists, nothing uses it);
   `PLATFORM_BRANCH_PREFIXES` in `api/pulls.py` lists `qa/` for a branch
   that is never created. Harmless (the fence and auto-merge key off
   `push_path_globs`, not the prefix) but the design's "`qa/<key>` PR" is
   not what lands; (b) the engineer was summoned by the QA's own ticket
   reply in the thread (run `32bac531…`, "Nothing for me to action on
   QA-18") — the hop budget did its job, but a QA ticket thread costs an
   engineer turn each time the QA writes `@engineer` in prose. **CI on PR
   #15:** `web` and `subscription-guard` fail, **both already failing on
   `main` at `2e9e15b`** (run 35471905002): `subscription-guard`'s "forbid
   API-key auth" grep trips on `services/backend/tests/test_runner_dev_image.py:56-57`
   (the test's own `assert "ANTHROPIC_API_KEY" not in text` literal), and
   `web`'s UI gate fails `wiki-integrations.spec.ts:172` "/memories scrolls
   sideways at 390" — the very overflow the QA's walk found in item 6
   (QA-19). Backend/runner/facade/apps/tools/helm/claude-proxy/secret-scan/
   security-scan green.

6. **The walk and the gate (AC-4)** — after the 5-hour reset (02:10Z the
   window read 7%; the 7-day window 97%): with the QA's real thresholds
   (80 / 50) the gate must refuse on the 7-day window, so both paths were
   run.
   - *Refusal path*, 02:13:06Z: `{"body":"@qa walk the UI and tell me what
     you saw; open a live session only if the quota allows"}` → run
     `070d3e620a3c43ab8912917f5ba6d44f` (02:13:06–02:20:07Z, 52 turns,
     $3.36). Transcript: `mcp__platform__quota_ok {}` → `{"ok": false,
     "five_hour_pct": 7, "seven_day_pct": 97, "five_hour_max_pct": 80,
     "seven_day_max_pct": 50, "stale": false, "reason": "the 7-day window is
     at 97%, over its 50% limit"} no: 5h 7% ≤ 80, 7d 97% > 50`; the QA:
     "Quota says no — not spending the browser … I'll do the walk instead.";
     `./bin/ap-web-login` → `ok`; `node services/web/scripts/walk.mjs` bare
     → the usage line (the prompt's invocation lacks the flags), then `node
     services/web/scripts/walk.mjs --base "$AP_WEB_URL" --state
     /workspace/qa/state.json --out /workspace/walk-out` → **`walk: 36 routes
     × 4 = 144 entries, 0 ok, 144 flagged (heading missing 0, console errors
     144, failed requests 144, overflow 4) → /workspace/walk-out/index.json`**
     (`index.json` is a 144-entry list with `route width theme file ok
     heading_found console_errors failed_requests overflow`). It read
     `index.json` selectively (counted the failed requests: 304× `403
     /api/pull-requests`, 16× `/api/secrets`, 12× `/api/jobs`, 8×
     `/api/schedules`, 8× `404 /api/artifacts/a1a1…`, 4× `404
     /api/wiki/pages/deploying` — the reader cookie's 403s from nav chrome
     and two e2e-fixture routes in `pages.ts`), **Read** `memories-390-dark.png`
     (398×844) and `help-tools-390-dark.png` (440×17511 — cropped the top
     400 px with PIL and Read that), checked the CSS (`.help-layout` has no
     mobile breakpoint; `.row-actions` is `nowrap`), opened **QA-19**
     "/memories and /help(/tools) overflow horizontally at 390px — no mobile
     stacking" with file/line refs, assigned to `agent:engineer`, appended the
     404-fixture note to wiki `ap-verify-dev-pod-env-leaks`, `ap-verify
     --changed` → 0 paths; frame `published:false "no changes"`; no
     `mcp__playwright__*` call anywhere in the 245 events. Thread reply
     02:20:06Z begins **"Not spending the browser: 7-day usage is at 97%,
     over its 50% limit (`quota_ok` said no). Ran the scripted walk
     instead."** — `scratchpad/live-qa/qa-walk-refused-top-1280-dark.png`
     (and `…-refused-1280-dark.png`, the QA-19 card under the ask).
   - *Allow path*, 02:21:29Z: `GET /api/agents/qa` → full body, one field
     changed, `PUT /api/agents/qa` (whole definition, `face`/`image_artifact_id`
     dropped from the body) → 200; re-GET diff against the original: only
     `quota_7d_max_pct: 50 → 100`; version 2 by admin. 02:21:37Z: `{"body":
     "@qa the gate has been widened for a test — check quota_ok again; if it
     allows, open a live session and answer one specific question: on
     /apps/tcms/ does the Latest run row's \"open\" link reach the run page?
     Then close the session and tell me what you saw."}` → run
     `177d9f1cf00b4e8ea1d71edb3987c8b0` (02:21:37–02:25:13Z, 13 turns,
     $0.76). Transcript: `quota_ok` → `{"ok": true, "five_hour_pct": 13,
     "seven_day_pct": 98, "five_hour_max_pct": 80, "seven_day_max_pct": 100,
     "stale": false, "reason": "ok"} ok: 5h 13% ≤ 80, 7d 98% ≤ 100`;
     `bin/ap-web-login` → `ok`; first `mcp__playwright__browser_navigate
     {"url": "https://pai.example/apps/tcms/"}` → **`net::ERR_BLOCKED_BY_CLIENT`**
     (the model guessed a host; the config's `--host-resolver-rules=MAP *
     ~NOTFOUND, EXCLUDE ap-web` refused it — the origin fence, live); then
     `mcp__playwright__browser_navigate {"url": "http://ap-web:8090/apps/tcms/"}`
     → `Page URL: http://ap-web:8090/apps/tcms/ · Page Title: TCMS`,
     `browser_snapshot` (the reader's page: nav + TCMS), `browser_click`
     `f1e122` ("open" in Latest run) → `Page URL: http://ap-web:8090/apps/tcms/runs/1`,
     a second snapshot (97 KB, `heading "Run #1"`), `browser_close` → "No
     open tabs". Reply 02:25:13Z: "**Answer:** Yes — … Clicking it navigated
     from `/apps/tcms/` to `/apps/tcms/runs/1`, which rendered a full "Run #1"
     detail page (commit `2e9e15b`, branch `coder/run-0937a118d0f1`, run by
     🦀 qa, 19m 27s, 277 pass, `verify ✗`) …". **One thing to fix in the
     prompt:** while debugging the blocked host the QA ran `cat
     /workspace/qa/state.json`, which put the **reader session cookie**
     (not the password) into the transcript; the file is 0600 and readable
     by design (the walk and the MCP server need it), so the prompt should
     say "never print `state.json`". Handoff.
   - *Restore*, 02:28:07Z: full `PUT` with `quota_7d_max_pct: 50` → 200;
     re-GET diff against the pre-test row → `{}` (identical: role dev,
     80/50, 8 globs, 6 tools, `[qa-web-login]`, prompt 3 452 chars, 7 200 s,
     system); versions now `3 admin/admin 02:28:07`, `2 admin/admin
     02:21:29`, `1 system:qa/seed`. `GET /api/quota/ok` → `ok: false … 7-day
     window is at 98%, over its 50% limit`.
   - **Password never in a transcript:** the secret's value grepped against
     the `/events` bodies of all six QA runs so far (`9e6e3364`, `0937a118`,
     `8cdbf562`, `2fba955f`, `070d3e62`, `177d9f1c`) → **absent** in each.


(filled by T11: deploy record, helm revision, evidence per item 1–8 with
commands, timestamps and screenshot paths)

## Handoff to Kyle

(filled by the loop; the GitHub settings below are drafted now and confirmed
with the exact glob list when T9 commits)

1. **Repository setting — allow auto-merge.** github.com →
   `kylep/agent-platform` → Settings → General → Pull Requests → tick
   "Allow auto-merge" (and, optionally, "Automatically delete head
   branches"). Without it the QA's PRs open with a warning in the `#qa` card
   and wait for you.
2. **Ruleset on `main` (the backstop for the platform's path fence).**
   Settings → Rules → Rulesets → New branch ruleset: name `main-guard`,
   enforcement Active, target `main`. Bypass list: your own user, bypass
   mode "Always" (so every bypass is logged) — NOT the PericakAI App.
   Rules: "Restrict file paths" with restricted path `**/*` and **allowed
   exceptions** = the `TEST_PATH_GLOBS` list from
   `services/backend/agentplatform/testpaths.py`, exactly these eight
   entries: `services/backend/tests/**`, `services/web/tests/**`,
   `services/claude-proxy/tests/**`, `services/*/test_*.py`,
   `apps/*/backend/test_*.py`, `apps/*/backend/tests/**`,
   `tools/*/test_run.py`, `tcms/cases/**`; "Require status checks to pass" with the CI job names
   `backend`, `runner`, `mcp-facade`, `apps`, `tools`, `web`, `helm`,
   `secret-scan` and "Require branches to be up to date" off. Effect: the
   App (which merges the QA's auto-merge PRs) can only land test paths;
   your own merges of the engineer's PRs bypass the path rule and are
   logged; nothing lands on `main` without CI.
3. **Confirm the App's permissions** (already used by the wizards):
   `Contents: Read and write`, `Pull requests: Read and write`. Auto-merge
   via GraphQL needs nothing more.
4. Nothing to paste: the `qa` login is minted by the platform into the
   `qa-web-login` secret; to rotate it, clear the secret's value on
   `/secrets` and delete the `qa-principal-v1` row from `schema_marks`.
