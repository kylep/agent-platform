# TCMS

**What:** the test case management system behind the [QA agent](agents.md#seeded-agents)
(`docs/design/25-qa-agent-and-tcms.md`). It answers two questions: what are the
platform's tests supposed to prove, and how are they doing. A **case** is one
behaviour written down — a key, a title, a layer, steps, the expected outcome —
and linked to the automated tests that prove it. A **run** is one recorded
execution of the suites; its **results** are the pass/fail of each test in it.

The case catalogue and the results are kept apart on purpose. Definitions are
reviewed with the code they cover; results are measurements and are never typed
by anybody.

**Lives in:** two places. Case definitions are YAML in git, under
`tcms/cases/` (one file per suite). Runs, results and coverage snapshots are
Postgres, in the `app_tcms` schema owned by the [app](apps.md) at `apps/tcms/`.
The database also holds a mirror of the cases, refreshed from `main` by
`sync_cases`.

## A case

One file per suite, named after the suite: `tcms/cases/relay.yaml` holds
`suite: relay`. The suites today are `apps`, `artifacts`, `quota`, `relay`,
`tickets`, `tools`, `wiki` and `workbench`.

```yaml
suite: relay                     # == the filename stem; keys are "<suite>.<slug>"
area: relay                      # the building block (glossary name)
cases:
  - key: relay.ping-pong-stops   # ^[a-z0-9-]+\.[a-z0-9-]+$, unique across all files
    title: Two agents mentioning each other stop at the hop cap
    layer: integration           # unit | integration | e2e | manual
    priority: p0                 # p0 (a loop guard) .. p3 (a cosmetic)
    preconditions: [cooldown off so the hop counter is the only fence]
    steps: [ada mentions bob, bob mentions ada, repeat past the cap]
    expected: runs stop at max hops and the suppression is said once
    automation:
      - pytest:services/backend/tests/test_relay_router.py::test_a_ping_pong_stops_after_max_hops
    tags: [router, loop-guards]
    tickets: []                  # QA-n keys, for provenance
```

`tools/tcms/cases.py` is the schema's one home. The tool and
`services/backend/tests/test_tcms_cases.py` both load the tree through it.
Limits: title 160 characters, expected 2000, 20 steps, 10 refs, 10 tags. A file
with any error is skipped whole and the error is reported as
`(file, key, message)`; the other files still load. `tcms/README.md` has the
same format reference beside the cases.

## How a test links to a case

A case's `automation` is a list of refs, paths relative to the repo root:

- `pytest:<file>::<node name>` — the node name from `pytest --collect-only -q`,
  with the suite's directory in front (`services/backend/`,
  `services/mcp-broker/`, `services/runner/`, `tools/<name>/`,
  `apps/<name>/backend/`).
- `playwright:<spec>::<title>` — the title exactly as `npx playwright test
  --list` prints it after `›`. A templated title cannot be linked; name a
  literal one.

A case with no refs is `manual`, and only a manual case has none. The backend
test refuses a ref whose function or title is not in the named file, so
renaming a test fails CI instead of silently unlinking its case.

When results are recorded, each result's ref is matched to a case's
`automation` entry. A ref no case names is **unlinked**. An automated case
whose refs matched nothing keeps its last result and shows up in
`coverage_gaps`.

## What the nightly does

A platform job, `qa-nightly`, posts into `#qa` at 02:00 America/Toronto:
"@qa — run the nightly: sync cases, run everything, record the results, fix or
file what you find, and leave a note here." The QA's prompt
(`QA_PROMPT` in `services/backend/agentplatform/db.py`) then has it:

1. `tcms(action="sync_cases")` — the DB's cases become what `main` says.
2. Run `bin/ap-verify --all --out /workspace/verify`.
3. `bin/ap-upload` the JUnit, Playwright JSON and coverage files, then
   `tcms(action="record_results", files=[<artifact ids>])`.
4. Read `runtime_report`, `flaky`, `coverage_gaps` and `prune_candidates`.
5. Fix each finding in test code, or open a `QA-n` ticket for it; product-code
   problems go to `agent:coder`.
