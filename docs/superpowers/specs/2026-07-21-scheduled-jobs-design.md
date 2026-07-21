# Scheduled Jobs (1:many) + daily news

## Problem

Scheduling is 1:1 today: the `schedules` table is keyed by agent name, the cron
lives in the agent's `manifest.yaml`, and every scheduled run fires the same
hardcoded prompt `"Scheduled run."` (`scheduler.py`). So a recurring *task* and
an *agent* are the same thing — to run two different scheduled prompts you need
two agents.

We want **1:many**: one agent backing many jobs, each with its own cron and
prompt. Concretely, the first use is a **daily news** job on the `pai` agent
that web-searches, dedups against what it already shared, and posts new items to
Discord `#news` — porting the behavior of multi's `journalist`.

## Design

### Scheduled Jobs as first-class DB entities

A **Job = {id, name, agent, cron, prompt, model?, enabled, last_fire,
next_fire}**, stored in a new `scheduled_jobs` table and managed from the UI
(created/edited/deleted like Conversations and Secrets — not git-declared).
Rationale: jobs are operational state the user tunes at runtime (enable, Run
Now, tweak the prompt), and the user explicitly wants to manage them from the UI.

- `agent` supplies the *capabilities* (skills, tools, memory namespace, default
  model). `cron` + `prompt` are the *task*. Optional `model` overrides the
  agent's model for this job (so news can run on Sonnet while pai chats on Opus).
- One agent → many jobs.

### Scheduler

`Scheduler.tick` iterates enabled `ScheduledJob` rows (not agent manifests).
When a job is due it publishes `run.requested` to `run.inbound` with the job's
`agent`, `prompt`, `trigger="schedule"`, optional `model`, and `job_id`. Missed
fires are still skipped-not-backfilled (next_fire computed from now).

### Migration / back-compat

Agents may still declare `schedule:` in their manifest (e.g. `health-monitor`
`*/15`). On tick, for any agent whose manifest has a valid cron and which has no
job yet, the scheduler **seeds** a job `{name: "<agent> (manifest)", agent,
cron, prompt: "Scheduled run."}`. This preserves existing scheduled agents while
moving the source of truth to `scheduled_jobs`. Manifest `schedule:` thus
becomes a convenience seed, not the store.

### Materialize / run

`materialize_run` already accepts an optional `model` override path; the
ingestor passes the job's `model` through so per-job model overrides take
effect. Runs carry `trigger="schedule"`.

### API (`/api/jobs`, admin)

- `GET /api/jobs` — list jobs joined with runtime state.
- `POST /api/jobs` — create `{name, agent, cron, prompt, model?}` (422 on bad
  cron / unknown agent).
- `PATCH /api/jobs/{id}` — edit any of name/cron/prompt/model/enabled.
- `DELETE /api/jobs/{id}`.
- `POST /api/jobs/{id}/run` — **Run Now**: materialize a run immediately from
  the job's (agent, prompt, model), `trigger="manual"`. Returns the run id.

The legacy `/api/schedules` endpoints are replaced by `/api/jobs` (the
Schedules page is reworked to Jobs).

### UI — Schedules page becomes Jobs

- List: name, agent, cron (with a **plain-English tooltip** via `cronstrue`,
  e.g. `0 11 * * *` → "At 11:00 AM"), next/last fire, enabled toggle.
- **Run Now** button per job → calls `/run`, links to the created run.
- Create / edit form: name, agent (select), cron (with live plain-English
  preview + validation), prompt (textarea), optional model.

### Daily news job (part B)

`pai` gains the capabilities to run it: `memory: true` (dedup store + unattended
tool use), `skills: [discord]` (webhook post), and Bash added to its tools
(curl the memory + webhook). Its `agent.md` stays a warm assistant but is told
to follow a task prompt when given one.

A job `morning-news` (agent `pai`, cron chosen by Kyle, optional `model:
sonnet`) whose prompt ports multi's journalist workflow: recall shared memories
→ read recent `#news` (via the bot token) → web-search the topic set → curate by
the significance rubric + watchlist, dropping already-shared (keep "material
update") → post new items to `#news` → save shared items to memory (key = URL,
idempotent) → prune >14 days.

Dedup mapping: multi re-read its last ~15 `#news` posts + a git wiki file; here
the durable per-agent **Memory API** is the primary store (queryable, prunable),
with `#news` re-reading as a backstop.

Prereqs: the `discord-webhook` secret (a `#news` webhook) must be set; reading
`#news` uses the existing valid `discord-bot` token.

## Testing

- scheduler: fires due jobs (not manifest), seeds a job from a manifest cron,
  skips disabled, computes next_fire from now, passes model override.
- jobs API: create/edit/delete/run-now; bad cron and unknown agent rejected.
- run-now materializes a run with the job's prompt/model.
- UI build typechecks; cron tooltip renders.

## Out of scope (YAGNI)

Per-job concurrency limits; job run history views beyond existing Runs;
git-declared jobs; porting multi's gnews.io MCP (WebSearch-only v1).
