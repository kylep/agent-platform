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

## Progress (2026-07-20)

- [x] **Scheduler loop** — agents declare a 5-field cron `schedule` in their
      manifest; a `Scheduler` in the dispatcher fires a `trigger=schedule` run
      when due, tracking enable/disable + last/next fire in the `schedules`
      table. Missed fires are skipped (next fire computed from now, never
      backfilled). `GET/POST /api/schedules` + a Schedules UI page. Verified
      live: a cron agent armed, fired on schedule, and disable/enable worked.
- [x] **Webhook triggers** — `POST /api/webhooks/{agent}` fires an agent from
      an external caller, authed by an operator+ `ap_` API key, with the body
      as prompt context; queuing comes from the existing dispatcher path.
      Verified live (operator key → webhook run succeeded). (Lean MVP: direct
      run creation rather than the webhooks.in→mapper topic in the full spec.)
- [ ] Agent-invokes-agent (RBAC + run-chain depth loop guard).
- [ ] DLQ surfacing UI.
- [ ] Kafka health on the dashboard.

## Done when

A cron agent fires on schedule and is visible in advance; an external
webhook queues a run under burst load; an agent triggers another agent
within its RBAC scope; a poisoned webhook lands in the DLQ UI instead of
crash-looping anything.
