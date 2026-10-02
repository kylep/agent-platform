# 38 — Judgment: Kai's private prediction-and-feedback loop

Status: **designed 2026-10-01**, revised after review by Fable, Sonnet, Sol
and Astra. From ticket ENG-8, which Kai filed at Kyle's request. Kyle's
decisions: build it as an App; v1 is for Kai only. The build plan is
`docs/superpowers/plans/2026-10-01-judgment-app.md`.

## The problem

Kai is Kyle's digital twin: its job is to approximate Kyle's perspective.
Its only store today is private memory (`tools/memory`), which keeps
plausible claims but cannot tell these apart:

- a **prediction** made before Kyle decided, versus an **explanation**
  written afterwards that happens to fit;
- something **Kyle said**, something **Kyle confirmed**, something Kai
  **inferred**, and text **imported** from another assistant;
- a claim that was **corrected**, versus one that was never tested.

Without those distinctions Kai can only grow more confident, not less wrong.
ENG-8 asks for a way to record what Kai thinks Kyle would choose before he
chooses, keep that record honest, attach the feedback Kyle chooses to share,
and revise beliefs with a visible trail.

## What this delivers

- **A private store** of beliefs (versioned), predictions (immutable) and
  feedback, readable only by Kai (through its tool) and Kyle (through his
  page).
- **A `judgment` tool**, granted to Kai and refused to every other caller,
  with five actions: `belief`, `predict`, `feedback`, `recall`, `pending`.
- **Kyle's page** at `/apps/judgment/`: inspect, confirm, correct, reject and
  delete anything, and read an honest review with counts and no accuracy
  score.

Not delivered: reminders, automatic collection of Kyle's decisions,
analytics, a general memory rewrite, or any authority for Kai to act as Kyle.

## Starting point (review)

### Apps and custom tools
An App (designs 11 and 33) is a separately deployed service under
`apps/<name>/` with its own Postgres schema `app_<name>`, created by the
dispatcher's `AppProvisioner`, which also writes the `app-<name>-db` secret.
The App runs its DDL at startup (`metadata.create_all`). A custom tool can
bind that secret (`infra.secrets: [app-<name>-db]`) and write the App's
tables directly. TCMS (design 25) and stockmarket's `prices` are the
precedents.

### Who can reach an App today
- **Browser:** nginx admits **any authenticated principal** to
  `/apps/<name>/`, whether a session or an `ap_` API key of any role
  (`api/apps.py` `auth_check`), and forwards `X-AP-User` / `X-AP-Role`. The
  NetworkPolicy lets only web and api reach App pods, so those headers can't
  be forged in-cluster.
- **`query_app`:** any agent holding `mcp__platform__query_app` (most of
  them, Kai included) can GET any App with `api: true`. The App sees
  `X-AP-User: <agent>` and always `X-AP-Role: reader`.
- **Custom tools:** the broker checks the run token's frozen tool list, and
  the executor sets `TOOL_CALLER_AGENT` / `TOOL_RUN_ID` from the verified
  token, never from model input. A grant is not an identity, though: Kai
  holds `agents_grant` and could grant the tool to another agent, and an
  admin principal calling the broker directly skips the grant check
  (`tools: None`, empty `TOOL_RUN_ID`).

### Kai
Kai is a live-only persona row (no seed in the repo). It runs on Codex with
`role: operator` and 26 platform tools, including `memory`, `wiki`,
`query_app`, `agents_grant` and `agent_self`. Any Relay or Discord message
that mentions Kai can start a Kai run. Kai already sends Kyle a daily 09:00
question in Discord and saves his answers as private memories.

## Decision

An App named `judgment` with Postgres, a UI and an API that serves only
Kyle's own session, plus a custom tool `judgment` that Kai uses for every
read and write. No agent ever reads the App's API; the tool serves only the
owner agent.

