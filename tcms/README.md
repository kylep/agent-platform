# tcms/ — test cases, in git

The platform's test cases live here, one YAML file per suite, reviewed with
the code they cover (design 25). Results never live here: the `tcms` tool
writes them to the `app_tcms` database from the JUnit, Playwright and
coverage files a run produced.

## The format

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

`tools/tcms/cases.py` is the schema's one home; the tool and
`services/backend/tests/test_tcms_cases.py` both load the tree through it.
Caps: title ≤ 160, expected ≤ 2000, ≤ 20 steps, ≤ 10 refs, ≤ 10 tags. A
file with any error is skipped whole and the error is reported as
`(file, key, message)`; the other files still load.

## How a case links to a test

`automation` is a list of refs, paths relative to the repo root:

- `pytest:<file>::<node name>` — copy it from
  `pytest --collect-only -q`, prefixed with the suite's directory
  (`services/backend/`, `services/mcp-broker/`, `services/runner/`,
  `tools/<name>/`, `apps/<name>/backend/`).
- `playwright:<spec>::<title>` — the title exactly as `npx playwright test
  --list` prints it after `›`. A templated title (`` `${path} passes axe` ``)
  cannot be linked; name a literal one.

A case with no refs is `manual`, and only a manual case has none. The
backend test refuses a ref whose function or title is not in the named file,
so a renamed test fails CI here instead of silently unlinking its case.

## How the nightly uses it

The QA agent's `#qa` job runs `tcms sync_cases` (the DB's cases become what
`main` says, absent keys retire), `bin/ap-verify --all`, uploads the
outputs, and `tcms record_results` matches each result's ref to a case's
`automation` entry. A ref no case names is *unlinked*; a case whose refs
matched nothing keeps its last result and shows in `coverage_gaps`. Add a
case beside the test that proves it in the same PR.
