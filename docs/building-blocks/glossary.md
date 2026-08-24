# Glossary

Every other page assumes these words. This one defines them once, so no doc
has to stop and explain "the dispatcher" again.

## The reference deployment

The platform is a Helm chart (`charts/agent-platform`) that can run on any
Kubernetes cluster. The deployment the docs describe — the one every example
command is written against — is a **single-node k3s cluster on a home Intel
NUC named `pai`**, installed as Helm release **`ap`**, with the UI on
`http://pai:8090`. When a doc says "the cluster", that is what it means; when
it says `ap-api` or `ap-dispatcher`, that is the release name `ap` plus a
component name.

## The workloads

Every long-running piece of the platform. All of these are Deployments in the
`agent-platform` namespace; agent runs themselves are short-lived Jobs.

| Name | Image built from | What it does |
|---|---|---|
| **api** | `services/backend` | The HTTP API and the source of truth for authorization. The UI, the agents, and the broker all talk to it. |
| **dispatcher** | `services/backend` | Consumes run commands from Kafka and launches a Kubernetes Job per run. Also runs the reconciliation heartbeat that provisions declared infrastructure (secrets, app and tool databases). |
| **recorder** | `services/backend` | Consumes transcript events from Kafka and persists runs, events, and metrics to Postgres. |
| **runner** | `services/runner` | The image an agent run *is*: it fetches the agent's definition from the API and mounts its skills, runs Claude Code inside the pod, and streams every event back to Kafka. One pod per run, then gone. |
| **web** | `services/web` | The React UI plus the nginx that serves it, terminates the login session, and proxies `/api` and `/apps/<name>/`. The only LAN-facing service. |
| **mcp-broker** | `services/mcp-broker` | The single MCP server agents talk to. It verifies who is calling and that the caller's definition declares the tool, then performs the call itself — agent pods never hold platform credentials. See [tools.md](tools.md) and [security.md](security.md). |
| **tool-executor** | `services/tool-executor` | Runs a custom tool's `run.py` in a locked-down subprocess with a minimal environment. The broker is its only client, and it is the platform's single point of third-party network egress. |
| **claude-proxy** | stock nginx + a config in the chart | Holds the Claude API credential and injects it into requests from runner pods, so the token never lands in an agent's pod. |
| **agents-sync** | stock `alpine/git` | Keeps the **synced checkout** (below) up to date with the git repository. |
| **connector-discord** | `services/connector-discord` | Bridges a Discord channel to the conversation API, so a chat message can start a run. |
| **app pods** | `apps/<name>/` | Full applications built on the platform ([apps.md](apps.md)), each with its own Postgres schema. |

## Vocabulary

- **Synced checkout** — the shared volume holding a clone of this repository,
  refreshed by agents-sync. It is what the API reads when it lists skills,
  tools, secret declarations, reports, apps, and these help pages: edit a file
  in git and the running platform picks it up on the next sync, with no
  redeploy. Agent *definitions* are the one building block that no longer
  lives here — they are rows in Postgres, see [Agents](agents.md).
- **Change loop** — the standard way *capability* changes land: an edit in
  the UI (or a wizard) has a coding agent open a **pull request** on a
  deterministic branch, which shows up under Changes for review. Nothing in
  `skills/`, `secrets/`, or `tools/` is edited in place in the cluster. See
  [changes.md](changes.md). Agent definitions do **not** use this loop —
  they write straight to their row and get their own audit trail instead
  (the **change log**, `agent_versions`; see [Agents](agents.md)). Two
  different mechanisms, both append-only, easy to conflate: the change loop
  is pre-merge review for code/config in git, the change log is a post-hoc
  record of already-live database writes.
- **Manifest** — the small YAML file that declares a block to the platform:
  `tools/<name>/tool.yaml`, a skill's frontmatter. Manifests declare *what is
  wanted*; the platform converges the cluster toward it. Agents no longer have
  a manifest file — their equivalent fields (role, skills, secrets, grants,
  limits) live directly on the `agent_defs` row.
- **Readiness gate** — the check, run before any pod launches, that an agent's
  required secrets are actually present and verified. An agent failing it is
  **blocked** and its runs are rejected with the reason recorded. See
  [agents.md](agents.md) and [secrets.md](secrets.md).
- **DLQ** (dead-letter queue) — where a run goes when the *launch* itself
  failed, rather than the agent. The DLQ page in the UI lists them for replay.
- **MCP** (Model Context Protocol) — the open protocol Claude Code uses to
  call tools hosted outside its own process. It is how agents reach the
  mcp-broker.
- **Platform agents** — agents that exist to operate the platform itself and
  are marked `system: true`: **platform-coder** (writes the pull requests
  behind every UI-driven *capability* change — skills, tools, secrets;
  agent-definition edits no longer go through it), **run-summarizer**
  (annotates finished runs), **health-monitor** (checks platform health and
  alerts), **change-summarizer** (explains pull requests in the Changes UI).
- **Kyle (project owner)** — the sole operator of the reference deployment.
  Design docs quote him directly; those quotes are the historical record of a
  decision, not instructions to the reader.

## Where the design record lives

`docs/design/00-overview.md` indexes the numbered design documents. A doc that
refers to "design 12" means `docs/design/12-executable-capabilities.md`; the
number is stable, the file name may gain words. The design docs record *why* a
thing is the way it is; these building-block pages record *what it is now*.
