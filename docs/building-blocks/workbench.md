# Workbench

**What:** the run profile a coding agent gets, and the one door its code
leaves through (`docs/design/24-coding-agent.md`). An agent whose row says
`role: dev` is a **dev agent**, and every run it makes is a **dev run**: a
bigger pod on a second runner image with a real shell, an anonymous clone of
the repository on a branch named after its ticket, the whole test toolchain
already installed — and **no git credential of any kind**. The pod cannot
push. When the agent's turn ends, the runner (not the model) commits what is
left, runs the repository's own verifier, bundles the branch and hands it to
the API; the API checks every touched path, pushes, opens or updates the pull
request, moves the ticket and posts a card in its thread. That hand-off is a
**publish**. Merge stays a human's.

The point is that a coding agent can be fed from a [ticket](tickets.md) — text
anybody in the room can write — without holding anything worth stealing. What
a prompt-injected dev agent can do is what any agent can do (speak as itself)
plus one thing: propose code, as a PR somebody reads.

**Lives in:** nothing of its own. The Workbench keeps no table: the
[ticket](tickets.md) names the branch, the branch on GitHub is the durable
state, and a fresh run resumes where the last one stopped by fetching it. Two
run-scoped routes (`GET /api/runs/{id}/workbench`, `POST /api/runs/{id}/publish`),
one Kafka topic (`workbench.events`, every publish and refusal with its real
file list), and one SSE feed over it (`GET /api/workbench/events`) that the
[Changes](changes.md) page reads. The seeded dev agent is the
[engineer](agents.md#seeded-agents), whose home project is `#eng` (prefix
`ENG`).

## What a dev pod holds, and does not

The pod is the image `agent-platform-runner-dev` (`services/runner/Dockerfile.dev`,
built from the repository root): Ubuntu 24.04 with Python 3.12, Node 24,
Playwright's Chromium, a venv at `/opt/venv` holding every service's and
tool's requirements, and a warmed npm cache — so `npm ci` and `pytest` inside
a run pull nothing from the network. Every other agent runs on the lean
`agent-platform-runner` image, which does not change.

It holds what every participant agent holds: the run's platform identity
(the projected ServiceAccount token and the run JWT, grants frozen at launch)
and the run-scoped session token. Both reach only what the agent could
already reach through the broker — Relay, Tickets, the Wiki, quota, artifacts
— plus the two Workbench routes above, and the run's end revokes them.

It does **not** hold a GitHub token (the launcher never mints one for
`role: dev`; `AP_GITHUB_TOKEN` is simply absent from the environment), the
Claude credential (the [claude-proxy](glossary.md#the-workloads) injects that,
as for every run), or any tool secret. The clone is anonymous — the public
HTTPS URL agents-sync already uses — so `git push` from inside the pod fails
at the remote by construction. `$HOME/.gitconfig` carries a `user.name` and
`user.email` for the agent and nothing else.

The cage is the one every runner pod wears (non-root uid 1001, read-only root
filesystem, all capabilities dropped, no privilege escalation, seccomp
`RuntimeDefault`, the ServiceAccount token projected on purpose rather than
auto-mounted). The profile only adds
resources — requests `2Gi`/`500m`, limits `6Gi`/`3` CPUs — and two scratch
volumes: `/workspace` (capped by `dev_workspace_size_limit`, **8Gi**) and a
memory-backed `/dev/shm` (`dev_shm_size_limit`, **1Gi**) for Chromium.
Chromium's own sandbox is off inside the pod (`chromiumSandbox: false` in
`services/web/playwright.config.ts` whenever `AP_WORKSPACE` is set); the pod
is the sandbox.