6. Run `bin/ap-verify --changed`, write `.ap/pr.md` naming every test it
   deleted and why, and end with a note in `#qa`.

The QA may change only test paths and `tcms/cases/`; see
[Workbench](workbench.md) for the path policy. Its PRs are published by the
platform, and a human merges.

## The dashboards

At `/apps/tcms/`, four pages:

| Page | What it shows |
|---|---|
| Overview | the test pyramid (count and seconds per layer), pass rate over recent runs, line coverage and its trend, the latest run, and four attention counts: failing now, flaky, unlinked cases, prune candidates |
| Runs | every recorded run; each opens to its own page (`/runs/<id>`) |
| Cases | the catalogue, filterable by search text, layer, area, status and automation, with an unlinked-only switch; each case opens to its steps and the last results of each ref |
| Health | slowest tests (average over the last 30 runs), flaky tests, and prune candidates with the reason |

The app's API is `GET`-only under `/apps/tcms/api/`. A 30-second reconciler in
the app announces each newly recorded run once: an `app.tcms.run.recorded`
envelope on Kafka and a one-line note in `#qa`. Once a day it deletes runs
older than `TCMS_RETENTION_DAYS` (default 90) with their results and coverage.

## The `tcms` tool

`tools/tcms/` is a custom [tool](tools.md); it is the only writer of the app's
`test_runs`, `results` and `coverage_snapshots` tables (the app owns the DDL).
Reads answer in lines of at most 4 KiB, with "showing N of M".

| Action | What it does |
|---|---|
| `sync_cases` | loads `tcms/cases/*.yaml` from the synced checkout; keys no longer present retire; returns counts and validation errors by file |
| `record_results` | ingests uploaded report files (below); returns the run id, totals by status, linked and unlinked counts |
| `cases` | lists cases; filters `layer`, `area`, `status`, `q`, `unlinked`, `limit` (default 40, max 200) |
| `case` | one case by `key`, with its refs and their last 10 results |
| `coverage_gaps` | packages under `threshold_pct` (default 70) line coverage, manual cases, refs no case names, automated cases with no result in the latest run |
| `runtime_report` | seconds by layer over the last `runs` runs (default 10), the trend, and the slowest refs |
| `flaky` | refs that failed then passed on one commit or alternate across commits, over `runs` (default 20) |
| `prune_candidates` | refs the QA may retire, with the reason, over `runs` (default 50) |

## How results get in

`record_results` is the only way, and it accepts no counts or statuses as
arguments. The path is:

1. `bin/ap-verify --all --out <dir>` writes the report files.
2. `bin/ap-upload FILE...` uploads each file as an [artifact](artifacts.md)
   with the run's own identity and prints one id per file, in order.
3. `tcms record_results files=[<ids>] commit_sha=<sha>` (plus `branch` and the
   `verify` object from `verify.json`). The broker fetches the artifacts into
   the tool's input directory and the tool parses them.

At most four files go in one call. The tool accepts only pytest JUnit XML,
Playwright's JSON report and a Cobertura `coverage.xml`; any other file is
refused by name and nothing is recorded. Playwright's own JUnit output is
refused too, so results are not counted twice. Recording the same run twice is
idempotent.

**Why the agent cannot type results.** A result that comes from the agent's
arguments is an assertion; a result that comes from a file a test runner
wrote, carried as an artifact and parsed by the tool, is a measurement. No file
path from model text is ever opened by the tool, and the run's `agent` and
`run_id` come from the executor's environment, not from the call.

## Adding a suite

1. Create `tcms/cases/<suite>.yaml`. `suite:` must equal the filename stem and
   match `^[a-z0-9-]+$`; set `area:` to the building block.
2. Write each case with a unique `<suite>.<slug>` key and an `automation` ref
   for every test that proves it. Copy refs from `pytest --collect-only -q` or
   `npx playwright test --list`.
3. Run `services/backend/tests/test_tcms_cases.py`; it rejects a ref that names no
   test, a key outside the suite prefix or a malformed file.
4. Put the case file in the same PR as the test that proves it.

The next `sync_cases` loads it; the next recorded run links its results.
