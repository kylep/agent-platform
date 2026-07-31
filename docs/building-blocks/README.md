# Building blocks

The platform is a small set of first-class citizens. The rule that organizes
them (docs/design/10): **configuration lives in git** as self-describing
folders — reviewed via Changes, versioned, survives a cluster teardown —
**runtime state lives in Postgres**, and **secret values live in k8s Secrets**
(the one thing that may never enter git). Losing the database costs history,
never configuration.

| Block | Lives in | Doc |
|---|---|---|
| [Agents](agents.md) | git `agents/` | who runs |
| [Entrypoints](entrypoints.md) | git `agents/<name>/entrypoints.yaml` | when they run |
| [Skills](skills.md) | git `skills/` | what they can do |
| [Secrets](secrets.md) | git `secrets/` (shape) + k8s (values) | what they may touch |
| [Jobs](jobs.md) | Postgres | ad-hoc scheduled experiments |
| [Runs](runs.md) | Postgres | every execution, forever |
| [Conversations](conversations.md) | Postgres | threaded chat with agents |
| [Memories](memories.md) | Postgres | what agents remember |
| [Changes](changes.md) | GitHub PRs | how config changes land |
