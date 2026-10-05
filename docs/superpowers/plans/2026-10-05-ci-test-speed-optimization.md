# CI and test speed optimization loop (open-ended, exits on diminishing returns)

Goal: keep cutting PR wall time and local test time until nothing worthwhile is left.
Owner's tradeoff: a *marginal* rise in skipped-test risk for a *major* speed gain; no more.
Owner is annoyed by slowness and by token spend: this loop must be cheap.

## Scope (hard limits)

IN: `.github/workflows/**`, `services/backend/tests/**` (fixtures, conftest, config), dev
dependencies in `services/backend/pyproject.toml`, `.test_durations`, test-time-only settings.
OUT, never: production code under `services/*/agentplatform/**` (needs a deploy), deleting or
weakening tests or security checks, per-test selection as a CI gate (pytest-testmon stays out;
a *non-gating* shadow comparison is allowed), turning on branch protection, `--admin` merges,
force-pushes, changing quota/model config. If a candidate needs an OUT item, write it under
"Handoff" and move on.

## Baseline (record, do not edit)

2026-10-05, main f1cf9d9, measured on real runs: full run 3m17s wall. Jobs: backend shards 176s
/142s, backend-extras 27s, web 34s, web-ui shards 85-118s, mcp-facade ~40s, secret-scan ~40s,
security-scan ~50s (off critical path). Backend-only PR ~2.5-3m, web-only PR ~2m. Local full
backend suite 76-83s on 8 cores (3032 tests). Already done: shared test DB template, xdist,
cheap argon2 in tests, job-level path filters, ci-ok, 2 backend shards + extras job, web-ui x3
shards, nightly + full-on-main. Known remaining costs: per-test `create_app` ~0.09s (YAML
manifest parse, ~0.065s of it), `pip install` + `npm ci` in every job and shard, web build
repeated in each web-ui shard, run-to-run runner variance on shard 1.

## Worthwhile means (mechanical, no judgement)

A candidate is WORTHWHILE only if ALL hold:
1. Estimated saving >= 15s on the critical path of some PR class (backend-only, web-only, mixed,
   full) OR >= 10s of the local full backend suite; AND
2. Risk is "none" or "low": it removes no assertion, skips no test that ran before, weakens no
   gate, and is reversible by reverting one PR; AND
3. It is in Scope.
Everything else is recorded under "Rejected" with the reason. Do not re-propose a rejected item.

## Loop protocol (read this every tick, follow it exactly)

You are the **orchestrator**. You do not write product or CI code yourself. You dispatch,
verify evidence, merge, and update this file. Be thin: every request resends your context.

0. Cost gate first. Call `mcp__ap__quota_ok`. If `ok` is false, or `seven_day_pct >= 46`
   (cap is 50), stop the loop: PushNotification "optimization loop paused on quota", then
   ScheduleWakeup `stop: true`. Do not resume it automatically.
1. Re-read this file. Find the first `- [ ]` task in "Tasks". If none, go to step 8 (Discovery).
2. Make a worktree off fresh `origin/main` (`git worktree add /Users/kp/gh/ap-opt-<tn> -b opt/<tn>
   origin/main`). Never edit the main checkout.
3. Dispatch **one sonnet implementer** (`model: "sonnet"`, `subagent_type: "general-purpose"`;
   never opus/fable/default) with: the task verbatim, the "Ground rules" block verbatim, and the
   worktree path. It must report exact commands and summary lines. One agent per task.
4. **Verify yourself, cheaply, before opening a PR:** run the commands in "Ground rules → local
   checks" that apply to the diff (yaml parse, drift test, the affected test files). Run the
   full backend suite exactly once per PR, in the background, `| tail -3`, only if the diff
   touches `services/backend/tests/**` or `pyproject.toml`.
5. If the diff touches workflow *gating* (the `changes` job, filters, `needs`/`if`, `ci-ok`),
   dispatch **one sonnet reviewer** with the diff and this framing: "list every way a job could
   be silently skipped or a failure silently ignored; give each a worst case". High findings go
   back to the same implementer via SendMessage. Otherwise no reviewer.
