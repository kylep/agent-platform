# Design 29 — Agent editor terminology

## Problem

The DB-first editor exposed storage names directly. `role` implied API RBAC
even though only `coder` and `dev` change execution, `result_topic` hid its
Kafka/app purpose, and timeout/concurrency lacked enough context to predict
queueing and termination. Runtime-specific and shared grants were mixed into
one long form.

## Decision

Keep the database and API compatible while presenting the concepts people
operate:

- `role` is **Execution profile**: Standard, Workbench developer, or Legacy
  self-editor. API authority is derived from platform-tool grants.
- `result_topic` is **App output topic**, distinct from Kafka input topics.
- `timeout_seconds` is **Run time limit** and `concurrency` is **Parallel runs**.
- Claude Code tool switches appear only for Claude agents. Skills, platform
  tools, secrets, and invocation authority are explicitly shared across
  runtimes.
- Workbench publishing controls appear only for the Workbench profile. Quota
  thresholds appear only when `quota_ok` is granted.
- Every editor concept has contextual, keyboard-accessible modal help linked
  to the durable Agents help page.

The API continues accepting legacy `reader` and `annotator` definitions. The
editor preserves and labels them, but does not create new ones.

## Semantic repairs

Concurrency and timeout are strictly positive. Kafka topic names are validated
before launch. Result routing reads the run's frozen definition snapshot so an
edit made during a run cannot redirect its output; rows created before
invocation snapshots retain the live-definition fallback.
