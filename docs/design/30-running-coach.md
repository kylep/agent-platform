# 30 — Running Coach

Status: **implemented 2026-09-22**; replaces the quota-heavy `running` ETL
agent while retaining its run history and the Running app's existing data.

## The problem

The Running app looked healthy while containing no activities. Its daily agent
successfully fetched Strava, then copied up to roughly 9 KB of activity JSON
through a language model into Kafka. That made deterministic data movement cost
model quota and money. When Claude's weekly allowance was exhausted the sync
stopped entirely. Meanwhile the app's Kafka consumer group had no active member
and the UI rendered plausible zeroes instead of exposing the broken pipeline.

The old agent also mixed two jobs with different needs: moving records and
writing a coaching note. The former needs no reasoning; the latter benefits
from a model after the app has computed bounded, trustworthy context.

## Decision

The read-only `strava` tool gains an explicit `sync` action. It fetches activity
pages, normalizes them, and publishes `running.activities.synced` directly to
`app.running.inbound`. Kafka access is opt-in tool infrastructure: the executor
only injects `AP_KAFKA_BOOTSTRAP` when a reviewed tool manifest declares
`infra.kafka: true`. The tool returns a small receipt to its caller and never
copies the archive through model output.

The app consumes with a new `running-coach-app-v2` group, so it can rebuild its
database from retained activity messages. Historical coaching prose has no
trustworthy week in the legacy envelope and is discarded during replay; it is
never relabelled as current or posted to Discord. Activity upserts remain
idempotent by Strava id.

The `running-coach` persona has two scheduled invocations:

- A daily Codex Luna sync reads the app's overlap cursor and calls the tool's
  deterministic sync action.
- A Monday Codex Terra coaching run reads `/coach-context`, which contains
  the last completed Monday–Sunday week's running totals and runs, plus bounded
  longer trends. It produces a short note from this context. The app validates
  and stores the note under that completed week's Monday. Its existing
  first-post guard controls Discord delivery.

The former `running` agent was disabled during migration, then deleted after
the replacement was verified. Its run history and definition change log remain
available. The replacement is a normal agent because it is a user-facing
persona, not platform machinery.

## Health and presentation

`/apps/running/api/health` reports consumer assignment, the latest sync, row
counts, and a bounded error state. An empty app now distinguishes a connected
pipeline waiting for its first sync from a degraded consumer. It hides empty
charts and records instead of presenting zeros as real training data.

The app's stable technical name remains `running`; the registry now supports a
`display_name`, shown as **Running Coach** in navigation and app listings. This
keeps routes, database names, and deployment names compatible.

## Failure boundaries

- Strava OAuth refresh remains solely inside the Strava tool and its private
  database. The app never receives credentials or third-party egress.
- A sync failure fails the small agent run and publishes no partial event.
- Kafka publication is limited to reviewed tool code and is not inherited by
  tools that omit `infra.kafka`.
- The consumer commits a batch after handling every message. A malformed item
  is logged and skipped so it cannot permanently poison the partition.
- Coaching input is app-computed and bounded; all model output is still parsed,
  clamped, mention-sanitized, and restricted to a closed tag vocabulary.
