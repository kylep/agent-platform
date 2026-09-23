# Agents

**What:** the unit of work — an agent the platform runs through Claude Code or
OpenAI Codex in an isolated pod.

**Lives in:** Postgres, one row per agent (`agent_defs`). An agent's identity
— prompt, config, grants, entrypoints — is a row, not a file: it is mutable
through the admin API/UI and edits apply immediately, no PR round-trip. This
is deliberately different from every other building block, which is
git-declared and rides the [change loop](changes.md); see
[design-15](../design/15-db-first-agents.md) for why identity moved to the
database while capability (tools, skills, secret declarations) stayed code.

**Fields** (`GET /api/agents/<name>`, all but `name` optional on write):

```yaml
prompt: You are ...          # the agent's context/personality (was agent.md's body)
description: One line for listings.
runtime: claude                # claude | codex
model: sonnet                  # runtime model override; empty = platform default
role: operator                 # execution profile; see below
system: true                   # platform-managed lifecycle; not deletable
responds_to_all: false         # excluded from room-wide mentions; direct mentions still work
can_invoke: true               # may trigger other agents (depth-guarded)
enabled: true                  # false = no new runs from any trigger (409)
concurrency: 1                 # maximum parallel runs of this agent; minimum 1
timeout_seconds: 1800          # wall-clock run limit including pod startup; minimum 1
result_topic: ""               # successful final result destination; blank = no event
transcript_retention_days: null
harness_tools: [WebFetch]                    # Claude Code built-ins (ignored by Codex); the sensitive
                                              # set (Bash/Read/Edit/Write/NotebookEdit)
                                              # stays hard-denied regardless
platform_tools: [mcp__platform__memory]      # mcp__platform__* grants
skills: [git]                                # mounted into the pod; their secrets get bound
secrets: [my-secret]                         # extra direct secret bindings
entrypoints: {crons: [], webhooks: [], topics: [], timezone: ""}   # see entrypoints.md
push_path_globs: []                          # GRANT: paths a dev agent may land without review
                                              # (empty = any non-denied path, PR only; see workbench.md)
may_delete_tests: false                      # GRANT: a publish that deletes a test file is refused otherwise
quota_5h_max_pct: 80                         # `quota_ok` says no above these (the agent's own thresholds)
quota_7d_max_pct: 50
```

New agents receive `mcp__platform__memory` by default, regardless of runtime.
It reads and writes only that agent's private namespace; granting the tool
does not make the agent remember automatically. The New Agent wizard starts
with **Memory** checked. Uncheck it to opt out, or remove the grant in the
agent editor later. API clients can send `memory: false` on `POST /api/agents`;
updates remove the name from `platform_tools`. The initial backfill grants
memory to existing agents once, so a later opt-out survives restarts.

The last four are the [Workbench](workbench.md) fields. They exist on every
row but only mean something for `role: dev` (the two globs/tests fields) or
an agent that holds `quota_ok` (the two thresholds). `role: dev` joins no
endpoint allow-list — it is the run-profile rung, not an API scope.

## Editor terminology and execution model

The editor separates **how a run executes** from **what it may access**:

| Editor setting | Stored field | Meaning |
|---|---|---|
| Runtime | `runtime` | Claude Code or OpenAI Codex. It chooses the CLI and model catalog. |
| Model | `model` | The default model within that runtime. Invocation entrypoints may override it for one run. |
| Execution profile | `role` | The pod shape and publishing workflow. It is not the run's API role. |
| App output topic | `result_topic` | Kafka destination for the successful final result. It is frozen when the run launches. |
| Run time limit | `timeout_seconds` | Hard wall-clock limit, including pod startup. Queued time is outside the limit. |
| Parallel runs | `concurrency` | Per-agent dispatched/running limit. Extra work remains queued; the global cap may be lower. |
| Transcript retention | `transcript_retention_days` | Detailed event retention; blank uses the platform default. |

An execution profile chooses the workspace around the model. It does not
change the runtime, model, platform-tool grants, secrets, or invocation
authority.

| Behaviour | Standard agent (`operator`) | Workbench developer (`dev`) |
|---|---|---|
| Intended work | Chat, research, schedules, image generation, and platform-tool calls | Editing, testing, and proposing changes to this repository |
| Pod image | Lean runner | Development runner with Python, Node, Playwright, browsers, and project dependencies |
| CPU | 250m requested; 2 cores maximum | 500m requested; 3 cores maximum |
| RAM | 1 GiB requested; 3 GiB maximum | 2 GiB requested; 6 GiB maximum |
| Startup | Starts the selected CLI in an empty scratch workspace | Clones the repository, checks out the ticket branch or a new branch, and prepares dependencies before starting the CLI |
| Repository | No checkout | Anonymous checkout with no GitHub credential |
| Editing and tests | No repository workflow | May edit and commit; receives the repository's test and browser tooling |
| End of run | Saves the transcript and delivers the final result normally | Commits leftover changes, runs the repository verifier, and sends a bundle to the platform API |
| GitHub result | None | The API checks the bundle and opens or updates a pull request; the agent itself cannot push or merge |
| Cost and latency | Lower pod overhead | Higher startup, CPU, RAM, and verification time |