6. Commit with `git add <file>` by name (never `-A`), message ends with the session's
   attribution trailer. Push, open a PR, wait for CI in the background (until-loop on the run
   status, never a foreground sleep). Merge with a normal squash merge ONLY when every check
   passes. If CI is red, give the failing step's name and tail to the implementer via
   SendMessage, at most 2 repair rounds, then close the PR, mark the task `- [!]` with one
   paragraph, and move on.
7. **Measure.** From the first main run after the merge, compare the affected job's duration
   with the Baseline. Record it under "Measurements". If the real gain is under half the
   estimate, or the job got slower, open a revert PR (same CI rules) and mark the task
   `- [!] reverted`. Tick `- [x] **Tn** (commit <hash>, est <a>s, measured <b>s)`, remove the
   worktree and branch. Then ScheduleWakeup (`delaySeconds: 60`, `noop: false`, the loop
   sentinel prompt). One task per tick.
8. **Discovery** (only when no `- [ ]` task remains). Dispatch **one sonnet analyst** with:
   Baseline, Measurements, Rejected, Scope, "Worthwhile means", plus its inputs: the last 5
   main CI runs' per-job and per-step timings (`gh api .../actions/runs/<id>/jobs`) and, for
   local time, `pytest --durations=30 -n 8` output from one run. It returns at most 8
   candidates as: title, mechanism, estimated saving, which PR class, risk, files. The
   orchestrator keeps only WORTHWHILE ones, appends them as new `- [ ]` tasks, records the rest
   under "Rejected" with the reason, and updates the counter:
   - >= 1 worthwhile candidate: set `Empty discovery rounds in a row` to 0, schedule a wakeup.
   - none: increment it. At 2, the loop is done (step 9). Otherwise schedule a wakeup.
9. **Exit.** Run "Definition of done". If it passes: edit this file's status line, commit the
   plan through a PR (docs-only, normal merge), PushNotification one line with the outcome
   (final baseline vs now per PR class, tasks done/reverted/rejected counts), then
   ScheduleWakeup `stop: true`. If it fails, add a task under "Repairs" and continue.
10. Failure handling: a quota 429 kills a subagent instantly (it wrote nothing): relaunch it.
    A stalled agent keeps its context: SendMessage "resume from X". If the auto-mode classifier
    refuses a Bash action, never retry variants: write the exact commands under "Handoff",
    continue with independent tasks, and PushNotification if nothing independent remains.
    Plan state lives in this file in the main checkout (uncommitted until step 9, because every
    push to main triggers a full CI run); never `git stash` or reset that checkout.

## Ground rules for implementers (paste verbatim into every implementer prompt)

- Repo: ~/gh/agent-platform. Work ONLY in the worktree you are given. Never touch the main
  checkout, other worktrees, or `.test_durations` unless the task says so.
- Venv for tests: `~/gh/agent-platform/services/backend/.venv/bin/python` (3.12; do not install
  into it without saying so). Backend tests: `cd services/backend && <venv python> -m pytest
  <files> -q -p no:cacheprovider -n 8`. Never run the full suite; the orchestrator does.
- Local checks that mirror CI, run the ones the diff touches: workflow yaml parses
  (`<venv python> -c "import yaml;yaml.safe_load(open('.github/workflows/ci.yaml'))"`),
  `tests/test_ci_path_drift.py`, `<venv python> sdk/regenerate.py && git diff --exit-code sdk/`
  if the API changed (it must not here), `helm lint` if charts changed (they must not).
- CI facts: backend job = 2 pytest-split shards (`--splits 2 --group N`, `.test_durations`) +
  an `extras` matrix entry; `changes` job filters PRs; `ci-ok` aggregates; web-ui is 3
  Playwright shards. A change to `.github/**` makes CI run every job.
- Never remove or weaken a test, an assertion, a security scan, or a gate. Never widen scope.
  Never commit, push, or open PRs: report the diff and the evidence; the orchestrator ships it.
