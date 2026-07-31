# Entrypoints

**What:** an agent's durable, defining triggers — the ways it fires that are
part of its identity, as opposed to ad-hoc experiments (those are [Jobs](jobs.md)).

**Lives in:** git — `agents/<name>/entrypoints.yaml` (optional; no file = only
manual/API/conversation triggers):

```yaml
cron: ["*/15 * * * *", "0 9 * * 1"]   # scheduler fires the earliest upcoming
webhooks:
  - path: newsflash                    # enables POST /api/webhooks/newsflash
kafka: []                              # topic subscriptions (reserved)
```

**Rules:**

- A webhook path must be **declared** to exist — `POST /api/webhooks/<path>`
  404s unless some agent's entrypoints claim the path. An agent can't be
  webhook-fired without opting in.
- Multiple cron entries are fine; the Schedules page shows the union and the
  runtime enable/disable toggle applies to the agent as a whole.
- A broken `entrypoints.yaml` (bad YAML, invalid cron) quarantines the agent,
  same as a broken manifest — triggers are part of the definition.
- The manifest `schedule:` field is deprecated; still honored, but new cron
  triggers belong here.
