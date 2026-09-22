# 25 — The QA agent and the TCMS (test cases in git, results in the database)

Status: **designed 2026-09-18**, plan at
`docs/superpowers/plans/2026-09-18-qa-agent-and-tcms.md`. Builds on the
Workbench [24](24-coding-agent.md) — the dev run, publish, the path policy,
`bin/ap-verify`, `quota_ok` — and must not start until that plan's
Definition of done records PASS. Also on Apps [11](11-apps-and-reports.md)
(the TCMS is an app), Tools [12](12-executable-capabilities.md) (the `tcms`
tool writes the app's schema, as `prices` does for stockmarket), Artifacts
[23](23-artifacts-and-image-studio.md) (result files travel as artifacts),
Tickets [20](20-tickets-agent-work-tracker.md) (findings are `QA-n`) and
Quota [22](22-quota-usage-bars.md).

One of the numbered design records under `docs/design/`. The series index is
`docs/design/00-overview.md`; component names are defined in
`docs/building-blocks/glossary.md`, and Kyle is the project owner.

## The problem

Kyle's ask, verbatim:

> a QA agent. It can also use relay, modify tickets, interact with the wiki,
> and PR against the repo. It only changes test code, and it is allowed to
> push changes to test code directly. It slings unit and integration tests +
> writes e2e tests. It also operates a new app, the TCMS (testcase management
> system) that defines test cases for everything. It decides what automated
> tests are needed according to the code and tcms. It prunes old or bad
> tests, measures test runtime and ensures its fast and focussed, and ensures
> good coverage across the test pyramid. It also uses playwright mcp to qa
> changes, but is aware of my quota and isn't willing to reach for that when
> weekly quota is above ... 50%, and 5h is above... 80%... those are somehow
> tuneable, too.

What exists: 87 backend test files and a dozen smaller suites, each run by
CI with no coverage measurement, no registered markers, no recorded
timings and no taxonomy (R3); 17 Playwright specs against a mocked API that
never exercise a real login (R5); a `get_quota_usage` tool that answers in
prose (R4); one human principal, `admin`, and a `/api/login` that
authenticates only it (R5). Nothing says what the platform's test cases
*are*, nothing says which test proves which case, and nothing knows whether
the suite got slower last week. The Workbench (design 24) gives an agent a
shell, the toolchains, a browser and a publish door with a path policy; this
design gives one agent the job of using them on the tests.

## The decision in one paragraph

The **QA** agent (`agent:qa`) is a second `role: dev` row with a different
grant set: `push_path_globs` = the platform's `TEST_PATH_GLOBS`,
`may_delete_tests: true`, `quota_5h_max_pct: 80`, `quota_7d_max_pct: 50`,
a `PlaywrightMCP` harness grant, and the `qa-web-login` secret. It publishes
through the same door as the engineer; because its globs are non-empty the
API refuses any path outside them *and* enables GitHub auto-merge on its
`qa/<key>` PR, so a test-only change lands on `main` the moment CI is green
with no human in the loop — "push directly", with a PR as the receipt and CI
as the gate. The **TCMS** is an app, `apps/tcms/`, that owns schema
`app_tcms` and the dashboards at `/apps/tcms/` (pyramid, pass rate, runtime
by layer, slowest, flaky, unlinked, prune candidates), plus a brokered tool,
`tools/tcms/`, that is the app's only writer. **Case definitions live in
git** at `tcms/cases/<suite>.yaml` — reviewed with the code they cover, one
file per suite, a case linked to its automation by the pytest node id or
Playwright title it names — and **results live in the database**, written
only by the tool from JUnit XML, Playwright JSON and `coverage.xml` files
the runner produced, never typed by the model. A nightly `#qa` job summons
the QA to sync cases, run everything through `bin/ap-verify --all`, record
the results, read the reports, open or update `QA-n` tickets, write or fix
tests, and publish. Exploratory QA has two tiers: a **scripted walk** — one
Playwright script that screenshots every route at two widths in both themes
and writes an index the agent reads selectively — is the cheap default; a
**live Playwright MCP session** against the in-cluster web service is the
expensive tool, and the agent asks `quota_ok` before opening one. When the
gate says no, the room hears why, the walk runs instead, and the ticket says
what was skipped.

## Naming

The agent is **QA** (`agent:qa`), home project **`#qa`** (prefix `QA`,
adopted if it already exists on the site). The **TCMS** is the app
(`apps/tcms`, schema `app_tcms`, UI `/apps/tcms/`) and the tool
(`tools/tcms`). A **case** is one row of intent — key, title, layer, steps,
expected, automation — and lives in git; a **suite** is a file of cases;
a **test run** is one ingest of one `ap-verify` output; a **result** is one
case-or-test outcome inside a run; a **coverage snapshot** is the per-package
rollup from one `coverage.xml`. A case's **automation** is a list of
**refs**: `pytest:<nodeid>` or `playwright:<file>::<title>`; a ref that no
recorded run has ever seen is **unlinked**; a case with no refs is
**manual**. The **layers** are `unit`, `integration`, `e2e`, `manual`. The
**walk** is `services/web/scripts/walk.mjs`; a **session** is a live
Playwright MCP browser. Kafka topic `app.tcms.run.recorded`.

## Data model

**Git** (`tcms/cases/<suite>.yaml`, validated by `tools/tcms/cases.py`,
which the tool and a backend test both import — the schema has one home):

```yaml
suite: relay                     # == the filename stem; keys are "<suite>.<slug>"
area: relay                      # building block (glossary name)
cases:
  - key: relay.mention-summons   # ^[a-z0-9-]+\.[a-z0-9-]+$, unique across all files
    title: An @mention in an open channel summons the agent once
    layer: integration           # unit | integration | e2e | manual
    priority: p1                 # p0..p3
    preconditions: [the agent is enabled and a member]
    steps: [post "@news hello" in #general]
    expected: one run for news, one reply in the thread, hop 1
    automation:
      - pytest:services/backend/tests/test_relay_router.py::test_mention_summons_once
    tags: [router, loop-guards]
    tickets: []                  # QA-n keys, for provenance
```

**Database** (`app_tcms`, owned and created by the app at boot, exactly as
`apps/stockmarket` owns its tables and `prices` writes them):

```
cases              key pk, suite, area, title, layer, priority, preconditions json, steps json,
                   expected, automation json, tags json, tickets json, status 'active'|'retired',
                   synced_at, source_sha
test_runs          id pk, commit_sha, branch, run_id (the platform run), agent, started_at,
                   finished_at, verify_ok bool, suites json [{name, exit, seconds}], published_at null
results            id pk, test_run_id fk, ref (pytest:… | playwright:…), case_key null,
                   status 'pass'|'fail'|'skip'|'flaky'|'error', duration_ms, message (≤ 4 KiB), layer
coverage_snapshots id pk, test_run_id fk, package, lines_covered, lines_total, branch_rate null
```

`layer` on a result is derived from the ref's path prefix (`services/web/tests/`
→ e2e, `tools/*/test_run.py` → unit, everything else → integration unless the
linked case says `unit`) so the pyramid can be drawn with no case linked at
all. Retention: `test_runs` older than `TCMS_RETENTION_DAYS` (90) are pruned
by the app.