- No sleeps longer than a few seconds; wait with `until <cond>; do sleep 2; done` in the
  background. Temp files go in the session scratchpad, never `/tmp`.
- Report format (max 15 lines): files changed, exact commands run with their final summary
  lines, the estimated saving and how you derived it, any risk you could not rule out.

## Tasks

- [x] **T1 Backend 3 shards.** (commit f56328e, est 25-45s, measured ~48s: slowest backend shard 149s -> 101s)
- [!] **T2 Cache the tool-manifest YAML parse in test fixtures.** NOT WORTH IT: measured twice, -n 8 full suite wall 78.9/77.1s -> 69.0/69.7s (-9.9s, -7.4s) and cpu 451.9/439.2s -> 403.9/416.2s (-10.6%, -5.2%); the bar was >=10% cpu or >=10s wall on BOTH runs. Real but below the bar and noisy; edit reverted, nothing shipped.

- [x] **T3 Weighted web-ui Playwright sharding.** (commit 1b50859, est 15-22s, measured: slowest Playwright step 76s -> 54s on the PR run, 63s on the first main run; shards 50/63/48s)

## Repairs

(empty)

## Deferred

(empty)

## Rejected (do not re-propose)

- pytest-testmon as a CI gate: four-model review, majority against; skip risk above "marginal".
- Dropping the medium-confidence tests from the Opus review: ~9s total, judgement-heavy.
- uv instead of pip (~11-14s per shard, under the 15s bar; revisit if installs grow).
- Refresh .test_durations alone (~5-9s, within runner noise).
- Playwright workers=4 (est 10-25s but risk is low-med: more flake with vite preview; fails the risk rule).
- Share one web build across web-ui shards (~3-6s after upload/download cost).
- Cache apt/playwright system deps (0-15s, unproven, unreliable cache).
- 4 web-ui shards (0-15s, shard variance larger than the gain).
- YAML-parse cache in test fixtures (T2): gain ~8s wall / 5-10% cpu locally, below the 10s/10% bar on both runs. Revisit only if per-test create_app cost grows.
- Trim the `changes` job or `ci-ok` (1-2s and 0s; removing `needs: changes` weakens gating).
- Split openapi-python-client/ruff out of the backend `dev` extra (~3-5s, unmeasured guess).
- Skip Playwright install-deps or use system Chrome (~20s on web-only PRs) because it needs `playwright.config.ts` (out of Scope), changes the browser under the axe/smoke gates (risk not low), and is brittle against runner image changes.
- Larger paid runners: out of Scope (repo is public, default 4-core runners).
- Production-code speedups (e.g. CSafeLoader in `toolregistry`): needs a deploy; out of scope.

## Measurements

- Discovery round 1 (2026-10-05, 5 main runs): run-to-run variance on test steps is about +-20s; savings under that are not measurable per run. Critical path: backend-only 149s (shard1: install 23 + tests 118), web-only 98-123s (web-ui shard1), full ~150s.

- T1 (main run after f56328e): backend shards 81/87/101s + extras 27s (baseline 149/137s); critical path is now web-ui shard 1 at 122s (80-122s across shards, +-20s run variance). Observation for Discovery: shards differ by ~20-30s despite 0.3% balanced recorded durations, so fixed per-shard cost (xdist workers each collect all 3037 tests, install ~23s) dominates; a 4th shard would not help.

- T2: not shipped, see Tasks.

- Discovery round 2 (post-T1): per-shard fixed cost (xdist startup + collection) is only ~5-7s locally (~4-6s on 4-core CI), not actionable. Backend per shard = install 14-20s + tests 59-73s. web-ui imbalance is structural (equal-count split puts all axe tests in shard 1), see T3. After T3 the backend shards (~101s) become the floor; remaining backend levers are all under the bar.

- T3 (main run after 1b50859): Playwright steps 50/63/48s (baseline 76/39/46s); web-ui jobs 82-111s; backend shards 115/121/103s + extras 39s; whole run 2m17s wall (16:40:55 -> 16:43:12). Critical path is now the backend shards (+-15s run variance). Cumulative: full run 3m17s (loop start) -> ~2m17s.

