# Agent Platform

Define Claude Code agents as code, run them on Kubernetes, and drive the whole
thing from a web UI — whose own edit button is itself a coding agent opening a
pull request against this repo.

Git is the source of truth for agent definitions. The database holds runtime
state only. Agents authenticate with a Claude subscription token; there are no
Anthropic API keys anywhere, and CI greps to keep it that way.

```
trigger (UI · cron · webhook · Discord · agent · API)
   → api writes a run row  → Kafka  → dispatcher creates a k8s Job
   → runner streams events → Kafka  → recorder persists → UI live-tails
```

## What it can do

**Run agents.** An agent is a directory: `agents/<name>/agent.md` (a portable
Claude Code definition, still runnable with bare `claude --agent`) plus
`manifest.yaml` (the platform layer — role, skills, secrets, schedule,
concurrency). Every run gets a live-tailing transcript, a kill button,
per-agent and global concurrency caps, and a wall-clock timeout.

**Trigger them however.** Run-now from the UI, cron via first-class Scheduled
Jobs (many jobs per agent, each with its own prompt, plus "Run Now" and
plain-English cron tooltips), inbound webhooks, the REST API, another agent
(depth-guarded), or a Discord mention.

**Edit itself.** Ask the UI to change an agent and a coding agent does it, with
writes tiered by the resulting diff: safe single-file edits commit straight to
`main`, anything structural opens a PR. A Pending Changes page lists the
platform's own open branches with rendered diffs. There's also a New Agent
wizard and a checkbox editor for skills and tools — both of which just produce
ordinary commits and PRs.

**Remember things.** A per-agent namespaced memory API backed by Postgres
full-text search, reviewable and editable in the UI.

**Talk.** Mention the bot in Discord and the thread becomes a Conversation the
agent replies in.

**Be observable.** Metrics rollups and a reporting page, a dead-letter queue
surfaced in the UI, Kafka health checks, transcript retention/pruning, and a
scheduled health-monitor agent that pages you when something looks wrong.

**Stay in its lane.** Agents run non-root with dropped capabilities under a
default-deny NetworkPolicy, and get a scoped tool allow-list derived from their
own declaration — no agent runs with permissions bypassed. Secret access is
audited, and denied tool calls are recorded and surfaced on the run page.

**Be driven by other tools.** An MCP broker exposes the platform API as
`mcp__platform__*` tools over streamable HTTP, forwarding each caller's own
token rather than holding credentials. Platform agents use it instead of a
shell; external MCP clients can use it too.

## Architecture

| Component | Role |
|---|---|
| **api** | Auth, REST + OpenAPI. Writes intent to Postgres, publishes commands to Kafka. Never touches the k8s API. |
| **dispatcher** | Consumes run requests, enforces RBAC and concurrency, creates k8s Jobs. Hosts the cron scheduler. |
| **runner** | The agent pod. Wraps `claude` and streams every event to Kafka. |
| **recorder** | Consumes events; writes transcripts, metrics, and state. |
| **web** | React SPA — dashboard, agents, runs, schedules, changes, skills, secrets, reporting. |
| **mcp-broker** | Exposes the platform API as MCP tools. |
| **connector-discord** | Bridges Discord threads to Conversations. |
| **postgres / kafka** | Runtime state, and the event spine. |

Kafka is kept honest: Postgres-first writes, idempotent consumers, a surfaced
DLQ, and dispatch that can fall back to Postgres polling in one service if
Kafka ever disappoints.

## Install

One Helm chart, any Kubernetes cluster:

```sh
helm install ap charts/agent-platform -n agent-platform --create-namespace
```

First browser visit walks you through creating an admin, then gates on the
secrets it needs (a Claude subscription token, and git credentials if you want
the self-editing loop). See [docs/setup.md](docs/setup.md) and
[docs/deployment.md](docs/deployment.md).

## Docs

[docs/design/](docs/design/) — architecture overview plus one document per
milestone, each recording what shipped and how it was verified.

## Status

Built and operated as a personal platform on a home k3s node. Everything above
runs live. Expect sharp edges.
