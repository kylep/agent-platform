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
an explicit RBAC model enforced at the broker's tool-authorization chokepoint
(the same gate every platform tool call passes through, whether it runs in
the tool-executor or, like these two, is broker-resident code — see "What
changes where"), which is stronger than "protected by workflow."

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
- **RBAC via platform tools.** Two new code-defined platform tools, both
  **broker-resident** rather than executor tools (as-built change — see "What
  changes where": the executor's minimal-env contract can't carry the
  attributable identity a definition write needs):
  - `agents_edit` — create/update/delete agent definitions (prompt, config,
    entrypoints). May NOT touch grants.
  - `agents_grant` — assign/revoke harness tools, platform tools, skills,
    secrets, `can_invoke`, and `role` on any agent. The escalation-capable
    tool: granting it is granting the keys. (`role` and `can_invoke` are
    grant-gated too, not just the four name lists — see the as-built note in
    "What changes where": both control a run's authority, not its prose.)
  The admin session always has both implicitly. An agent has them only if
  granted (like `stocks` or `discord_chat`); calls go through the broker's
  guarded call path, land in `ToolAudit` (the same `platform.tool.audit`
  trail every tool call uses), and are attributed in `agent_versions` via
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
  deleted from the repo. The import materializes **effective**, not literal,
  semantics: an `agent.md` with no `tools:` line meant "unrestricted" under
  the file era's implicit default, so it is expanded on import to the full
  non-sensitive harness set (`CLAUDE_TOOLS` minus the sensitive tools) plus an
  *empty* platform-tool set — matching what that agent's run token actually
  granted before the migration, not a literal (and silently narrower) reading
  of the absent line. An agent with an explicit `tools:` line copies exactly.
- Ops: a Postgres backup CronJob ships in the chart — with identity fully in
  the DB, backups are the recovery story (plus `agent_versions` for
  agent-level point-in-time).
- **As-built deviations, both accepted:**
  - `agents_edit`/`agents_grant` ship as **broker-resident core tools**, not
    `tools/agents_edit/**` executor tools. A `tools/<name>/run.py` gets no
    credential of any kind — the executor builds its subprocess env from
    scratch, by design, so the caller's own token is never forwarded outward.
    Writing a definition needs exactly that forwarding (attribution, and the
    `WriteScope`/`agent_write_scope` authority check), so the two tools live
    in the broker next to the other core (`runs_read`-style) tools instead. A
    `tools/agents_edit/` directory is refused at the registry as a name
    collision with a core tool, the same guard that protects `runs_read` etc.
  - `role` and `can_invoke` moved into the grant-gated field set alongside the
    four name lists (`harness_tools`, `platform_tools`, `skills`, `secrets`).
    Both control a run's *authority* rather than its prose — `role: coder`
    is the self-edit rung (GitHub App token, no sensitive-tool deny-list), and
    `can_invoke` widens a run's token to operator-scoped — so leaving either
    editorial would let `agents_edit` hand any agent, including itself, more
    power than an editor should be able to grant.

## Security invariants (unchanged or strengthened)

- The sensitive harness tools (Bash/Read/Edit/Write/NotebookEdit) remain
  unconditionally denied to non-self-edit runs in the runner, regardless of
  what a row grants.
- Run tokens/JWTs still freeze the grant set at launch (design-13); a run
  cannot benefit from a grant added mid-flight.
- No agent token can write grants unless the agent explicitly holds
  `agents_grant`; `agents_edit` cannot write a grant field directly (see the
  indirect-escalation note below for what it can still reach).
- Every definition/grant change is attributable: principal from the verified
  identity chain, never from request payload.

### Indirect escalation via editorial fields

**Accepted, reviewed design decision (Task 3 review, 2026-08-23) — documented
here so it reads as chosen, not missed.** `agents_edit` is restricted to
`EDIT_FIELDS` (prompt, description, model, entrypoints, timeout, …) and can
never write a grant field on any agent, including its own. But it can rewrite
the **prompt and entrypoints of a MORE privileged agent** — including one that
holds `agents_grant`, `coder`, or `admin`-adjacent capabilities — and give it
a cron or a webhook that fires it. The edit/grant split guards *direct* grant
writes only; it says nothing about steering what an already-privileged agent
is told to do or when it runs.

Practically: an agent holding only `agents_edit` cannot grant itself
`agents_grant`, but it *can* edit the prompt of an agent that already holds
`agents_grant` and schedule it to run — at which point that more-privileged
agent's next action is whatever the edited prompt says. The escalation is
indirect (it routes through another agent's own judgment, and every step is
attributed in the change log and audited at the broker), but it is real
influence over that agent's behavior. The practical consequence: **granting
`agents_edit` is granting influence over every agent's behavior**, not just
the target agent's prose. It should be handed out with the same care as
`agents_grant`, not treated as the "safe half" of the split. This was weighed
against the alternative (`agents_edit` restricted to agents no more privileged
than the caller, which needs a privilege-ordering concept the role/grant model
doesn't otherwise have) and accepted as-is rather than adding that complexity
for a single-operator deployment where every grant is still admin-reviewed.

## Known losses, accepted

- Definitions are no longer greppable in the repo or reviewed as diffs in PRs;
  the change log and UI history replace that.
- CI no longer exercises the shipped agent definitions; validation moved to
  the write path.
- A fresh cluster bootstraps from a pg backup (or empty + UI), not from git.