## Trust boundaries and guards

- **Results are machine-ingested, never agent-asserted.** The only writer
  of `test_runs`, `results` and `coverage_snapshots` is `tools/tcms/run.py`
  parsing files, and the files reach it as **artifacts**: the QA pod uploads
  `ap-verify`'s output files with `bin/ap-upload` (the run's own identity
  token, `POST /api/artifacts`), then calls `tcms record_results
  files=[<artifact ids>]`; the broker's `CustomTool` resolves a reserved
  `files` argument (≤ 4 artifact ids) into the executor's `files_in` by
  fetching each artifact's content **with the caller's token** — so a tool
  can only ingest what its caller can read. A `record_results` call with no
  files, or with a file that is not JUnit/Playwright JSON/Cobertura, is an
  error string; a status field never comes from an argument. `sync_cases`
  reads `tcms/cases/*.yaml` from the synced checkout the executor already
  mounts (`Path(__file__).parents[2] / "tcms" / "cases"`), so the DB's cases
  are what `main` says, stamped with the checkout's sha.
- **The publish policy is the fence** (design 24, unchanged): the API
  matches every changed path against the QA's `push_path_globs` and refuses
  the whole publish on the first miss, before any push; `.github/**` stays
  on the deny list for everyone. The QA may delete tests (that is pruning)
  and every deletion is named on the PR and the thread card. Auto-merge is
  GitHub's, gated on the repository's required checks — the platform never
  merges anything itself, and when the repository does not allow auto-merge
  the PR simply waits for a human with a warning in the card.