- Discovery round 3 (empty): no candidate reaches 15s on a PR class critical path or 10s locally. Backend shard step times are runner noise (tests 59-87s, install 21-24s, everything else <2s per shard), not structural. `changes` job ~5s total, ci-ok ~3s.

- Discovery round 4 (local dev loop, in-Scope result: empty). Measured on a scratch worktree: `bin/ap-verify --changed` backend-only 408.8s (serial, no xdist; the same suite with `-n 8 --cov` ran in 113s; 66-83s without --cov); web-only 62.4s (lint 0.4, tokens 0.3, build 4.3, storybook 2.4, playwright 54.5). Local full suite is a flat per-test floor (~0.15s each: create_app ~0.09s + DB copy), slowest 12 tests 1.8-4.4s, top ~25 are ~6s of wall on 8 workers: no fixture/config lever reaches 10s.
- FINAL COMPARISON (loop start main f1cf9d9 -> end main): full CI run 3m17s -> 2m17s wall (16:40:55 -> 16:43:12). Backend: slowest shard job 176s/149s -> 101-121s (T1, 3 shards). Web-ui: Playwright steps 76/39/46s -> 50/63/48s (T3; jobs 82-111s). Backend-only PR ~2.5-3m -> ~2m. Web-only PR ~2m -> ~1m50s. Shipped T1, T3; T2 measured and rejected. Earlier in the session (outside this loop): 600s serial local suite -> ~66-83s on 8 workers; CI 10-20 min -> 2m17s.

## Handoff to Kyle

1. **bin/ap-verify runs the backend suite SERIALLY** (no `-n`): `python -m pytest -q --timeout=120 tests [--junitxml --cov=agentplatform ...]`. Measured: 408.8s for a backend-only `--changed` vs 113s with `-n 8 --cov` (3034 passed) and 66-83s without --cov. This is the largest remaining win and every consumer pays it (engineer agent mid-run `--changed`, the runner's end-of-run verify.json, laptops). Proposed change, NOT made because `bin/ap-verify` is outside this loop's Scope and is platform-owned (`PUBLISH_DENY_GLOBS`; its output is evidence that rides along with an agent publish): in `suite_table()` add `-n auto` to `backend_cmd` when `has_module(py, "xdist")`, else stay serial. Risk low-med: a dev pod with few cores or little RAM may gain less or oversubscribe; junit/cov under xdist were verified to work. Second, smaller option: drop `--cov` from the default (non-`--out`) local command, ~30-35s with `-n 8`. Both need your decision.
2. Playwright `install-deps` is ~15s x 3 shards and could go away with system Chrome (`channel: 'chrome'`), but that changes the browser under the axe/smoke gates and edits `playwright.config.ts`: rejected here, your call if you want it.
3. Branch protection still does not exist; `ci-ok` is the one check to require when you turn it on.
4. The `PWTEST_SHARD_WEIGHTS=51:166:149` setting (T3) is hardcoded for 366 tests and PWTEST_-prefixed (semi-internal): re-check it after any Playwright bump or when specs are added; drift only costs balance, never coverage.

## State

Empty discovery rounds in a row: 2
Discovery rounds done: 4 (round 1: 2 worthwhile; round 2: 1 worthwhile = T3; round 3: empty; round 4: empty in Scope, one out-of-Scope finding in Handoff)
Status: DONE 2026-10-05. T1, T3 shipped; T2 not worth it; Discovery rounds 3 and 4 empty (2/2). See Handoff.

## Definition of done (mechanical)

All of: (a) no `- [ ]` task remains (each is `[x]` or `[!]`); (b) `Empty discovery rounds in a
row` is 2; (c) the latest main CI run is green; (d) no open `opt/*` branch or `ap-opt-*`
worktree is left; (e) the final comparison is recorded in "Measurements". The loop's own
opinion that "this is fast enough" never ends it; only the counter does.
