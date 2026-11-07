# Jobs

For a single future run rather than a recurrence, use [Tasks](tasks.md).

**What:** ad-hoc scheduled runs — "run agent X with prompt Y on cron Z" —
created and managed entirely in the UI (Schedules page), with Run Now,
enable/disable, and plain-English cron tooltips. One agent can back many jobs,
each with its own prompt. The job form uses the same schedule builder as an
agent's [entrypoints](entrypoints.md), and every English description of a cron
anywhere in the UI comes from `GET /api/cron/preview` — one renderer, beside
the scheduler that fires it.

**Lives in:** Postgres, on purpose. Jobs are *experiments and history, not
identity* — like a chat transcript, you spin one up, let it run for a while,
and throw it away. A trigger that becomes part of an agent's identity should
graduate into the agent's [entrypoints](entrypoints.md) — edited directly on
the agent's Config tab, no PR needed.

**Shape:** `{name, agent, cron, timezone, prompt, model, run_when, enabled,
last_fire, next_fire}` — see the Schedules page or `GET /api/jobs`. `model`
overrides only this job's run, leaving the agent's normal conversations on its
configured model. `run_when` is normally `always`. For a persona's Discord
awareness job, `discord_unaddressed` checks its owned, currently readable guild
channels for new human posts and skips the model run on a quiet tick. The
persona acknowledges each bounded batch using the `discord` Tool after triage.

**Skipped fires (ENG-14):** a job that runs an agent is skipped without a run in two cases. The clock still advances, and the reason is shown as `skip_reason` / `skipped_at`.
- **Agent unavailable:** its agent no longer exists or is disabled.
- **Repeated failures:** after 3 consecutive failed runs (failed, timed out, rejected or DLQ), the job backs off. It fires at most once per 1h, 2h, 4h… after the last failure, capped at 24h. A success resets the backoff.

`failure_streak` shows the current count. Models are not preflighted, because jobs accept aliases that the runtime resolves.

**Timezones:** `timezone` is an IANA zone (`America/Toronto`) that the cron is
read in; blank means UTC, and stored times are always UTC either way. Set it
for anything pinned to human hours — "9:35 on a weekday" is a different UTC
instant in July and December, so a market-open or business-hours job left on
UTC silently slides an hour every daylight-saving switch. Changing the zone
re-arms `next_fire` on the scheduler's next tick.
An agent's entrypoints take the same key, once for all of its cron entries.
