# Milestone 01 — Walking Skeleton

One `helm install` brings up the platform; a hello-world agent runs end to
end from a UI click; the live transcript streams in the browser.
Proves the spine: dispatch → runner → subscription auth → stream capture →
persistence → live UI.

## In scope

**Chart.** `charts/agent-platform` umbrella: postgres and single-node
KRaft Kafka as dependencies; api, dispatcher, recorder, web as subcharts;
`values-pai-nuc.yaml` with the sizing from the overview. Topics created by
an init job: `run.requests`, `run.events`, `run.transcript`, `run.dlq`.

**api.** FastAPI with:
- `setup_state` + one-time admin creation; argon2 password, session cookie.
- Secrets API backed by k8s Secrets (create/update/probe); `secrets_meta`
  in postgres. Claude-credentials probe = spawn a minimal smoke run.
- Agents (read-only this milestone): list/detail from the synced git
  checkout. Sync = init-container + periodic pull of main into a shared
  volume; manifest schema validation with per-agent quarantine.
- Runs: POST creates the `runs` row, publishes `run.requests`; GET
  list/detail; websocket live-tail bridging `run.transcript`.
- OpenAPI served; no SDK generation yet.

**dispatcher.** Consume `run.requests`, idempotency check against `runs`,
global + per-agent concurrency caps, create the k8s Job, watch it,
publish lifecycle events, honor cancel commands, enforce the 30m default
timeout. Failures → `run.dlq` with reason.

**runner.** Image with claude CLI pinned + a small wrapper: mount
subscription credentials read-only (copy, never write back), mount the
synced agent definitions, exec `claude --agent <name> -p <prompt>
--output-format stream-json`, relay every event + exit status to Kafka.

**recorder.** Consume `run.events` + `run.transcript` + `run.dlq`, upsert
run state, append transcript events, compute per-run duration/token/tool
counts.

**web.** React SPA: setup flow, required-secrets gate (banner + redirect
to Settings→Secrets until probes pass), agent list/detail (rendered
agent.md + manifest), run-now button, runs table, run detail with live
transcript and kill button, minimal dashboard (service health, token
status, recent runs).

**Seed content.** `agents/hello-world/` — trivial agent proving the loop.

**bin/set-claude-token.sh.** Reads local `~/.claude/.credentials.json` or
prompts; pre-boot mode (`kubectl create secret`) and post-boot mode
(secrets API).

**docs/setup.md.** Install, first launch, token setup, smoke test.

## Out of scope

Git writes (02), scheduling/webhooks (03), memory + skills + SDK (04),
metrics rollups (05), hardening (06). API keys for non-admin principals
land in 02 with RBAC.

## Verification checklist

- [ ] Fresh k3s: `helm install` → all pods Ready within resource requests.
- [ ] First browser visit forces admin creation, then secrets gate.
- [ ] Invalid token → probe fails → UI stays gated; valid token unlocks.
- [ ] Run-now on hello-world: state walks queued→dispatched→running→succeeded;
      transcript streams live; tool calls render; totals recorded.
- [ ] Kill button terminates a running agent; state = killed.
- [ ] Concurrency: 4 simultaneous requests with global cap 3 → one queued.
- [ ] Kafka down → runs queue as `queued` (postgres row exists), drain on
      recovery; nothing lost.
- [ ] Subscription-only check: CI grep for `ANTHROPIC_API_KEY` /
      `sk-ant-` passes; runner env contains no API key.
- [ ] Token refresh behavior observed and documented (steward go/no-go).

## Open questions to resolve during build

- Exact credentials file path/format the pinned CLI expects (verify, don't
  assume; changes across CLI versions).
- Whether stream-json event schema needs normalization before storage
  (store raw + parsed columns vs parsed only).