A dev pod reaches any host on port 443, because every runner pod already can
— see [Security](security.md#the-other-guardrails-briefly). The Workbench
relies on that for the anonymous clone and does not widen it.

## A dev run, start to finish

1. **Summons.** A ticket is assigned to the agent (board drag, `tickets
   assign`, or `@engineer` in a ticket's thread) — the same summons as any
   agent's, through every Relay guard, with the ticket on the run.
2. **Prepare** (`services/runner/workbench.py`, before Claude starts). The
   runner asks `GET /api/runs/{id}/workbench` for its facts — the branch, the
   base, whether the branch already exists on GitHub, the open PR for it if
   any — and receives, on the run's first call only, a one-shot **publish
   nonce** it keeps in memory. It clones `--depth 50`, fetches and checks out
   the existing branch or creates a new one from `main`, runs `npm ci` from
   the warmed cache (a failure is a note on the run, not a failed run), and
   appends a `<workbench>` block to the prompt: branch, base, commits ahead,
   the open PR's number, and four rules — commit as you go, never push, run
   `bin/ap-verify --changed` before you finish, write `.ap/pr.md`. Every value
   in that block is computed by the runner; none comes from the ticket or the
   thread.
3. **Permissions.** Claude Code runs with `--permission-mode acceptEdits`,
   `--strict-mcp-config`, and an allow-list of `Bash`, `Read`, `Edit`, `Write`,
   `NotebookEdit`, `Glob`, `Grep`, the agent's declared harness tools and the
   platform broker's `mcp__platform__*`. The rendered `tools:` line of the
   agent's definition carries the same list, because the CLI reads that line
   as the enabled set. Only the broker is loaded as an MCP server.
4. **Work.** The agent reads the ticket and the branch, posts a plan comment,
   moves the ticket to `in_progress`, edits, commits, runs `bin/ap-verify
   --changed` itself, writes `.ap/pr.md` (its hand-off notes; `.ap/` is
   gitignored) and replies in the thread.
5. **Finalize** (after Claude exits cleanly). The runner commits anything
   still uncommitted as `<agent>: checkpoint at run end`. If the branch is not
   ahead of its base, the run records "no changes" and publishes nothing.
   Otherwise it runs `bin/ap-verify --changed --base origin/main --out
   /workspace/verify --timeout <dev_verify_timeout_seconds>` (**2400 s**, the
   platform's budget for the suites; a timeout or a crash is recorded as such,
   never hidden), bundles `origin/main..HEAD`, refuses to send a bundle over
   `publish_max_bytes` (**16 MiB**), and POSTs the bundle, the verifier's
   `verify.json` and the notes to `POST /api/runs/{id}/publish` with the
   nonce. The result — or the refusal — is a `workbench` frame in the
   transcript, which the run page renders: branch, PR link, or the sentence.
6. **Publish** (`agentplatform/workbench.py`, the one place). Below.
7. **Report.** The agent's last words are in the thread as for every summons;
   the publish card sits beside them with "view run ↗"; the PR is on
   [Changes](changes.md) with its ticket chip and gets the change-summarizer's
   review comment like every platform PR.

A refused publish fails the run, so the run page and the thread both say so.
A run that produced no commits succeeds quietly.

## Publish, and the path policy in plain words

The API — which holds the GitHub App the pod does not — re-derives everything
from the bundle. It fetches the bundle into its own fresh clone, checks that
the bundle names exactly one branch and that its head **descends from** the
branch's current tip on GitHub (or from `main` for a new branch), lists the
changed paths itself, and only then runs the **path policy**, in this order:

1. **The platform deny list wins over everything.** `PUBLISH_DENY_GLOBS` in
   `agentplatform/testpaths.py`: `.github/**`, `.pre-commit-config.yaml`,
   `bin/forbid-secret-files.sh`, `bin/ap-verify`, `bin/ap_verify*`. The CI
   gate, the secret-leak prevention and the verifier stay human-authored, for
   every agent, always; the verifier is on the list because the checkout's
   own copy is what produces `verify.json`.
2. **The agent's push path globs.** When `push_path_globs` on the agent's row
   is non-empty, every changed path must match one of them, and the first
   miss is named in the refusal. Empty means "any path not on the deny list",
   PR only. Non-empty also switches on GitHub auto-merge for the PR — that is
   what the field means: paths the agent may land without review.
3. **Test deletions.** A path matching `TEST_PATH_GLOBS` — the platform's one
   definition of test code: `services/backend/tests/**`, `services/web/tests/**`,
   `services/claude-proxy/tests/**`, `services/*/test_*.py`,
   `apps/*/backend/test_*.py`, `apps/*/backend/tests/**`, `tools/*/test_run.py`,
   `tcms/cases/**` — that is deleted, or renamed away from a test path, is
   refused unless the agent's `may_delete_tests` is true. Net-negative test
   lines are never refused, only flagged (`⚠️ test` in the PR's file table, a
   red chip on the card).

Also refused before any rule: a path containing `..` or starting with `/`, a
symlink anywhere in the tree at the head, more than 200 changed files, a
bundle over the cap. The globs use one matcher for the fence and the deny
list alike: `**` is zero or more whole segments, `*` and `?` never cross a
`/`, matching is case-sensitive and anchored — the semantics GitHub's rulesets
use, so a ruleset on the repository and the platform's fence agree about what
a test path is.

When the policy passes, the API pushes `refs/heads/<branch>` **without
force** — a branch that moved under the agent is a 409 and a "branch moved"
line in the thread, never an overwrite — then opens the PR or updates the
existing one (one PR per branch, never a second), moves the ticket, posts
the card, and publishes the envelope. A refusal is a 422 whose body is one
sentence naming the path, and the same sentence is the `⛔` card in the
thread; nothing was pushed.

**Branches** are named by the API, never by the model: `coder/<ticket key,
lower-cased>` (`coder/eng-12`), or `coder/run-<first 12 of the run id>` for a
run with no ticket; a QA agent's are `qa/<key>`. One branch per ticket, so a
second run on the same ticket fetches the branch, adds commits, and updates
the same PR.

**The PR body** has four parts, in order: a platform header line (agent, run
link, ticket link, branch, commit count — what Changes parses the agent back
out of); a files table (status, path, `+/−`, `⚠️ test` on a removed test);
**Verification (captured by the runner)** — one row per suite with its exit
code, seconds and ✓/✗/`timed out`/`skipped`, the last lines of a failing
suite folded underneath, or "verify did not run: <reason>"; then **`<agent>`'s
notes (agent-authored)** — the contents of `.ap/pr.md`, capped at 32 KiB,
with HTML comments and the platform's own summary marker stripped, so a
hidden comment cannot ride into the PR and the agent cannot forge the
change-summarizer's mark. The title is `<agent>: ENG-12 <ticket title>`,
prefixed `[verify ✗]` when verification failed.

**The ticket** moves to `review` when verification passed (or there was
nothing to run) and to `blocked` with `verify failed: <suite> (exit n)` or
`verify ✗: <suite> timed out` when it did not — as the agent actor, from
the run, so the board's history names the run that did it.

**The card** is an event row in the ticket's thread (or `#eng` for a run with
no ticket), a plain line the Discord mirror can read — `🔀 engineer published
coder/eng-12 → PR #123 · 4 files · verify ✓ backend`, `⚠️ … verify ✗
backend (exit 1)`, or `⛔ publish refused for engineer: <path> is
platform-owned and no agent may change it` — rendered as a chip row: the
branch, `PR #n ↗`, the file count, a red **removes tests** chip when a test
went, `verify ✓/✗`, `view run ↗`. Nothing in it summons anyone.

**The envelope** on `workbench.events` (3 partitions, kept 30 days) is
`published`, `refused` or `verify_failed`, with the agent, the run, the
ticket key, the branch, the PR, every path with its status and line counts
and whether it is test code, the removed tests, and the verifier's per-suite
result. It is the audit: the broker's tool log keeps an argument hash, and a
publish needs its file list on the record.

## `bin/ap-verify`

The repository's own verifier: Python, stdlib only, runs on a laptop too, and
never edits the tree. Its suite table mirrors `.github/workflows/ci.yaml` —
the same commands in the same directories — so a green here is a green there.
Three callers: the agent mid-run, the runner at finalize, and you before a
push.

`--changed` (the default) diffs against `--base` (`origin/main`) and runs the
suites whose globs the changed paths touch — `services/backend/**` and `sdk/**`
→ the backend suite (with JUnit and coverage reports when `--out` is given);
`services/mcp-broker/**`, `services/mcp-facade/**`, `services/tool-executor/**`,
`services/runner/**`, `services/connector-discord/**` → that service's pytest;
`services/web/**` and `packages/ui/**` → lint, tokens, build, Storybook,
Playwright; `apps/<x>/backend/**` and `apps/<x>/frontend/**` → theirs;
`tools/<x>/**` → its `test_run.py` and the executor suite; `charts/**` → `helm
lint`; `docs/**` → nothing. `--all` runs everything; `--list` prints the
table and what would run, without running; `--out <dir>` is where the
reports and `verify.json` land (default `.ap/verify`); `--timeout <s>` is per
suite (**900** unless given — the runner passes the platform's 2400);
`--skip <suite>` leaves one out, recorded as skipped. A suite whose tool is
missing (`helm`, `docker`, `npm`) is *skipped*, not failed; a suite that times
out or crashes *is* a failure. Every suite runs with the pod's `AP_*` and
`KUBERNETES_*` variables scrubbed from its environment (only `AP_VERIFY_PYTHON`
and `AP_WORKSPACE` survive), so the platform's own tests do not read the pod's
settings.

`verify.json` is `{ok, base, head, changed, suites: [{name, cmd, cwd, exit,
seconds, skipped_reason, tail}], files}`; `ok` is true iff no suite that ran
failed, and the exit status mirrors it. The runner ships that file with the
publish, and the PR's verification table is rendered from it — the agent's
claim never is.

## `quota_ok`

A dev run is the most expensive thing the platform does, so it asks first.
`quota_ok` (`mcp__platform__quota_ok`, a broker tool granted explicitly — no
default-grant sweep; the engineer holds it) calls `GET /api/quota/ok`, which
reads the cached [quota](quota.md) snapshot (one coalesced refresh if it is
stale), takes the thresholds from the calling agent's own row —
`quota_5h_max_pct` and `quota_7d_max_pct`, defaults **80** and **50**, the
column defaults for a human caller — and answers `{ok, five_hour_pct,
seven_day_pct, five_hour_max_pct, seven_day_max_pct, stale, reason}`. The
tool returns that JSON on one line and then a sentence, so the model's
decision is a field, not a reading of prose. A snapshot with no reading yet
is `ok: false`. The engineer's prompt says: call it first, and when it says
no, say so in one line in the thread and stop before cloning anything.

## Making another dev agent

A dev agent is an ordinary [agent](agents.md) row with `role: dev`. The New
Agent wizard or `POST /api/agents` with `role: dev` is enough: the role
selects the profile (the image, the shell, the clone, the publish) and
grants no API scope of its own. Give it `tickets` and `relay` so it can be
summoned and can answer, `quota_ok` if it should ask before it works, and
`Glob`/`Grep` as harness tools — the shell tools come with the profile, not
the grant, and are not declarable. Then set the four Workbench fields:

- `push_path_globs` (a **grant** field — `agents_grant` or the admin session):
  empty for "PR only, any path"; a list of globs for "these paths, and land
  them on green" (design 25's QA agent is fenced to test paths this way).
- `may_delete_tests` (a grant field): `false` unless you mean it.
- `quota_5h_max_pct`, `quota_7d_max_pct` (**edit** fields — `agents_edit` or
  the admin session): what `quota_ok` measures the agent against.

Set its `timeout_seconds` to fit a clone, a change and a verify (the engineer
has 5400), and leave `concurrency` at 1 so two runs never share a branch.
Assign it a ticket and it works.

## Changing the engineer's thresholds and globs

On `/agents/engineer` → Config: the two quota fields sit beside Timeout
("Quota gate: 5-hour max %" / "7-day max %"), and the grants panel has **Push
path globs** (one per line) and **May delete tests**, enabled for whoever may
grant. From an agent or the laptop MCP, `agents_edit` changes the quota
fields and `agents_grant` the two grant fields — both merge into the row.

One thing to know about the raw API: `PUT /api/agents/engineer` replaces the
**whole** definition. A body of `{"quota_7d_max_pct": 1}` resets every other
field to its default (role to `operator`, grants to none, the prompt to
empty). Send the full definition from `GET /api/agents/engineer`, or use the
editor and the tools, which do.

## The guards, in plain words

- **The pod cannot push.** No token, an anonymous clone, and a remote that
  refuses unauthenticated writes. Nothing the model runs in its shell changes
  that.
- **The runner publishes, not the model.** The publish happens when the turn
  ends, if the branch is ahead — the model cannot skip it, and it cannot forge
  it either: the publish route wants the nonce the runner received before the
  model existed, so a shell that reads the session token out of the
  environment still cannot POST a `verify: {ok: true}` of its own.
- **The API decides what a change is.** It lists the paths from the bundle
  in its own clone; the runner's `head_sha` and the agent's notes are never
  the evidence. Deny list, then globs, then test deletions; the first refusal
  names its path and stops everything after it.
- **Never force.** A push is `refs/heads/<branch>` without `+`; a moved
  branch is a 409, not an overwrite. One PR per branch, updated in place.
- **Evidence is captured, not claimed.** The verification table is
  `ap-verify`'s recorded result, run by the runner after the model's turn; CI
  on the PR is the second, clean-checkout run; a human reads both.
- **Untrusted text stays untrusted.** The ticket, the thread, the wiki and
  every file the agent did not write are data — the prompt says so — and the
  `<workbench>` block is the runner's own voice, built from computed facts
  only. HTML comments are stripped from the agent's notes before they reach
  the PR.
- **Budgets.** `timeout_seconds` on the row, `--max-turns` (`dev_max_turns`,
  **200**), 2400 s for the verifier, one publish per run, `quota_ok` at the
  top, concurrency 1.

What a dev run does *not* protect against is what it pulls in from the
network during `npm ci` or a `pip install` the agent decides to run: the
supply-chain exposure of any developer laptop, bounded by the fact that the
result is a PR.