### Why these choices
- **An App, not platform tables.** The data has its own UI and a deletion
  policy. An App keeps it out of the platform's memory, wiki and search
  indexes by construction, and survives the planned split of Apps into
  their own repo. No platform code changes are needed beyond the lockstep
  registries.
- **Kyle-only App API.** It keeps `query_app`, reader logins and admin API
  keys out without a new per-App grant mechanism. Kai doesn't need the API:
  the tool gives it everything.
- **Identity check in the tool, not just the grant.** The grant can be
  re-granted by Kai or bypassed by an admin; `TOOL_CALLER_AGENT` plus a
  non-empty `TOOL_RUN_ID` is the trustworthy pair, the same one the memory
  tool relies on.
- **No Kafka, no agent key.** Nothing outside the App needs to hear about a
  belief, and the App never calls the platform API.

## Naming

App `judgment` (schema `app_judgment`, secret `app-judgment-db`, deployment
`ap-app-judgment`, image `agent-platform-app-judgment`). Tool `judgment`
(grant `mcp__platform__judgment`). Two constants in the App's `schema.py`,
which the tool loads by path: `OWNER_AGENT = "kai"` and, read from the App's
environment, `JUDGMENT_OWNER_PRINCIPALS` (default `admin`, the principal
Kyle's login session uses). `schema.py` imports only the standard library,
because the tool-executor image doesn't carry SQLAlchemy.

## Data model (`app_judgment`)

All timestamps are server-set UTC. Text fields are bounded (claim 1,000
characters, `source_ref` 200, others 4,000) so "minimal necessary evidence"
is enforced rather than hoped for. `source_ref` has a strict format:
`relay:<channel id>/<message id>` (32 hex each) or
`discord:<channel id>/<message id>`. The page links a Relay ref to its room
and thread so Kyle can check it, and shows a Discord ref as its ids (a
server-channel link would need the server id).

**`beliefs`**
- `id`, `created_at`, `status` (`active` | `superseded` | `rejected`),
  `current_version`.

**`belief_versions`**, append-only and never updated:
- `belief_id`, `version` (`UNIQUE(belief_id, version)`), `claim`, `scope`
  (the context it holds in, including exceptions), `evidence` (short
  paraphrase), `provenance`, `source_ref`, `confidence`
  (`low` | `medium` | `high`), `reason` (why this version exists),
  `feedback_id` (the feedback that prompted it, if any), `author`
  (`agent:kai` or `user:<principal>`, always derived server-side), and
  `created_at`.
- `provenance` is one of:
  - `kyle_confirmed`: Kyle confirmed this exact claim on his page. Only a
    `user:` author can write it; the tool refuses it.
  - `kyle_relayed`: Kai reports Kyle said it. Requires `source_ref`.
  - `observed`: a pattern Kai noticed in Kyle's own messages.
  - `inference`: Kai's reasoning, not something Kyle said.
  - `imported`: text from another assistant or an export. Never
    confirmation, whatever it claims.
- A new version takes the belief row's lock (`SELECT … FOR UPDATE`) and
  writes the version and the `current_version` pointer in one transaction.
  The tool's `belief` action takes `expected_version` and refuses on a
  mismatch.
- **Kai cannot bury a confirmed belief.** The tool refuses to set
  `rejected` or `superseded` on a belief that has any `kyle_confirmed`
  version. Kai may still add versions on top, and `recall` always shows the
  latest confirmed version beside the current one.

**`predictions`**, immutable once written:
- `id`, `created_at`, `scenario`, `alternatives` (a JSON list, stored as text
  for SQLite parity), `predicted_choice`, `rationale`, `confidence`,
  `timing` (`prospective` | `retrospective`), `question_ref` (optional
  `source_ref` of the question Kai sent Kyle about it, which links the daily
  question to its answer), and `author`.
- `outcome_known` is required on `predict`. `true` stores `retrospective`.
  **Prospective timing is Kai's attestation, not a proof**: the server
  timestamp prevents backdating the record, but not a prediction written
  after Kai already heard the answer. The review flags two patterns:
  - feedback whose `source_at` is before the prediction's `created_at`;
  - feedback that arrives within ten minutes of the prediction.
- `prediction_beliefs` links a prediction to the belief versions it relied
  on.

**`feedback`**: what Kyle shared, kept apart from what Kai made of it.
- `id`, `created_at`, `prediction_id`, and/or `belief_id` plus
  `belief_version` (the exact version it speaks to), `kyle_words` (his
  words or a minimal paraphrase), `source_ref`, `source_at`, `outcome`
  (`supported` | `contradicted` | `mixed` | `context_changed` |
  `unresolved`), `interpretation` (Kai's reading), `author`, and
  `confirmed_at`.
- Feedback from the tool is **relayed**: `author = agent:kai`, with
  `confirmed_at` null and `source_ref` required. Kyle confirms it on his
  page, which confirms his words and the outcome but not Kai's
  interpretation. Feedback Kyle writes on his page is confirmed on
  creation.
- No response is never feedback. Nothing ages into `supported`.

**Resolution.** A prediction is **resolved** when it has at least one
feedback item whose outcome is not `unresolved`. When several resolved items
disagree, the review shows all of them and counts the prediction as `mixed`.
`pending` lists prospective predictions that aren't resolved, including ones
with only `unresolved` feedback.

## The `judgment` tool (`tools/judgment/`)

`category: domain_capability`, `infra.secrets: [app-judgment-db]`,
psycopg (`psycopg[binary]` in `requirements.txt`), connecting like
`tools/tcms`. Every call first checks `TOOL_CALLER_AGENT == OWNER_AGENT` and
a non-empty `TOOL_RUN_ID`, and refuses otherwise. That covers a re-granted
agent and an admin calling the broker directly.

| action | kind | what it does |
|---|---|---|
| `belief` | write | No `id`: a new belief at version 1. With `id` and `expected_version`: a new version (reword, narrow, broaden) or, with `status`, a status change. Always requires `reason` on revisions. Refuses `kyle_confirmed`, requires `source_ref` for `kyle_relayed`, and refuses status changes on a Kyle-confirmed belief. |
| `predict` | write | A new prediction with `outcome_known`, linked belief ids (pinned to their current versions), and an optional `question_ref`. |
| `feedback` | write | Relayed feedback on a prediction and/or a belief version, with `kyle_words`, `source_ref`, `source_at`, `outcome` and `interpretation`. |
| `recall` | read | With `query`: matching beliefs, each with its current version, the latest Kyle-confirmed version if different, provenance, confidence, a one-line-per-version trail, and any contradicting or mixed feedback. With `id`: one belief, prediction or feedback item in full with its links. |
| `pending` | read | Prospective predictions that aren't resolved (oldest first), plus contradicted or mixed feedback that no belief version cites yet ("awaiting revision"). |

- **Writes** take an optional `request_id`. A repeat with the same id
  returns the first result instead of writing twice, so a retried call is
  safe. Writes print a minimal JSON receipt (`{"ok": true, "id", "version",
  "timing"}`), never the stored text, which keeps run transcripts lean.
- **Reads** print plain text inside a `<judgment-records>` block marked as
  untrusted data, the way Relay history is, so stored text can't act as an
  instruction.
- The tool never accepts `author`, `confirmed_at` or `timing` as inputs.
- There is no delete. Kai can supersede or reject; erasing is Kyle's.
- The review is Kyle's page only. Kai has no `review` action, so it isn't
  optimising against a score it can read.

## The App (`apps/judgment/`)

FastAPI + SQLAlchemy async in the TCMS layout: `judgmentapp/{main,api,db,
schema}.py`, DDL at startup, and tests on aiosqlite with an opt-in Postgres
run through `APP_DB_URL`. `app.yaml` sets `ui: true`, `api: true` and
`needs.postgres: true`, with no Kafka and no agent key.

**Authorization:** every API route requires `X-AP-Role == "admin"` **and**
`X-AP-User` in `JUDGMENT_OWNER_PRINCIPALS`. That admits Kyle's login session
and refuses reader logins, `query_app` (always `reader`), app keys and admin
API keys, each with its own test. `author` is stamped server-side as
`user:<X-AP-User>`; the frontend never sends it.

**One page with tabs** (Vite + React + `@ap/ui`, workspace
`judgment-frontend`):
- **Beliefs:** a list with status, provenance and confidence. A belief's
  detail shows its full version trail with source links, its predictions
  and its feedback. Actions: confirm (writes a `kyle_confirmed` version of
  the current claim), correct (a new confirmed version in Kyle's words),
  reject, delete.
- **Predictions:** pending and resolved, each with its original text,
  pinned belief versions, feedback and flags. Actions: add feedback
  (confirmed on creation), delete.
- **Feedback:** relayed items waiting for confirmation. Actions: confirm,
  edit Kyle's words (which confirms), delete.
- **Review:** counts first, then each resolved prospective prediction with
  its expectation, feedback and outcome. The counts are: prospective
  resolved, broken down by outcome and by confirmed versus relayed;
  prospective pending; retrospective; flagged; relayed and unconfirmed.
  There is no accuracy percentage.

## Deletion policy

Deletion overrides history inside the App, and the page says plainly what it
can't reach.

- **Belief:** deletes the belief, every version and its prediction links.
  Predictions that relied on it keep their own text and show
  "linked belief deleted". Feedback that targeted the belief loses the
  link; if it targeted nothing else, it is deleted.
- **Prediction:** deletes it, its links, and feedback that targeted only it.
  Feedback that also targeted a belief version survives with the prediction
  link cleared.
- **Feedback:** deletes it **and every belief version that cites it**, since
  a derived claim can repeat its words. The belief's `current_version` falls
  back to the newest remaining version, and a belief left with no versions
  is deleted. Before confirming, the page lists what will go.
- Every delete is an explicit `DELETE` sequence in one transaction rather
  than a database cascade, so SQLite and Postgres behave the same.

What deletion doesn't reach:
- **Run transcripts.** Kai's tool calls and their outputs are stored in its
  run transcripts, which only admins can read, and kept until Kai's
  transcript retention expires. Write receipts carry ids only. `recall`
  output does carry text, so Kai's retention setting is the bound.
- **Backups.** The encrypted cloud backups (`c00fb37`) include Postgres. A
  deleted record survives in older backups until they rotate out, and
  restoring one brings it back. The page says this next to the delete
  button, and the backup runbook gets a line about it. Replaying deletions
  after a restore is deferred.

## Trust boundaries and guards

- **Other agents:** they have no grant, and the tool refuses any caller but
  Kai even with a grant. There's no `query_app` path.
- **Reader, QA and admin-key principals:** the App API refuses them.
- **The App's database role** is shared by the App and the tool, and it owns
  the schema. Anyone holding `app-judgment-db` can read or alter rows, so
  the secret is trusted the way every App secret is. Immutability and
  no-delete are enforced in code and tests. Database triggers are deferred.
- **Who triggered Kai.** Any Relay or Discord mention can start a Kai run,
  and the tool can't see who did. An injected message could get Kai to
  record fabricated "relayed" feedback. Three things limit that:
  `source_ref` must point at a real message format, the page links it, and
  only Kyle's confirmation upgrades anything. The review separates confirmed
  from relayed outcomes. Passing the run's initiator through the executor
  is deferred.
- **Kai's other stores.** Kai's prompt gets a Judgment section: judgment
  records (beliefs, predictions, feedback) stay in the tool and are never
  copied into memory, the wiki, tickets or chat. That's a prompt rule, and
  Kai can edit its own prompt (`agent_self`). A platform guard is deferred.
- **No authority.** Nothing in the tool or App sends, buys, commits or
  speaks for Kyle. The tool reaches nothing but its database.

## Kai's prompt (live row)

Kai is live-only, so its definition is changed through the API, which
records a change-log version. Two changes: grant `mcp__platform__judgment`,
and append this section:

> **Judgment.** Use the `judgment` tool to find where your model of Kyle is
> wrong. Before Kyle decides something you can foresee (including your daily
> question, when you can guess his answer), record a prediction with
> `outcome_known: false` and the question's message as `question_ref`.
> Never write a prediction after you know the answer; if you already know
> it, say `outcome_known: true`. When he shares what he chose or corrects
> you, record feedback with his words and the message reference, then
> revise the belief it touches with `belief`. Only Kyle can confirm a
> claim; silence is not feedback. Judgment records stay in the tool: don't
> copy beliefs, predictions or feedback into memory, the wiki, tickets or
> chat. (Saving Kyle's answer itself to private memory, as your daily
> question already does, is fine.) Call `pending` when you want to see
> what's open.

## Lockstep and infrastructure

- `scripts/compile_live_operation_catalog.py`: classify the five actions
  (`recall` and `pending` as `reads_sensitive`; `belief`, `predict` and
  `feedback` as `mutates_platform`; all `private`), then regenerate
  `live_operation_catalog.json`.
- CI: an `apps` step for `apps/judgment/backend`, and `npm run build -w
  judgment-frontend` in the `web` job. The `tools` job picks up
  `tools/judgment` on its own.
- Images: the App image is new. The tool-executor image needs a rebuild only
  if `psycopg[binary]` isn't already in the union of tool requirements.
- Helm: add `judgment` to `apps.enabled` in `values-pai-nuc.yaml`, and a row
  to `docs/deployment.md`'s image table.
- Docs: `docs/building-blocks/judgment.md`, an index row and glossary
  entries.

## Acceptance (from ENG-8)

1. Kai creates an unconfirmed belief and a prospective prediction, retrieves
   both in a later run, then attaches Kyle's correction; the original
   prediction is unchanged.
2. Feedback narrows or rejects a belief with a traceable reason. Kai's
   inferred revisions stay distinct from Kyle-confirmed ones, and Kai can't
   reject a confirmed belief.
3. Imported text, relayed feedback and missing replies never produce
   `kyle_confirmed`.
4. Ambiguous outcomes stay `mixed`, `context_changed` or `unresolved`;
   `unresolved` keeps a prediction pending.
5. `recall` returns corrections, the latest confirmed version and
   conflicting feedback with the claim.
6. Kyle can inspect, confirm, correct and delete on his page. Every other
   principal is refused, and so is every other agent, granted or not.
   Deletion leaves nothing in the App's tables, including derived versions.
7. The review separates prospective resolved (confirmed versus relayed),
   prospective pending, retrospective, flagged and unconfirmed, with counts.
8. Everything Kai does goes through the tool and survives across runs.

## Alternatives considered

- **Extend the memory tool** with kinds and versions. Rejected: memory is
  admin-searchable platform data that shares the promote-to-wiki path, and
  bending it into versioned, immutable records is the "general memory
  rewrite" ENG-8 rules out.
- **App API for agents via `query_app`.** Rejected: `query_app` has no
  per-App grant, so the API would trust `X-AP-User == kai` on a path every
  agent can reach. One tool with one identity check is easier to reason
  about.
- **Wiki pages per belief.** Rejected outright: the wiki is shared.

## Deferred

- A platform guard against copying judgment content into memory or the
  wiki, and against Kai removing its own prompt rule.
- Passing a run's initiator to custom tools, so relayed writes can require
  that Kyle started the run.
- Database triggers or a separate read-only role for immutability.
- Replaying deletions after a backup restore.
- More than one owner (Pai, Olu): `OWNER_AGENT` becomes a set and rows are
  scoped by owner.
- Any statistics beyond counts.