Workbench publishing has two additional controls:

- **Auto-merge paths** is blank by default. The platform still opens or
  updates a pull request, but it waits for human review. When path globs are
  present, the platform enables GitHub auto-merge only if verification passes
  and every changed file matches one of those globs. A non-matching file leaves
  the PR waiting for review. Platform-protected files are always refused.
- **Permit test-file deletion** is off by default. If a handoff deletes a test
  or renames it out of a test path, the platform refuses the whole handoff
  before updating the PR. Turning it on permits the deletion; the ordinary
  review or auto-merge rules still apply.

See [Workbench](workbench.md) for the full publishing and verification flow.

Old definitions may contain `reader` or `annotator`. They execute like a
standard agent and remain editable, but the editor does not offer them for new
definitions. Those words are meaningful for human/API keys, not agent run
profiles.

The retired `coder` profile is rejected on write and cannot launch. On upgrade,
old rows are moved to Standard and disabled for an administrator to review;
they are never promoted to Workbench implicitly. The editor also labels an
unmigrated value as retired rather than silently displaying another profile.

Authority comes from **Grants**. Platform tools work in both runtimes and the
launcher derives the run token's least-privileged API role from them.
**Can invoke other agents** separately adds run-launch authority. Skills and
secrets also work in both runtimes. **Claude Code tools** are CLI built-ins and
apply only to Claude; Codex capabilities come from its runtime plus the shared
skills and platform tools. Provider credentials remain behind the runtime
proxies and cannot be selected as ordinary secrets.

Entrypoints are input routes: built-in schedules, webhooks, and Kafka input
topics. **App output topic** is the separate output route. Kafka names accept
letters, numbers, `.`, `_`, and `-`, up to 249 characters. A successful result
is published as an `agent.result` event containing `run_id`, `agent`, and
`result`; failed runs do not publish there.

**Readiness (derived, never declared):** an agent's secret dependencies are
computed from `secrets` plus each of its skills' declared secrets. An unmet
*required* dependency makes the agent **blocked** — runs are rejected before a
pod launches, with the exact reason recorded as a failed Run. *Blocked* (fix
the secret) is distinct from *quarantined* (the row fails validation — an
unknown skill/tool/secret, a bad cron, a bad role — fix the definition).

## RBAC: editing vs. granting

Two platform tools split write authority, so a definition being editable does
not mean it is grantable:

- **`agents_edit`** — create/update/delete a definition's prose and config
  (prompt, description, model, entrypoints, timeout, the two quota
  thresholds, …). Cannot touch any grant field.
- **`agents_grant`** — assign/revoke `harness_tools`, `platform_tools`,
  `skills`, `secrets`, `can_invoke`, `push_path_globs`, `may_delete_tests`,
  and `role` on any agent. This is the
  escalation-capable tool: granting it is granting the keys to every agent's
  capabilities, including its own.

The admin session always has both implicitly. An agent has either only if
granted — same as any other platform tool — and every call is attributed to
the calling agent (never self-reported) in the change log below. **No agent
holds either tool by default.**

