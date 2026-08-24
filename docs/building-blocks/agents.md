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

## Change log, not review

Every write to a definition — from the UI, the raw API, or either tool —
appends a full-snapshot row to `agent_versions` (`version`, `changed_by` the
verified principal, `changed_via` — `admin` / `tool:agents_edit` /
`tool:agents_grant` / `import` / `rollback`, `created_at`). There is no
pending/approval state: edits go live the instant they're written. The
History tab on an agent's page lists every version, lets you view an old
snapshot, and roll back — which re-applies that snapshot as a *new* version,
so the log only ever grows. Deleting an agent files a tombstone version
(`changed_via` prefixed `delete:`) rather than erasing the log.

**How to add one:** the New Agent wizard in the UI, or `POST /api/agents`
directly — no PR, no folder, no manifest file.
