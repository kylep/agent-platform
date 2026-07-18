# Agent Platform — Design Overview

End-to-end agent wrangler: define Claude Code agents as code, run them as
k8s pods, control everything from a web UI whose own edit mechanism is
dispatching a coding agent. Lives on the pai NUC
([setup](https://github.com/kylep/multi/blob/main/apps/blog/blog/markdown/wiki/devops/pai-nuc-k3s.md))
but installs on any k8s via one Helm chart.

Successor to `multi/infra/ai-agents` (v2, clean slate, inspiration only).

## Hard constraints

- **Claude subscription auth only.** Agents authenticate with the
  subscription OAuth token, stored as a platform secret. No Anthropic API
  keys anywhere; CI greps enforce it.
- **Git is the source of truth** for agent definitions and skills. The
  database holds runtime state only.
- **Postgres for data, no vector store.** Memory search uses postgres FTS.
- **Kafka is the spine**, kept honest: postgres-first writes, idempotent
  consumers, DLQ surfaced in the UI, dispatch swappable to
  postgres-polling in one service if Kafka ever disappoints.

## Architecture

Seven deployables:

| Component | Role |
|-----------|------|
| **api** (FastAPI) | Auth, REST + OpenAPI for agents/runs/schedules/secrets/memory. Writes intent to postgres, publishes commands to Kafka. Never touches the k8s API. |
| **dispatcher** (Python) | Consumes `run.requests`, enforces RBAC + concurrency caps, creates k8s Jobs. Contains the cron scheduler loop. Idempotent against the `runs` table. |
| **runner** (image) | Agent pod. Wraps `claude --agent <name> -p <prompt> --output-format stream-json`, mounts the subscription token read-only, publishes every stream event to Kafka. |
| **recorder** (Python) | Consumes event topics, writes transcripts/metrics/state to postgres. |
| **web** (React SPA) | Dashboard, agents, runs with live transcript, schedules, pending changes, skills, secrets, settings. |
| **postgres** | Runtime state: runs, transcripts, schedules, principals, memories, secret metadata. |
| **kafka** (single-node KRaft) | Topics: `run.requests`, `run.events`, `run.transcript`, `run.dlq`, `webhooks.in`. |

Run flow: trigger (UI / cron / webhook / agent / API) → api writes `runs`
row → publishes to `run.requests` → dispatcher validates and creates Job →
runner streams events → recorder persists, UI live-tails via websocket.

k8s CronJobs are deliberately not used; the dispatcher's scheduler
publishes `run.requests` so scheduled runs share the same queueing,
records, and guardrails as every other trigger.

## Repo layout

```
agents/<name>/agent.md        # pure Claude Code agent definition (portable)
agents/<name>/manifest.yaml   # platform layer: rbac role, skills[], secrets[],
                              # triggers[], schedule, concurrency
skills/<name>/                # Claude Code skill format; git/ and discord/ ship at launch
services/{api,dispatcher,recorder,runner,web}/
charts/agent-platform/        # umbrella chart + postgres/kafka dependencies
sdk/                          # generated from OpenAPI, python first
docs/design/                  # this doc + numbered milestone docs
bin/                          # set-claude-token.sh and friends
```

`agent.md` stays runnable with bare `claude --agent`. The sync process
pulls main into a shared volume and schema-validates manifests; a broken
manifest quarantines that agent in the UI without stopping sync. Secret
bindings are declared on skills; an agent gets the union of its skills'
secrets plus its own.

## Data model

- `runs` — agent, trigger, requested_by, state, timestamps, cost/duration.
- `run_transcript_events` — append-only stream-json events by run + seq;
  feeds both the transcript view and metrics (tool calls, tokens).
- `schedules` — cron expr, enabled flag; runtime state, toggleable without
  a commit.
- `principals` / `api_keys` — admin + per-agent keys, role, scopes, hashed.
- `memories` — agent-namespaced rows, postgres FTS, reviewable in the UI,
  exposed to agents through a memory skill hitting the API.
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

Git writes are tiered by the *diff*, not the request:

- **Tier 1 — direct commit:** single-file edits to an existing agent's
  `agent.md` body or safe manifest fields (schedule, prompt,
  description). Applied deterministically by the API's git service.
- **Tier 2 — PR required:** new/deleted agents, role changes, secret
  bindings, skill changes, anything under `services/` or `charts/`.
  The coding agent works on a branch; nothing syncs until merge.

A Pending Changes page lists platform-authored branches/PRs with rendered
diffs; affected agents get an "unmerged changes" badge. The platform
authenticates to git with a repo-scoped deploy key.

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

Runner pods get full autonomy inside a cage: `claude` runs
unrestricted, contained by a scoped ServiceAccount, the secrets its
manifest earns, and (in the hardening milestone) NetworkPolicies and a
tight securityContext. Workspaces are ephemeral `emptyDir`; persistent
per-agent workspaces are a later opt-in.

**Subscription token wrinkle:** concurrent runners refreshing a shared
OAuth token can race. Runners mount a copy and never write back; one
steward process is the sole refresher, updating the k8s Secret. Verify
against real CLI behavior in milestone 01; the steward stays dormant if
refresh proves unnecessary within run lifetimes.

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
| [07](07-pai-migration.md) | pai migration | Port multi's agents, retire the v1 stack |