- **The browser is a reader.** The `qa` principal is a Principal row with
  `role: reader`; its session cookie renders every page and is refused by
  every write route. Its password is minted by the API once at boot
  (`ensure_qa_principal`, mark-gated, in the API's lifespan because only the
  API holds the secret store), stored into the k8s secret behind the
  declared block `qa-web-login` (`QA_WEB_USER`, `QA_WEB_PASSWORD`) and never
  shown; rotating it is deleting the secret's value and clearing the mark.
  The QA's row binds the block, so the pod gets the two env vars, and
  `bin/ap-web-login` turns them into a Playwright **storage state** file
  (`POST /api/login {principal: "qa", password}` → the `ap_session` cookie)
  that both the walk and the MCP server read — the model never handles the
  password and never types it into a page. `/api/login` learns an optional
  `principal` (default `admin`); `/api/setup` is unchanged.
- **The MCP server is locked to the platform.** The runner starts it (never
  the model): the image's `playwright-mcp` bin with `--headless --isolated
  --no-sandbox --executable-path /opt/chromium/chrome --storage-state
  /workspace/qa/state.json --allowed-origins <web_internal_url>
  --image-responses allow --output-dir /workspace/qa/mcp --viewport-size
  1280x800 --config /workspace/qa/mcp.json`, under `--strict-mcp-config`,
  with `--allowedTools mcp__playwright__*` added to the dev allow-list only
  when the agent holds the `PlaywrightMCP` grant. The origin flags are
  advisory (the package documents them as not a security boundary); the real
  boundary is Chromium's own resolver: the config file's launch args carry
  `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE <web host>`, so every name
  but the platform's web service fails to resolve inside the browser,
  redirects included. Page content it renders is untrusted input in a pod
  that holds no credential worth stealing (design 24), and screenshots are
  model turns bounded by `--max-turns`.
- **Quota.** `quota_ok` is checked before every MCP session, with the QA
  row's thresholds (defaults 80 / 50, editable in the agent editor and
  through `agents_edit`). The nightly suite run, the walk and the tool calls
  are not gated: they cost one run's worth of turns, not a screenshot per
  decision. The gate is about the browser-in-the-loop.
- **Findings are tickets, in `#qa`**, opened with the `tickets` tool
  (budgeted at `tickets_agent_creates_per_hour`), one per distinct problem,
  with the evidence: the case key, the ref, the run's `test_runs.id`, a
  screenshot artifact card when there is one. The QA never opens a ticket
  for something it can fix itself in test code — it fixes it and publishes.
- **Everything the QA writes to the repo is a PR** with the runner's
  captured `ap-verify` result in its body, auto-merged or not; a
  reader-role cookie and a test-only path fence mean the worst a
  prompt-injected QA can do is land a bad test on `main` after CI passed it,
  which the engineer's next `ap-verify` and the TCMS's flaky report both
  surface.

## The `tcms` tool (`tools/tcms/`)

`tool.yaml`: `infra.secrets: [app-tcms-db]` (the app's provisioned DB
secret, the `prices` pattern; `APP_DB_*` in the subprocess env),
`timeout_seconds: 120`, `params.action` enum. Stdlib plus `psycopg` (already
baked into the executor image for `prices`). Actions:

| action | args | writes | returns |
|---|---|---|---|
| `sync_cases` | — | upserts `cases` from the checkout; absent keys → `retired` | counts, validation errors by file |
| `record_results` | `files: [artifact ids]`, `commit_sha`, `branch?`, `verify?` (the `verify.json` object) | one `test_runs` row, its `results`, `coverage_snapshots` | run id, totals by status, unlinked refs count |
| `cases` | `layer?`, `area?`, `status?`, `unlinked?`, `q?`, `limit` | — | one line per case |
| `case` | `key` | — | the case, its refs, last 10 results per ref |
| `coverage_gaps` | `threshold_pct` (70) | — | packages under the threshold, cases with no automation, refs no case names |
| `runtime_report` | `runs` (10) | — | total seconds by layer over the last N runs, slowest 15 refs, the trend |
| `flaky` | `runs` (20) | — | refs that failed then passed on the same commit, or alternate across commits |
| `prune_candidates` | `runs` (50) | — | refs that never failed across N runs AND sit in the slowest decile, refs whose case is retired, refs that duplicate another's covered lines (only when coverage contexts exist; else omitted) |

Every read answers in the broker's line shape (≤ 4 KiB, "showing 40 of
212"); the description tells the model that `record_results` wants artifact
ids from `bin/ap-upload` and that it never accepts counts.

The parsers (`tools/tcms/ingest.py`): JUnit XML (pytest's `--junitxml`,
`xunit2`: `testcase classname+name` → `pytest:<file>::<name>`, `failure` /
`error` / `skipped` children → status, `time` → ms); Playwright JSON
(`suites[].specs[].tests[]`: `outcome` `expected|unexpected|flaky|skipped`
→ status, `results[].duration`, the title path → `playwright:<file>::<title>`);
Cobertura `coverage.xml` (`packages/package@name`, `line-rate`, `lines-valid`
via class counts). A file that parses as none of these is refused by name.
Refs are matched to `cases.automation` entries exactly; a case whose ref
matched nothing this run keeps its last result and counts as unlinked in
`coverage_gaps`.

## The app (`apps/tcms/`)

`app.yaml`: `ui: true`, `api: true`, `needs.postgres: true`,
`needs.kafka_topics: [app.tcms.run.recorded]`, `agent_key.role: annotator`
(it posts to `#qa` through the platform API; it enqueues no runs).
Backend (`tcmsapp`, the `runningapp` layout): DDL at boot; a 30 s reconciler
that finds `test_runs` with `published_at IS NULL`, publishes one
`app.tcms.run.recorded` envelope each and posts one line into `#qa` the way
the running app posts its recap (`🧪 test run 4f2e… on a1b2c3d · 912 pass ·
2 fail · 1 flaky · 4 m 12 s · [[open]](/apps/tcms/runs/…)`), then stamps
`published_at`; the retention prune. API under `/apps/tcms/api/`:
`overview` (pyramid counts and seconds by layer for the latest run, pass rate
over the last 30, coverage total, unlinked count), `runs` and `runs/{id}`,
`cases` (filters as the tool's), `cases/{key}`, `flaky`, `slowest`,
`prune-candidates`, `coverage` (by package, latest) — every one readable by
`query_app` (they are GETs with scalar params) and by the UI.

Frontend (`tcms-frontend`, an npm workspace importing `@ap/ui`, prebuilt on
the host like `news-frontend`): **Overview** — a pyramid drawn as three
stacked bars (e2e / integration / unit) labelled with count and minutes,
pass-rate sparkline over the last 30 runs, coverage total with the trend,
four attention tiles (failing now, flaky, unlinked cases, prune candidates);
**Runs** — the list with commit, agent, duration, totals, and a run page
listing results grouped by suite with failures first and their messages;
**Cases** — a filterable table (layer, area, status, automation, unlinked)
opening a case page with steps, expected, refs and the last results per ref,
plus a link to the file in git; **Health** — slowest 15 with durations over
time, the flaky list, prune candidates with the reason each is there. Charts
are server-rendered SVG from the app's API (the report-kit chart route is for
reports; the app draws its own simple bars — no client charting library).
Theme follows `localStorage.theme` as the other apps do.

## The QA (seeded row)

