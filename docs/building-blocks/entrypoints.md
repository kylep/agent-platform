# Entrypoints

**What:** an agent's durable, defining triggers — the ways it fires that are
part of its identity, as opposed to ad-hoc experiments (those are [Jobs](jobs.md)).

**Lives in:** the `entrypoints` field of the agent's row (`agent_defs`, see
[Agents](agents.md)) — edited on the agent's Config tab, via `PUT
/api/agents/<name>`, or via the `agents_edit` tool. No file, no PR: an empty
shape (`crons: [], webhooks: [], topics: []`) means only manual/API/
conversation triggers.

```json
{
  "crons": [
    {"schedule": "*/15 * * * *", "prompt": ""},
    {"schedule": "0 9 * * 1", "prompt": "Morning brief."}
  ],
  "webhooks": [{"path": "newsflash"}],
  "topics": [],
  "timezone": "America/Toronto"
}
```

**Rules:**

- A webhook path must be **declared** to exist — `POST /api/webhooks/<path>`
  404s unless some agent's entrypoints claim the path. An agent can't be
  webhook-fired without opting in.
- Multiple cron entries are fine; the Schedules page shows the union and the
  runtime enable/disable toggle applies to the agent as a whole.
- Each cron entry may carry its own `prompt`; the scheduler uses it verbatim
  when non-empty, or falls back to a generic "Scheduled run." When two entries
  are due at the same tick, the one whose most recent scheduled occurrence is
  latest wins (a tie goes to the first declared) — the reason a cron carries
  its own prompt at all is so two different rhythms on one agent can ask for
  two different things.
- `timezone` applies to every cron in the entrypoints — one agent, one rhythm.
  Blank means UTC. Set it for anything pinned to wall-clock human hours, which
  would otherwise drift an hour across daylight saving; an unknown zone
  quarantines the agent like any other bad field.
- A malformed entrypoints shape (bad cron expression, unknown timezone)
  quarantines the agent, same as any other invalid field on the row —
  triggers are part of the definition.
