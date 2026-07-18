# Milestone 03 — Scheduling & Triggers

Every way a run starts besides a button: cron, webhooks, agents invoking
agents, and the DLQ story for when triggers misbehave.

## Scope

- **Scheduler loop** in the dispatcher: cron expressions from manifests,
  runtime enable/disable + next/last-fire in `schedules`, missed-fire
  policy (skip, never backfill), Schedules UI page.
- **Webhook listeners:** manifest-declared HTTP endpoints; api
  authenticates (per-listener token), publishes raw payloads to
  `webhooks.in`; a mapper consumer turns them into `run.requests` with
  the payload as prompt context. Queuing and backpressure come free from
  Kafka.
- **Agent-invokes-agent:** SDK/API path with the caller's key; RBAC
  decides who may invoke whom; loop guard via run-chain depth limit.
- **DLQ surfacing:** UI page for `run.dlq` with reason, payload, retry
  and discard actions.
- **Kafka health:** consumer lag + broker liveness on the dashboard
  (fuller observability waits for 05).

## Done when

A cron agent fires on schedule and is visible in advance; an external
webhook queues a run under burst load; an agent triggers another agent
within its RBAC scope; a poisoned webhook lands in the DLQ UI instead of
crash-looping anything.
