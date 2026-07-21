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

## Progress (2026-07-20)

- [x] **Run-metrics rollups** — `GET /api/metrics/overview` (platform-wide:
      totals, state histogram, success rate, 24h/7d counts, token sums, avg/max
      duration, dlq depth) and `GET /api/metrics/agents` (per-agent aggregates +
      `failure_streak`). Portable Python aggregation over a bounded recent
      window. Verified live over real run history.
- [x] **Reporting page** — one page with health stat cards (broker, dispatcher
      lag, active runs, dlq) + run stats (success rate, throughput, duration,
      tokens) + per-agent table with failure-streak highlighting. Kafka health
      reused from the M03 `/api/health/kafka` probe.
- [x] **Alerting hook** — `health-monitor` system agent (15-min cron, Sonnet,
      memory + `discord` skill) evaluates threshold rules (per-agent
      `failure_streak >= 3`, dlq depth, kafka lag/liveness), de-dupes against a
      remembered `alert-state`, and pings Discord on new breaches. (Delivery
      needs the `discord-webhook` secret set; detection/logic runs regardless.)
- [x] **Log retention** — `TranscriptPruner` deletes `run_transcript_events`
      past their agent's retention (per-agent manifest
      `transcript_retention_days` override, else the platform default; <= 0 keeps
      forever), keeping Run metadata/summary/metrics. Runs daily in the
      dispatcher's gather loop; `GET /api/maintenance/retention` shows the
      effective per-agent windows and `POST /api/maintenance/prune-transcripts`
      triggers it on demand (with a button on the Reporting page).

## Done when

The dashboard answers platform health at a glance; a failure streak on
any agent produces a Discord ping; disk usage is bounded by policy.
