# Agent Platform — Design Overview

**What this is:** the index and founding architecture of the design record.
Each numbered doc under `docs/design/` records one milestone — why it was
built and how. They are historical documents, kept in their original voice;
this overview is maintained. For what the platform *is* today, read
`docs/building-blocks/` (start with the Glossary); for how to run it,
`docs/setup.md`.

End-to-end agent wrangler: define Claude Code agents as code, run them as
k8s pods, control everything from a web UI whose own edit mechanism is
dispatching a coding agent. The reference deployment is the pai NUC — a
single-node k3s cluster, Helm release `ap`
([host setup](https://github.com/kylep/multi/blob/main/apps/blog/blog/markdown/wiki/devops/pai-nuc-k3s.md))
— but it installs on any k8s via one Helm chart.

Successor to `multi/infra/ai-agents` (v2 of Kyle's earlier agent runner in
the `kylep/multi` repo; clean slate, inspiration only).

## Hard constraints

- **Claude subscription auth only.** Agents authenticate with the
  subscription OAuth token, stored as a platform secret. No Anthropic API
  keys anywhere; CI greps enforce it.
- **Git is the source of truth for capability** — skills, tools, secret
  declarations, apps, the platform services themselves. Agent *identity*
  (prompt, grants, entrypoints, config) is the one exception: since
  [15](15-db-first-agents.md) it lives in Postgres as a row, with its own
  append-only change log standing in for the PR record. The database
  otherwise holds runtime state only.
- **Postgres for data, no vector store.** Memory search uses postgres FTS.
- **Kafka is the spine**, kept honest: postgres-first writes, idempotent
  consumers, DLQ surfaced in the UI, dispatch swappable to
  postgres-polling in one service if Kafka ever disappoints.

## Architecture

The deployables (the last four arrived after this doc was first written;
`docs/building-blocks/glossary.md` has the maintained list with build
contexts):

| Component | Role |
|-----------|------|
| **api** (FastAPI) | Auth, REST + OpenAPI for agents/runs/schedules/secrets/memory. Writes intent to postgres, publishes commands to Kafka. Never touches the k8s API. |
| **dispatcher** (Python) | Consumes `run.requests`, enforces RBAC + concurrency caps, creates k8s Jobs. Contains the cron scheduler loop. Idempotent against the `runs` table. |
| **runner** (image) | Agent pod. Wraps `claude --agent <name> -p <prompt> --output-format stream-json`, mounts the subscription token read-only, publishes every stream event to Kafka. |
| **recorder** (Python) | Consumes event topics, writes transcripts/metrics/state to postgres. |
| **web** (React SPA) | Dashboard, agents, runs with live transcript, schedules, pending changes, skills, secrets, reporting, settings. |
| **mcp-broker** (FastMCP) | Exposes the platform API as `mcp__platform__*` tools over streamable HTTP. Holds no credentials — forwards each caller's bearer token, so a run's scope is preserved. Lets agents act without a shell, and lets external MCP clients drive the platform. |
| **connector-discord** | Bridges Discord to the platform: a mention opens a thread, which is a Conversation; consumes `discord.channel.post` to speak. Sole holder of the bot token. |
| **postgres** | Runtime state: runs, transcripts, schedules, jobs, principals, memories, conversations, secret metadata. |
| **kafka** (single-node KRaft) | Topics: `run.inbound`, `run.requests`, `run.events`, `run.transcript`, `run.dlq`, `conversation.*`, `discord.channel.post`, `platform.tool.audit`, `dead.letter` (the chart's `topics.specs` is authoritative). |
| **tool-executor** (`docs/design/12-executable-capabilities.md`) | Runs custom tools' reviewed code in a locked-down subprocess with call-time secrets. The broker is its only client; it is the platform's single third-party-egress point. |
| **claude-proxy** (`docs/design/09-token-brokering.md`) | Holds the Claude credential and injects it per-request, so runner pods never carry it. |
| **agents-sync** | Keeps the synced checkout of this repository current; every service reads definitions from it. |
| **app pods** (`docs/design/11-apps-and-reports.md`) | Full applications built on the platform, one per `apps/<name>/`. |

Run flow: trigger (UI / cron / webhook / agent / API) → api writes `runs`
row → publishes to `run.requests` → dispatcher validates and creates Job →
runner streams events → recorder persists, UI live-tails via websocket.

k8s CronJobs are deliberately not used; the dispatcher's scheduler
publishes `run.requests` so scheduled runs share the same queueing,
records, and guardrails as every other trigger.

## Repo layout

```
skills/<name>/                # Claude Code skill format (knowledge an agent reads)
tools/<name>/                 # executable capabilities run by the tool-executor (design 12)
apps/<name>/                  # full applications built on agents (design 11)
packages/ui/                  # @ap/ui — the shared design system, consumed as source
services/backend/              # api + dispatcher + recorder (one image, three entrypoints)
services/{runner,web,mcp-broker,tool-executor,connector-discord}/
charts/agent-platform/        # umbrella chart + postgres/kafka dependencies
sdk/                          # hand-written, dependency-free python; CI asserts
                              # every path it calls exists in the live OpenAPI
docs/design/                  # this doc + numbered milestone docs
bin/                          # set-claude-token.sh and friends
```

Agent identity is not part of this tree ([15](15-db-first-agents.md)): each
agent is a row in `agent_defs` (prompt, grants, entrypoints, config),
edited through the API/UI or the `agents_edit`/`agents_grant` platform tools,
with an append-only version log instead of a git history. There used to be an
`agents/<name>/{agent.md,manifest.yaml}` tree here; it was deleted from the
repo once the one-time import into Postgres succeeded (`docs/deployment.md`
has the migration note). The sync process still pulls main into a shared
volume for everything above — skills, tools, secret declarations, reports,
apps, these docs — and schema-validates as it goes; a broken skill/tool
quarantines just that block without stopping sync. Secret bindings are
declared on skills and tools; an agent gets the union of its skills' secrets
plus its own row-level grants.

## Data model

- `agent_defs` / `agent_versions` — an agent's identity as a row (prompt,
  grants, entrypoints, config) plus its append-only change log (agent,
  version, full snapshot, changed_by, changed_via, timestamp). Since
  [15](15-db-first-agents.md); see `docs/building-blocks/agents.md`.
- `runs` — agent, trigger, requested_by, state, timestamps, cost/duration.
- `run_transcript_events` — append-only stream-json events by run + seq;
  feeds both the transcript view and metrics (tool calls, tokens).
- `schedules` — cron expr, enabled flag; runtime state, toggleable without
  a commit.
- `principals` / `api_keys` — admin + per-agent keys, role, scopes, hashed.
- `memories` — agent-namespaced rows, postgres FTS, reviewable in the UI.
  (Since design 12 this lives in the memory *tool's* own schema,
  `tool_memory`, and agents reach it by declaring the tool.)
- `secrets_meta` — names, bindings, rotation timestamps. Values live in
  k8s Secrets only.

Run states: `queued → dispatched → running → succeeded | failed |
timed_out | killed`, plus `rejected` and `dlq`. Guardrails on every run:
per-agent concurrency (default 1), wall-clock timeout (default 30m), and a
global concurrency cap — the subscription token is one shared rate-limit
pool.

## RBAC and the tiered git write path

Roles: `admin` (Kyle), `operator` (trigger runs, toggle schedules),
`coder` (operator + git writes; the platform-coder agent), `reader`.
The API enforces scopes; the dispatcher re-checks at dispatch time.

Git writes are tiered by the *diff*, not the request — this now applies to
**capability** (skills, secrets, tools, reports, apps), not agent definitions:

- **Tier 1 — direct commit:** single-file edits, e.g. a SKILL.md body or safe
  fields. Applied deterministically by the API's git service.
- **Tier 2 — PR required:** new/deleted blocks, secret bindings, anything
  under `services/` or `charts/`. The coding agent works on a branch; nothing
  syncs until merge.

A Pending Changes page lists platform-authored branches/PRs with rendered
diffs; affected blocks get an "unmerged changes" badge. The platform
authenticates to git as a GitHub App (installation tokens, which can push and
open PRs), with a `github-token` PAT as the fallback.

Agent definitions used to be tiered the same way (`agent.md` body / manifest
fields tier-1, new/deleted agents and role changes tier-2). Since
[15](15-db-first-agents.md) they are not git writes at all: every agent edit
— prose or grant — applies immediately to the `agent_defs` row, gated instead
by which of the two RBAC platform tools (`agents_edit`/`agents_grant`) the
caller holds, with the append-only `agent_versions` log standing in for the
PR record.

## Auth

Local admin login (argon2, session cookie) plus scoped `ap_...` bearer
API keys, hashed at rest, shown once at mint. Agents are principals: each
gets a key bound to its role. Every run records `requested_by`; every
platform commit carries an author trailer.

**First-launch setup:** the API exposes `setup_state`. No admin → SPA
routes to one-time admin creation. Then a required-secrets gate: Claude
credentials and the git deploy key are probed for validity (a minimal
`claude -p` smoke run; `git ls-remote`); anything not `ok` banners the UI
and redirects to Settings → Secrets. Headless alternative:
`bin/set-claude-token.sh` calls the same secrets API.

## Runtime posture

Runner pods are caged on several sides at once: a scoped ServiceAccount, only
the secrets the agent's definition earns, a default-deny NetworkPolicy, a non-root
securityContext with capabilities dropped, and a **scoped tool allow-list
derived from the agent's own declaration** — no agent runs with permissions
bypassed (see [08](08-news-and-injection-hardening.md)). Denied tool calls are
recorded on the run and surfaced in the UI. The one self-edit exception is
platform-coder, which gets `acceptEdits` on an ephemeral clone.
Workspaces are ephemeral `emptyDir`; persistent per-agent workspaces are a
later opt-in.

**Subscription token (resolved in M01 verification):** sharing the
laptop's session credentials fails fast — the laptop's own claude rotates
the refresh token, invalidating any snapshot. The platform instead uses a
dedicated long-lived token from `claude setup-token` (1-year validity,
subscription-billed), stored under the `token` key of the
claude-credentials secret. Since token brokering
([09](09-token-brokering.md)) the token never enters runner pods: only the
claude-proxy holds it, and runners reach Anthropic through the proxy with a
placeholder credential. No steward process is needed; nothing rotates the
token.

## Infra sizing (pai NUC: i3-7100U 2c/4t, 29Gi RAM, 480G NVMe)

Requests/limits: postgres 1Gi/2Gi, kafka 2Gi/3Gi (JVM heap sized to fit),
api/dispatcher/recorder 256Mi each, web 128Mi — ~4Gi baseline. Runners
1Gi/3Gi with global concurrency 3 (CPU-bound, not RAM). Worst case ~13Gi
of 29Gi. PVCs on `local-path`; the idle 372G SATA SSD is reserve.
Exposure: LAN LoadBalancer, auth always on; public exposure waits for the
hardening milestone.

## Milestones

| Doc | Milestone | Proves |
|-----|-----------|--------|
| [01](01-walking-skeleton.md) | Walking skeleton | The spine + subscription auth |
| [02](02-self-hosting-loop.md) | Self-hosting loop (MVP) | Agents editing agents via tiered git |
| [03](03-scheduling-and-triggers.md) | Scheduling & triggers | Cron, webhooks→Kafka, agent-invokes-agent, DLQ |
| [04](04-memory-skills-sdk.md) | Memory, skills, SDK | Memory API/UI, shipped skills, OpenAPI→SDK+skill |
| [05](05-observability.md) | Observability & health | Metrics rollups, lag monitoring, reporting |
| [06](06-hardening.md) | Hardening | NetworkPolicies, securityContext, rotation, exposure |
| [07](07-pai-migration.md) | Conversations & Kafka foundation | Event-sourced ingress, Conversation entity, Discord connector. **Reframed**; the original pai-migration scope closed 2026-08-03 (v1 archived to multi-sandbox — see the doc). |
| [08](08-news-and-injection-hardening.md) | News & injection hardening | Privilege separation, scoped tool allow-lists, no bypassed permissions |
| [09](09-token-brokering.md) | Token brokering | The Claude credential leaves runner pods entirely |
| [10](10-declarative-building-blocks.md) | Declarative building blocks | Secrets-as-code, readiness gate, entrypoints, wizards |
| [11](11-apps-and-reports.md) | Apps & reports | Reports as a block; full apps (news) built on agents |
| [12](12-executable-capabilities.md) | Executable capabilities | Tools as a building block: reviewed code, no shell, declared infra |
| [13](13-workload-identity.md) | Workload identity | Projected SA tokens, SPIRE mTLS, run JWTs, principals, tool audit |
| [14](14-conversation-session-resume.md) | Conversation session resume | Stateful session resume instead of stateless full-transcript replay |
| [15](15-db-first-agents.md) | DB-first agents | Agent identity moves from git to Postgres rows; `agents_edit`/`agents_grant` RBAC tools; per-agent change log replaces the PR record |
| [16](16-webhook-auth.md) | Webhook auth | Per-path webhook secrets, uniform-401 anti-enumeration |
| [17](17-external-mcp-facade.md) | External MCP facade | The platform API as MCP tools for outside clients, generated from OpenAPI |
| [18](18-news-freshness.md) | News freshness | Why the digest posted old news; per-item `published` + deterministic gates (stale/undated/hub/repeat) as rejected events; `params` on the app query proxy |
