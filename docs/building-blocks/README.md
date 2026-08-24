# Building blocks

These pages are the platform's concept documentation, and they are also its
in-app Help: the API serves each file below straight from the synced checkout,
so editing one here updates `/help` in the running platform. New to the
vocabulary — dispatcher, runner, broker, the synced checkout — start with the
[Glossary](glossary.md).

The platform is a small set of first-class citizens. The rule that organizes
most of them (`docs/design/10-declarative-building-blocks.md`): **capability
lives in git** as self-describing folders — reviewed via Changes, versioned,
survives a cluster teardown — **runtime state lives in Postgres**, and
**secret values live in k8s Secrets** (the one thing that may never enter
git). Agents are the one exception: since
[design-15](../design/15-db-first-agents.md), agent *identity* (prompt,
config, grants, entrypoints) is Postgres too — mutable immediately, with its
own append-only change log instead of a PR. Losing the database now costs more
than history for agents specifically; everything else still reconstructs from
git.

| Block | Lives in | Doc |
|---|---|---|
| [Agents](agents.md) | Postgres (`agent_defs` + `agent_versions`) | who runs |
| [Entrypoints](entrypoints.md) | Postgres, part of the agent row | when they run |
| [Skills](skills.md) | git `skills/` | what they know how to do |
| [Tools](tools.md) | git `tools/` (+ `agents_edit`/`agents_grant` built into the broker) | what they can execute (MCP) |
| [Secrets](secrets.md) | git `secrets/` (shape) + k8s (values) | what they may touch |
| [Reports](reports.md) | git `reports/` (types) + Postgres (artifacts) | what they produce for humans |
| [Apps](apps.md) | `apps/` (code + manifest — NOT change-loop) | full applications built on agents |
| [Jobs](jobs.md) | Postgres | ad-hoc scheduled experiments |
| [Runs](runs.md) | Postgres | every execution, forever |
| [Conversations](conversations.md) | Postgres | threaded chat with agents |
| [Memories](memories.md) | Postgres | what agents remember |
| [Changes](changes.md) | GitHub PRs | how capability changes land |

Two pages describe the platform rather than a block of it:

| Page | What it covers |
|---|---|
| [Glossary](glossary.md) | the components and vocabulary every other page assumes |
| [Security](security.md) | how a tool call is authorized, in plain language |