`qa`: `role: dev`, `system: false` (it is a replaceable worker like engineer;
`responds_to_all: false` independently keeps it out of `@all` and standup —
its own job summons it), `model: sonnet` (the nightly
is bookkeeping most of the time; it may request a stronger run by ticket),
`timeout_seconds: 7200`, `concurrency: 1`, `quota_5h_max_pct: 80`,
`quota_7d_max_pct: 50`, `platform_tools: [relay, tickets, wiki, quota_ok,
artifacts, tcms]` (no `query_app`: it sits on the wide `annotator` rung and
the `tcms` tool's read actions answer the same questions), `harness_tools: [Glob, Grep, PlaywrightMCP]`,
`secrets: [qa-web-login]`, `push_path_globs: <TEST_PATH_GLOBS>`,
`may_delete_tests: true`, description "Owns the tests: writes and prunes
unit, integration and e2e tests, keeps the TCMS current, measures the suite
and QAs the live UI — spending the browser only when the quota allows."

The prompt carries: what it owns and what it must never touch (only test
paths, the platform refuses anything else; never product code — open a
`QA-n` ticket for the engineer instead, assigned to `agent:engineer` when
the fix is clear); the **nightly** — `tcms sync_cases`; `bin/ap-verify --all
--out /workspace/verify`; `bin/ap-upload` the junit, Playwright JSON and
coverage files; `tcms record_results`; read `runtime_report`, `flaky`,
`coverage_gaps`, `prune_candidates`; for each finding either fix it in test
code now (a flaky test made deterministic, a duplicate pruned with the
reason in the commit, a missing case written into `tcms/cases/` beside the
test that proves it) or open a ticket; run `bin/ap-verify --changed`; write
`.ap/pr.md` naming every deletion and why; end with a `#qa` reply that reads
like a nightly note: what ran, what changed, what is red, what it did not
spend; the **walk** — `bin/ap-web-login`, then `node
services/web/scripts/walk.mjs`, then read `index.json` and only the PNGs it
flags (console errors, failed requests, horizontal overflow, a page that did
not render), and the ones for pages the engineer's open PRs touch; the
**session rule** — before a live browser session call `quota_ok`; when `ok`
is false say in the thread "not spending the browser: <reason>", do the walk
instead and note in the ticket what was skipped; a session is for a specific
question (follow a flow the walk cannot, reproduce a ticket), never a sweep;
the **test rules** — a new test asserts behaviour, not "did not throw"; run
a new e2e spec three times before keeping it; never weaken an assertion to
pass; prefer the layer that catches the bug cheapest; a case file changes in
the same PR as the test that proves it; the **hand-back** — when a suite is
red because of product code, a `QA-n` ticket with the failing ref, the
message and the commit, assigned to `agent:engineer`, and a `blocked` note on
its own ticket if it had one. First change-log entry `changed_via: seed`.

Jobs (seeded): `qa-nightly` — `relay_channel: qa`, `0 2 * * *`
America/Toronto, prompt "@qa — run the nightly: sync cases, run everything,
record the results, fix or file what you find, and leave a note here."

## API

```
POST  /api/login                    {password, principal?: "admin"} → looks the named Principal up; unchanged for admin
GET   /api/quota/ok                 (design 24) the QA's thresholds come from its row
GET   /api/pull-requests            (design 24) qa/* rows carry auto_merge: true|false|null
/apps/tcms/api/*                    the app's routes above, behind nginx auth_request as every app
```

## Broker

- `tcms` is a custom tool (the executor runs it); the QA declares it, which
  earns the whoami-only `tools` rung — its participant role comes from the
  core grants beside it.
- `CustomTool.run` gains the reserved `files` argument: a list of ≤ 4
  artifact ids (32 hex each, validated before they become a path), fetched
  via `GET /api/artifacts/{id}/content` with the caller's forwarded token
  (≤ 8 MiB each, the executor's `files_in` caps), passed as `files_in:
  [{name: <artifact name>, mime, b64}]`. A missing or unreadable id is a
  plain error string naming it. Generic: any tool with a manifest may take
  files this way from now on; the audit row records sizes, never bytes.
- `PlaywrightMCP` is a harness-tool name in `agentspec.CLAUDE_TOOLS` with a
  `TOOL_HELP` entry marked `dev_only: True` ("A live browser through the
  Playwright MCP server, locked to the platform's own UI; only a `role: dev`
  agent can use it, and the runner starts the server"). For a non-dev run it
  is filtered out with the sensitive set (declaring it does nothing), which
  the runner test pins.

## Relay

`#qa` gets three kinds of rows: the app's nightly line (`system`-authored
through the app key, no mentions), the QA's own replies (with "view run ↗"),
and the publish cards from design 24 (`🔀 qa published qa/qa-31 → PR #140 ·
3 files · auto-merge on · verify ✓ backend`). A refused publish reads `⛔
publish refused for qa: <path> is outside its test paths`.

## Kafka

`app.tcms.run.recorded` (app-namespaced, provisioned from `app.yaml` by the
AppProvisioner): `{test_run_id, commit_sha, branch, run_id, agent, totals:
{pass, fail, skip, flaky, error}, seconds, verify_ok, unlinked}` — published
by the app's reconciler post-commit. `workbench.events` (design 24) carries
every QA publish with its file list. The Dashboard's Tickets tile already
counts `QA-n`; a TCMS tile is deferred.

## Alternatives considered

- **Cases in the database, edited through the UI.** Kyle asked "code or db
  state"; the shipped precedent is `reports/<name>/report.yaml` in git →
  `reports` rows in the database, and a case is intent that should be
  reviewed in the same PR as the test that proves it. Results are facts
  about a run and belong in rows. One file per case would make every new
  case a one-file PR and a 200-file directory; one file per suite reviews as
  a unit.
- **A pytest `case` marker plus a conftest hook writing JUnit properties.**
  Needs a plugin loaded by every suite (nine separate pytest roots, no shared
  conftest) for what a node id already says. Refs are code-verifiable
  strings; a rename shows up as `unlinked` in the next report, which is the
  QA's job to fix.
- **Pushing straight to `main`.** Simpler than a PR, and it skips CI: a bad
  test lands before anyone runs it, and a non-fast-forward push after Kyle's
  own commit needs a rebase nobody is there to do. A `qa/<key>` PR with
  GitHub auto-merge on required checks is "direct" in effect, gated in fact,
  and leaves the file list where a human can see it.
- **The platform merging QA PRs itself** (`merge_pull_request` from the
  publish route). Immediate, but it would merge before CI answered, and it
  would make the platform a merging actor — the one thing every prior art
  agrees no agent should be. GitHub merges when CI says so.
- **A time-boxed admin credential for the browser.** Cheaper by one route
  and one seed; it hands an exploratory agent that renders untrusted pages a
  cookie that can delete agents. A `reader` principal costs `principal` on
  the login body and a seed that mints its own password.
- **Chromium in a sidecar.** Matches the ghostunnel precedent and keeps the
  runner image lean; but the QA also runs `npx playwright test`, which needs
  the browser in the same container as the tests, so the dev image carries
  it anyway and a sidecar would be a second copy.
- **Coverage-driven test generation.** R6: coverage targets are gamed;
  mutation testing is the honest signal and is expensive. v1 measures
  coverage for the gaps report and prunes on history and runtime; mutation
  runs are deferred.
- **TCMS dashboards as Reports.** The `/reports` iframe is script-free
  server-rendered SVG, fine for a daily snapshot; the TCMS wants filters and
  drill-down, which is an app page. A daily `tcms-health` report type can be
  added later with `generator: app:tcms`.

## Deferred (noted, not built)

Mutation testing as a monthly job; `pytest-rerunfailures` for pytest-side
flaky detection (v1 derives flakiness from run history); coverage contexts
per test (the duplicate-coverage prune signal is omitted until they exist);
a vitest unit layer for the web (none exists; the pyramid's e2e layer is
Playwright); a TCMS dashboard tile; a `tcms-health` report type; a
`#qa`-to-engineer auto-assignment rule (the QA assigns by hand in v1);
review-comment re-entry for the QA's PRs; a second GitHub App installation
dedicated to the QA (one App serves both agents; the path policy is
per-agent server-side); a `--device` mobile pass in the walk beyond the 390
width; visual regression diffs against a stored baseline (the walk's PNGs
are per-run evidence, kept as artifacts for 30 days).

## AS BUILT

To be written by the plan's docs task from the ticked tasks and the
implementer reports: deltas from the design above, each forced by a review,
a test or the live run.