`agents_edit` can still point a *more privileged* agent at new prose — a new
cron, a rewritten prompt — without touching its grants. Granting `agents_edit`
is granting influence over what every agent (including admin-equivalent ones)
actually does; see the "Indirect escalation via editorial fields" note in
[design-15](../design/15-db-first-agents.md#indirect-escalation-via-editorial-fields).

## Profile image

An agent's picture is a **presentation attribute, not part of its
definition** — the same seam as `icon`: `image_artifact_id` on the row names
an image [artifact](artifacts.md), it never enters a version snapshot, a
rollback leaves it alone, and it is dropped from a definition `PUT`. It is
set through its own route, `PUT /api/agents/{name}/image` with
`{artifact_id | null}`, by the admin session, an `agents_edit` holder, or
**the agent itself** from its own run (which is what the artist does after
it draws one) — the artifact must be a live image. Once set, every face the
UI draws for that agent — Relay, tickets, the wiki, presence, the Agents
grid — shows the picture inside the same hue-tinted disc, with the emoji as
the fallback. Deleting the artifact clears the picture.

On the agent's page the Config tab has a **Profile image** section outside
the definition editor: **Upload** (a file or a drop), **Choose from
artifacts**, **Generate** (a configured model and a prompt prefilled as
`Portrait of "<name>": <description>. flat, friendly avatar, square, centred,
no text` — the generation is an ordinary artifact owned by you, so it lands
in `#art` and the Studio's strip too), and **Remove**. `/agents` itself is a
card grid by default — face, name, description, status, schedule — with a
Grid/Table toggle in the header that `localStorage` remembers; the table is
the old page, unchanged.

## Change log, not review

Every write to a definition — from the UI, the raw API, or either tool —
appends a full-snapshot row to `agent_versions` (`version`, `changed_by` the
verified principal, `changed_via` — `admin` / `tool:agents_edit` /
`tool:agents_grant` / `import` / `rollback` / `seed` / `migration`,
`created_at`). There is no pending/approval state: edits go live the
instant they're written. The History tab on an agent's page lists every
version, lets you view an old snapshot, and roll back — which re-applies
that snapshot as a *new* version, so the log only ever grows. Deleting an
agent files a tombstone version (`changed_via` prefixed `delete:`) rather
than erasing the log.

**How to add one:** the New Agent wizard in the UI, or `POST /api/agents`
directly — no PR, no folder, no manifest file.

## Seeded agents

The platform ships several agent rows, written once at boot behind a schema
mark and then left alone — edit or delete any of them and your version stays:

- **`wiki`** — the [librarian](wiki.md#the-librarian). It is platform-managed
  (`system`) and separately opts out of `@all`; only `@wiki` wakes it.
- **`artist`** — makes images on request (portraits, avatars, scene art,
  icons) with `image_gen`, keeps them as artifacts and answers with an
  `[[artifact:<id>]]` card. Summon it with `@artist` and a brief, in `#art`
  or anywhere. Runs on `sonnet`, holds `image_gen`, `artifacts` and `relay`,
  is deletable (`system: false`) and opts out of `@all`; direct mentions still
  reach it. Its first change-log row is `changed_via: seed`.
- **`codex-artist`** — runs Codex's built-in ImageGen for the Studio and for
  direct `@codex-artist` briefs, spending the Codex subscription allowance.
  It is `system` and has `responds_to_all: false`: Studio dispatch and direct
  mentions still reach it without spending a Codex run at standup.
- **`engineer`** — writes code for the platform: takes a ticket assigned to
  it, works on a branch in its own clone, verifies, and opens a PR for a
  human to merge — it never pushes, the platform publishes
  ([workbench.md](workbench.md)). `role: dev` (the dev run profile), `opus`
  (a coding run is where the strong model earns its cost), a 90-minute
  timeout (`timeout_seconds: 5400`), quota thresholds of 95 % (5 h) and 90 %
  (7 d), and `relay`, `tickets`, `wiki`, `quota_ok` and `artifacts` plus the
  `Glob` and `Grep` harness tools (the shell tools come with the profile).
  `push_path_globs` empty and `may_delete_tests` false: PR only, any path,
  no test deletions. Its home project is `#eng` (prefix `ENG`), seeded with
  it, and the weekday `eng-queue` job (`0 7 * * 1-5`, America/Toronto) asks
  it in `#eng` to pick up anything still open. It opts out of `@all`, because
  each wake is a full dev pod; direct mentions and assignments still reach it.
  Its first change-log row is `changed_via: seed`.
- **`qa`** — owns the tests: writes and prunes unit, integration and e2e
  tests, keeps the TCMS current (`apps/tcms`, the `tcms` tool), measures
  the suite and QAs the live UI. `role: dev`, `sonnet` (the nightly is
  bookkeeping most of the time), a two-hour timeout (`timeout_seconds:
  7200`), quota thresholds of 80 % (5 h) and 50 % (7 d) — the
  browser-in-the-loop is the expensive part: before a live session its
  prompt calls `quota_ok`, and when the answer is no it says "not spending
  the browser" and does the scripted walk instead. Holds `relay`,
  `tickets`, `wiki`, `quota_ok`, `artifacts` and `tcms` (not `query_app`:
  that is the wide rung, and the tool's read actions answer the same
  questions), the `Glob`, `Grep` and `PlaywrightMCP` harness tools, and
  binds the `qa-web-login` secret the API mints at boot (the readiness gate
  blocks it while that secret is missing — only ever mid-rotation).
  `push_path_globs` is the platform's one definition of test code
  (`testpaths.TEST_PATH_GLOBS`, imported, never restated) and
  `may_delete_tests` is true: pruning, with every deletion named on the PR
  and the card. Its home project is `#qa` (prefix `QA`; a `#qa` that
  already exists is adopted, and a prefix held by another room is left
  where it is), seeded with it, and the `qa-nightly` job (`0 2 * * *`,
  America/Toronto) summons it there. It is a normal, deletable worker like
  `engineer`, with `responds_to_all: false`,
  so `@all` and the `#standup` pass it by; `@qa` by
  name still wakes it. Its first change-log
  row is `changed_via: seed`.
