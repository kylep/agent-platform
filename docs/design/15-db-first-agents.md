# 15 — DB-first agents (capabilities are code, identity is rows)

## Decision

Agent identity moves out of git and into Postgres. The `agents/` tree, the
agent PR-edit flow, and the agents-sync coupling for definitions are removed.
What remains code — and only ever code — is **capability**: platform tools
(`tools/`), skills, secret *declarations* (`secrets/`), apps, and the platform
services themselves. What an agent *is* (its prompt/personality, its grants,
its entrypoints, its config) is a row, mutable through the admin API/UI and
through two new platform tools, with every change captured in an append-only
change log.

Rationale (2026-08-23): the DB already owned most agent state — schedules
(`ScheduledJob`), memories (`tool_memory`), conversations/runs, and (design-14)
Claude session blobs. Identity-in-git had become a split-brain: neither store
could restore a whole agent, every prose tweak paid a PR round-trip with one
human reviewer, and the sync machinery was a recurring gotcha tax. The one
thing git genuinely earned — gating *agent-initiated* changes — is replaced by
an explicit RBAC model enforced at the tool-executor chokepoint, which is
stronger than "protected by workflow."

## The model

- **Capability = code.** A tool, skill, secret declaration, or app exists
  because it is in the repo. Secrets bind to capabilities (tools/skills) as
  declared in code; the DB never defines a capability.
- **Identity = rows.** `agent_defs` holds one row per agent: prompt (the
  former agent.md body — the agent's context/personality), description, model,
  role, system/can_invoke flags, timeout, concurrency, result_topic,
  transcript retention, harness-tool grants (e.g. WebFetch; the sensitive set
  stays hard-denied for non-self-edit runs exactly as before), platform-tool
  grants (design-12 role ladder derives from these rows now, not frontmatter),
  skill grants, secret grants, and entrypoints (cron/webhooks/topics, formerly
  entrypoints.yaml).
- **Change log, not review.** Every write to `agent_defs` — API, UI, or tool —
  appends a full-snapshot row to `agent_versions` (agent, version, snapshot
  JSON, changed_by principal, timestamp). There is no pending/approval state;
  edits apply immediately. Rollback = re-apply an old snapshot (which itself
  logs a new version).
- **RBAC via platform tools.** Two new code-defined platform tools:
  - `agents_edit` — create/update/delete agent definitions (prompt, config,
    entrypoints). May NOT touch grants.
  - `agents_grant` — assign/revoke harness tools, platform tools, skills, and
    secrets on any agent. The escalation-capable tool: granting it is granting
    the keys.
  The admin session always has both implicitly. An agent has them only if
  granted (like `stocks` or `discord_chat`), calls go through the tool
  executor, land in `ToolAudit`, and are attributed in `agent_versions` via
  the run's verified identity (never self-reported). **Seed state: no agent
  holds either tool.**

## What changes where

- `AgentStore` becomes DB-backed but keeps its read interface (AgentInfo,
  Manifest-shaped views) so joblauncher/dispatcher/recorder/readiness keep
  working; it refreshes from the DB instead of a directory tree.
- The launcher/runner stop reading the `/agents` mount: the launcher passes
  the definition to the run pod (run-scoped fetch, like the design-14 session
  blob), and the runner materializes `~/.claude/agents/<name>.md` from it.
  Skills still install from the git-synced mount (they are code).
- The agents API becomes direct CRUD + `GET .../versions`; the agent PR-flow
  endpoints and Pending-Changes lock for agents are removed. Validation that
  CI used to provide on the files (parseable frontmatter equivalents, known
  tools/skills/secrets) becomes validation-on-write.
- The web UI's agent editor edits live rows, shows the version history, and
  gains a grants panel; the New-Agent wizard writes a row.
- One-shot migration: an admin import endpoint seeds `agent_defs` from the
  final state of the `agents/` tree; after the live import, the tree is
  deleted from the repo.
- Ops: a Postgres backup CronJob ships in the chart — with identity fully in
  the DB, backups are the recovery story (plus `agent_versions` for
  agent-level point-in-time).

## Security invariants (unchanged or strengthened)

- The sensitive harness tools (Bash/Read/Edit/Write/NotebookEdit) remain
  unconditionally denied to non-self-edit runs in the runner, regardless of
  what a row grants.
- Run tokens/JWTs still freeze the grant set at launch (design-13); a run
  cannot benefit from a grant added mid-flight.
- No agent token can write grants unless the agent explicitly holds
  `agents_grant`; `agents_edit` cannot escalate.
- Every definition/grant change is attributable: principal from the verified
  identity chain, never from request payload.

## Known losses, accepted

- Definitions are no longer greppable in the repo or reviewed as diffs in PRs;
  the change log and UI history replace that.
- CI no longer exercises the shipped agent definitions; validation moved to
  the write path.
- A fresh cluster bootstraps from a pg backup (or empty + UI), not from git.
