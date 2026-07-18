# Milestone 05 — Observability & Health

From "it works" to "I can see it working": metrics, trends, and one page
that answers "is everything okay?"

## Scope

- **Run metrics rollups:** per-agent and platform-wide aggregates
  (runs/day, success rate, duration, token usage, tool-call profiles)
  computed by the recorder, browsable per agent and platform-wide.
- **Reporting page:** overall system health — service liveness, Kafka
  broker + consumer lag, postgres size/connections, PVC usage, token
  validity, schedule adherence, DLQ depth.
- **Alerting hook:** threshold rules (DLQ depth, lag, failure streaks)
  publishing to a notification skill (discord first).
- **Log retention:** transcript pruning policy with per-agent overrides.

## Done when

The dashboard answers platform health at a glance; a failure streak on
any agent produces a Discord ping; disk usage is bounded by policy.
