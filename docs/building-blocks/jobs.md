# Jobs

**What:** ad-hoc scheduled runs — "run agent X with prompt Y on cron Z" —
created and managed entirely in the UI (Schedules page), with Run Now,
enable/disable, and plain-English cron tooltips. One agent can back many jobs,
each with its own prompt.

**Lives in:** Postgres, on purpose. Jobs are *experiments and history, not
configuration* — like a chat transcript, you spin one up, let it run for a
while, and throw it away. A trigger that becomes part of an agent's identity
should graduate into the agent's [entrypoints.yaml](entrypoints.md) via a
pull request ([Changes](changes.md)).

**Shape:** `{name, agent, cron, timezone, prompt, enabled, last_fire,
next_fire}` — see the Schedules page or `GET /api/jobs`.

**Timezones:** `timezone` is an IANA zone (`America/Toronto`) that the cron is
read in; blank means UTC, and stored times are always UTC either way. Set it
for anything pinned to human hours — "9:35 on a weekday" is a different UTC
instant in July and December, so a market-open or business-hours job left on
UTC silently slides an hour every daylight-saving switch. Changing the zone
re-arms `next_fire` on the scheduler's next tick.
`entrypoints.yaml` takes the same key, once for all of its cron entries.
