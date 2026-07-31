# Runs

**What:** one execution of an agent — the platform's unit of history. Every
trigger path (UI, cron, webhook, Discord, another agent, API) converges here:
the API writes the run to Postgres, Kafka carries the command, the dispatcher
launches a k8s Job, the runner streams every transcript event back through
Kafka, and the recorder persists it all.

**Lives in:** Postgres. Run metadata (state, timings, tokens, summary, error)
is kept forever; transcript *events* are pruned after
`transcript_retention_days` (per-agent override in the manifest).

**States worth knowing:**

- `rejected` — refused before dispatch, with the reason in `error`. This is
  where the readiness gate is visible: *"blocked: skill `discord` disabled —
  secret `discord-bot` failed verification"* means fix the secret, not the
  agent. Also used by the Claude-credential circuit breaker and the
  agent-chain depth guard.
- `dlq` — the launch failed; surfaced on the DLQ page for replay.
- `succeeded` / `failed` / `killed` / `timeout` — terminal outcomes; the
  run-summarizer agent annotates recent ones with summaries and tags.
