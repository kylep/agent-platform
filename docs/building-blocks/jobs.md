# Jobs

**What:** ad-hoc scheduled runs — "run agent X with prompt Y on cron Z" —
created and managed entirely in the UI (Schedules page), with Run Now,
enable/disable, and plain-English cron tooltips. One agent can back many jobs,
each with its own prompt.

**Lives in:** Postgres, on purpose. Jobs are *experiments and history, not
configuration* — like a chat transcript, you spin one up, let it run for a
while, and throw it away. A trigger that becomes part of an agent's identity
should graduate into the agent's [entrypoints.yaml](entrypoints.md) via a PR.

**Shape:** `{name, agent, cron, prompt, enabled, last_fire, next_fire}` — see
the Schedules page or `GET /api/jobs`.
