# Agents

**What:** the unit of work — a Claude Code agent the platform can run in a pod.

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
model: sonnet                 # claude model override; empty = CLI default
role: operator                 # reader | annotator | operator | coder | dev
                                # (coder gets the github-app + acceptEdits for platform self-edit PRs;
                                #  dev gets the Workbench: a shell + a credential-less clone, see workbench.md)
system: true                   # platform-internal; protected from UI deletion
can_invoke: true               # may trigger other agents (depth-guarded)
enabled: true                  # false = no new runs from any trigger (409)
concurrency: 1
timeout_seconds: 1800
result_topic: ""
transcript_retention_days: null
harness_tools: [WebFetch]                    # Claude Code built-ins; the sensitive
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

The last four are the [Workbench](workbench.md) fields. They exist on every
row but only mean something for `role: dev` (the two globs/tests fields) or
an agent that holds `quota_ok` (the two thresholds). `role: dev` joins no
endpoint allow-list — it is the run-profile rung, not an API scope.

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

Four rows ship with the platform, written once at boot behind a schema
mark and then left alone — edit or delete any of them and your version stays:

- **`wiki`** — the [librarian](wiki.md#the-librarian). A `system` agent, so
  `@all` passes it by; only `@wiki` wakes it.
- **`artist`** — makes images on request (portraits, avatars, scene art,
  icons) with `image_gen`, keeps them as artifacts and answers with an
  `[[artifact:<id>]]` card. Summon it with `@artist` and a brief, in `#art`
  or anywhere. Runs on `sonnet`, holds `image_gen`, `artifacts` and `relay`,
  and is *not* `system`, so `@all` reaches it. Its first change-log row is
  `changed_via: seed`.
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
  it in `#eng` to pick up anything still open. *Not* `system`, so `@all` and
  the `#standup` reach it — each such wake is a full dev pod, which is a
  cost worth knowing. Its first change-log row is `changed_via: seed`.
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
  America/Toronto) summons it there. A `system` agent, so `@all` and the
  `#standup` pass it by; `@qa` by name still wakes it. Its first change-log
  row is `changed_via: seed`.
