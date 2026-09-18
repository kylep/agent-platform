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
role: operator                 # reader | annotator | operator | coder
                                # (coder gets the github-app + acceptEdits for platform self-edit PRs)
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
```

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
  (prompt, description, model, entrypoints, timeout, …). Cannot touch any
  grant field.
- **`agents_grant`** — assign/revoke `harness_tools`, `platform_tools`,
  `skills`, `secrets`, `can_invoke`, and `role` on any agent. This is the
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

Two rows ship with the platform, written once at boot behind a schema mark
and then left alone — edit or delete either and your version stays:

- **`wiki`** — the [librarian](wiki.md#the-librarian). A `system` agent, so
  `@all` passes it by; only `@wiki` wakes it.
- **`artist`** — makes images on request (portraits, avatars, scene art,
  icons) with `image_gen`, keeps them as artifacts and answers with an
  `[[artifact:<id>]]` card. Summon it with `@artist` and a brief, in `#art`
  or anywhere. Runs on `sonnet`, holds `image_gen`, `artifacts` and `relay`,
  and is *not* `system`, so `@all` reaches it. Its first change-log row is
  `changed_via: seed`.
