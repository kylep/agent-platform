# Runs

**What:** one execution of an agent — the platform's unit of history. Every
trigger path (UI, cron, webhook, Discord, another agent, API) converges on the
same pipeline: the **api** writes the run to Postgres, Kafka carries the
command, the **dispatcher** launches a Kubernetes Job, the **runner** (the pod
the agent actually runs in) streams every transcript event back through Kafka,
and the **recorder** persists it all. Those component names are defined once in
the [Glossary](glossary.md).

**Lives in:** Postgres. Run metadata (state, timings, tokens, summary, error)
is kept forever; transcript *events* are pruned after
`transcript_retention_days` (a per-agent override on the agent's definition,
see [agents.md](agents.md)).

**States worth knowing:**

- `rejected` — refused before a pod was ever launched, with the reason in
  `error`. This is where the [readiness gate](glossary.md) is visible:
  *"blocked: skill `git` disabled — secret `github-token` failed
  verification"* means fix the secret, not the agent. Two other guards reject
  here too: the circuit breaker that stops launching runs when the Claude
  credential is failing, and the depth guard that stops agents invoking each
  other in an unbounded chain.
- `dlq` — the *launch itself* failed (see dead-letter queue in the
  [Glossary](glossary.md)); the DLQ page in the UI lists these for replay.
- `succeeded` / `failed` / `killed` / `timeout` — terminal outcomes. The
  run-summarizer platform agent annotates recent ones with summaries and tags.
