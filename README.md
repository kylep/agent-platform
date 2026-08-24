# Agent Platform

Define Claude Code agents as code, run them on Kubernetes, and drive the whole
thing from a web UI — whose own edit button is itself a coding agent opening a
pull request against this repo.

Git is the source of truth for capability — tools, skills, secret
declarations. Agent *identity* (prompt, grants, entrypoints, config) is a
Postgres row instead, mutable immediately with its own append-only change
log — see [docs/design/15-db-first-agents.md](docs/design/15-db-first-agents.md).
Agents authenticate with a Claude subscription token; there are no
Anthropic API keys anywhere, and CI greps to keep it that way.

```
trigger (UI · cron · webhook · Discord · agent · API)
   → api writes a run row  → Kafka  → dispatcher creates a k8s Job
   → runner streams events → Kafka  → recorder persists → UI live-tails
```

## What it can do

**Run agents.** An agent is a Postgres row (`agent_defs`): a prompt (the
agent's context/personality), plus the platform layer — role, skills,
secrets, harness/platform-tool grants, schedule, concurrency. Every run gets
a live-tailing transcript, a kill button, per-agent and global concurrency
caps, and a wall-clock timeout.

**Trigger them however.** Run-now from the UI, cron via first-class Scheduled
Jobs (many jobs per agent, each with its own prompt, plus "Run Now" and
plain-English cron tooltips), durable entrypoints (cron/webhooks/topics
stored on the row), inbound webhooks, the REST API, another agent
(depth-guarded), or a Discord mention.

**Edit itself.** The agent editor writes the row directly and immediately —
no PR, no branch, no lock — with every change captured in an append-only
version log (view, diff, roll back from the agent's History tab). Two
platform tools split the write authority the same way a human editor is
bound by RBAC: `agents_edit` can rewrite an agent's prose and config;
`agents_grant` is the one that can touch tool/skill/secret grants. Skills,
tools, and secrets are a different story: those still go through the change
loop — a New Skill/Tool wizard or a raw editor opens a PR, reviewed on the
Changes page, live after the next sync.

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

## Building blocks

Capability (skills, tools, secret declarations, reports, apps) lives in
**git** as self-describing folders; agent *identity* and all runtime state
live in **Postgres**; secret *values* live in **k8s** and never enter git.
Losing the database now costs agent identity too, not just history — a
Postgres backup CronJob is the recovery story for that (see
[docs/deployment.md](docs/deployment.md)). One paragraph each — full docs in
[docs/building-blocks/](docs/building-blocks/):

- **[Agents](docs/building-blocks/agents.md)** — a Postgres row (`agent_defs`):
  prompt, role, skills, secrets, harness/platform-tool grants, model, limits.
  No PR — edits apply immediately, through the UI/API or the `agents_edit`/
  `agents_grant` platform tools, with every change appended to a version log
  (`agent_versions`). Readiness is still *derived*: an unmet required secret
  dependency blocks the agent's runs before dispatch, with the exact reason
  recorded.
- **[Entrypoints](docs/building-blocks/entrypoints.md)** — part of the agent
  row: the agent's durable triggers (cron list, each with its own optional
  prompt; declared webhook paths; kafka reserved). Undeclared webhook paths
  don't exist.
- **[Skills](docs/building-blocks/skills.md)** — `skills/<name>/SKILL.md`:
  reusable *knowledge* agents opt into; each declares its secrets with
  strictness (`state`/`severity`). Authored by a wizard-driven coding agent or
  edited in place — both land as PRs.
- **[Tools](docs/building-blocks/tools.md)** — `tools/<name>/`: reusable
  *execution*. Reviewed code (`tool.yaml` + `run.py`) the MCP broker offers to
  agents that declare it and the tool-executor runs in a locked-down
  subprocess, with declared secrets injected per call. The model picks
  arguments, never code.
- **[Secrets](docs/building-blocks/secrets.md)** — `secrets/<name>/secret.yaml`
  declares the *shape* (keys, hints, verify: declarative probe or sandboxed
  script); values live only in k8s. A heartbeat re-verifies every secret so
  status can't go stale-green.
- **[Reports](docs/building-blocks/reports.md)** — `reports/<name>/report.yaml`
  declares a class of dated HTML artifacts (generator = the write ACL,
  cadence, retention); instances are sanitized report-kit fragments in
  Postgres, browsed on a calendar and rendered in a script-free sandbox.
- **[Apps](docs/building-blocks/apps.md)** — `apps/<name>/`: full applications
  (own API/UI/schema/topics) built on agent output; declared by `app.yaml`,
  provisioned automatically, served at `/apps/<name>/` behind the platform
  session. Code, not change-loop config.
- **[Jobs](docs/building-blocks/jobs.md)** — ad-hoc "agent + prompt + cron"
  experiments in the DB; history, not config. Durable triggers graduate to
  entrypoints.
- **[Runs](docs/building-blocks/runs.md)** — every execution: live-tailed
  transcript, metrics, terminal state; rejections carry the reason.
- **[Conversations](docs/building-blocks/conversations.md)** — typed threads
  (web = continuable in the UI, discord = bridged read-only), each turn a run.
- **[Memories](docs/building-blocks/memories.md)** — per-agent namespaced
  notes with full-text search, editable in the UI.
- **[Changes](docs/building-blocks/changes.md)** — the self-edit loop for
  *capability*: every skill/tool/secret mutation becomes a commit or PR;
  deterministic editors lock on their pending change; nothing an agent writes
  goes live unreviewed. Agent definitions don't use this loop — see
  [Agents](docs/building-blocks/agents.md)'s change log instead.

Two more pages describe the platform itself:
[Glossary](docs/building-blocks/glossary.md) (the components and vocabulary
everything else assumes) and [Security](docs/building-blocks/security.md) (how
a tool call is authorized, in plain language — the engineering version is
[docs/security.md](docs/security.md)).

## Repo map

Agent identity (WHO runs — prompt, grants, entrypoints, config) is not part of
this tree: it lives in Postgres as a row per agent (`agent_defs`), edited
through the UI/API or the `agents_edit`/`agents_grant` platform tools, with an
append-only version log standing in for git history. See
[docs/building-blocks/agents.md](docs/building-blocks/agents.md) and
[docs/design/15-db-first-agents.md](docs/design/15-db-first-agents.md). There
used to be an `agents/<name>/{agent.md,manifest.yaml,entrypoints.yaml}` tree
here; it was removed from the repo once the one-time import into Postgres
succeeded.

```
agent-platform/
├── skills/                    # building block: WHAT agents can do (granted via an agent's `skills:` list)
│   └── <name>/SKILL.md        #   frontmatter (secrets + strictness) + usage instructions
├── tools/                     # building block: WHAT agents can EXECUTE (run by the tool-executor)
│   └── <name>/
│       ├── tool.yaml          #   manifest: description, JSON-schema params, infra (secrets, db)
│       └── run.py             #   the code: JSON args on stdin, result on stdout
├── secrets/                   # building block: WHAT they may touch — shape only, values live in k8s
│   └── <name>/
│       ├── secret.yaml        #   keys→env-vars, hints, required, verify (probe | script | run)
│       └── verify_*.py        #   sandboxed verify escape hatch (e.g. github-app signs a JWT)
├── reports/                   # building block: report TYPES (dated HTML artifacts agents produce)
│   └── <name>/report.yaml     #   generator (write ACL), cadence, retention — instances live in pg
├── apps/                      # full applications built on the platform (code, not change-loop config;
│   └── news/                  #   separable — depends only on the HTTP API/SDK, Kafka, and @ap/ui)
│       ├── app.yaml           #   the contract: ui/api, needs (pg schema, kafka topics), key role
│       ├── backend/           #   FastAPI + its own models; consumes app.news.inbound, owns dedup,
│       │                      #     posts the Discord digest, writes the daily-news report
│       └── frontend/          #   vite + @ap/ui browser (topic × date) served at /apps/news/
├── packages/
│   └── ui/                    # @ap/ui — THE design system: tokens.css (only legal hex),
│                              #   report-kit.css, shadcn-style primitives + their stories
├── services/
│   ├── backend/               # one image, three processes: api, dispatcher (+scheduler,
│   │                          #   verifier heartbeat, ingest), recorder — FastAPI/SQLAlchemy/Kafka
│   ├── runner/                # the agent pod: wraps `claude`, streams every event to Kafka
│   ├── web/                   # React SPA (Vite + Tailwind v4) consuming @ap/ui;
│   │                          #   Storybook workshop ships with the site at /storybook/;
│   │                          #   Playwright smoke+axe gate in tests/ (runs in CI);
│   │                          #   nginx also session-guards /apps/<name>/ (auth_request)
│   ├── mcp-broker/            # platform API + custom tools as mcp__platform__* over streamable HTTP
│   ├── mcp-facade/            # the API's OpenAPI generated into MCP tools for EXTERNAL clients (/mcp)
│   ├── tool-executor/         # runs tools/<name>/run.py in a minimal env; the single egress point
│   ├── connector-discord/     # Discord threads ↔ Conversations
│   └── connector-slack/       # placeholder (not implemented)
├── charts/agent-platform/     # the Helm chart: all Deployments incl. agents-sync (git→cluster
│                              #   pull loop) and claude-proxy (token-holding egress proxy)
├── sdk/                       # typed Python client generated from the OpenAPI spec (CI drift-checks)
├── docs/
│   ├── building-blocks/       # one concise doc per first-class citizen + the change loop
│   └── design/                # numbered design notes, one per milestone/feature
├── bin/                       # operator helpers (secret provisioning, the never-commit filename guard)
└── exports.sh.sample          # dev-only: how the operator hands Claude secret values to set via
                               #   the API while building — the platform never reads it
```

## Architecture

| Component | Role |
|---|---|
| **api** | Auth, REST + OpenAPI. Writes intent to Postgres, publishes commands to Kafka. Never touches the k8s API. |
| **dispatcher** | Consumes run requests, enforces RBAC and concurrency, creates k8s Jobs. Hosts the cron scheduler. |
| **runner** | The agent pod. Wraps `claude` and streams every event to Kafka. |
| **recorder** | Consumes events; writes transcripts, metrics, and state. |
| **web** | React SPA — dashboard, agents, runs, schedules, changes, skills, secrets, reporting. |
| **mcp-broker** | Exposes the platform API and every custom tool as MCP tools; verifies who is calling and that the caller declared the tool. |
| **mcp-facade** | The same API as MCP tools for clients outside the cluster, generated from its OpenAPI spec; forwards the caller's API key verbatim and holds no credential ([design/17](docs/design/17-external-mcp-facade.md)). |
| **tool-executor** | Runs custom tools' reviewed code with call-time secrets; the platform's only third-party egress. |
| **claude-proxy** | Holds the Claude credential and injects it per request, so runner pods never carry it. |
| **agents-sync** | Pulls this repo into the shared volume every service reads skills/tools/secrets/reports/docs from (agent definitions live in Postgres, not this volume). |
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
