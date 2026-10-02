# Judgment App (design 38) — build plan

Design: `docs/design/38-judgment-app.md`. Worktree `/Users/kp/gh/ap-judgment`,
branch `feat/judgment-app`. Orchestrator: Claude (Opus 5.5) with parallel
implementer subagents; reviews by Fable, Sonnet, Sol, Astra.

## Ground rules for implementers

- Work only in `/Users/kp/gh/ap-judgment`. Stage files by explicit path; never
  `git add -A`. Do not push. End commit messages with
  `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- `apps/judgment/backend/judgmentapp/schema.py` is the contract (tables,
  enums, limits, validation, SQL, pure rules). Use it; do not duplicate its
  logic. If you need a change there, make the smallest one and say so.
- Mirror the TCMS layout and idioms (`apps/tcms`, `tools/tcms`). Comments
  explain why, sparingly, in the house voice.
- Never weaken a test. Every guard in the design gets a test.
- Python: `/Users/kp/gh/agent-platform/services/backend/.venv/bin/python`.

## Tasks

### Phase A — the contract (orchestrator)
- [x] **T1 `schema.py`**: tables, enums, limits, validation, SQL, resolution and review rules.

### Phase B — parallel (T2 ∥ T3 ∥ T4)

- [x] **T2 The tool** (commit `b81a2fb`; 88 tests; catalog + registry lockstep green) (`tools/judgment/`): `tool.yaml` (category
  `domain_capability`, `infra.secrets: [app-judgment-db]`, `timeout_seconds`
  30, params with an `action` enum `belief|predict|feedback|recall|pending`
  and per-action fields described in the description, tcms style),
  `run.py`, `test_run.py`, `requirements.txt` (`psycopg[binary]>=3.1,<4`).
  Identity check first (`TOOL_CALLER_AGENT == OWNER_AGENT` and non-empty
  `TOOL_RUN_ID`). Writes in one transaction each, `request_id` idempotency
  via `requests`, minimal JSON receipts; reads as text inside
  `<judgment-records untrusted="true">…</judgment-records>`. Also: classify
  the five actions in `scripts/compile_live_operation_catalog.py` and
  regenerate `services/backend/agentplatform/live_operation_catalog.json`;
  `services/backend/tests/test_operation_catalog.py` and
  `test_toolregistry.py` must pass.
- [x] **T3 The App backend** (commit `2cdf80d`; 55 tests on aiosqlite and on Postgres 16) (`apps/judgment/`): `app.yaml`, `Dockerfile`,
  `backend/requirements.txt`, `backend/pytest.ini`,
  `backend/judgmentapp/{__init__,main,db,api}.py`,
  `backend/test_judgmentapp.py`. DDL from `schema.COLUMNS` with types in
  `db.py` (assert agreement at import, like tcms), `translate()` for sqlite.
  Auth dependency on the router: `X-AP-Role == "admin"` and `X-AP-User` in
  `schema.owner_principals()`; tests for reader, admin-key principal, missing
  headers. All deletes as explicit statements in one transaction. Plus CI
  (`.github/workflows/ci.yaml` `apps` job step), Helm
  (`charts/agent-platform/values-pai-nuc.yaml` `apps.enabled`), and the
  `docs/deployment.md` image row.
- [x] **T4 The frontend** (commit `68f0bba`; build + token gate clean; 390px both themes against a contract mock) (`apps/judgment/frontend/`, workspace
  `judgment-frontend`): one page, four tabs (Beliefs, Predictions, Feedback,
  Review), `@ap/ui`, Vite `base: "/apps/judgment/"`, built against the API
  contract below with a fixture mode for development. Both themes, no
  horizontal scroll at 390px, no raw hex (token gate). Plus the CI `web` job
  build step.

### App API contract (T3 serves it, T4 consumes it)

All under `/apps/judgment/api`, JSON, Kyle-only (403 `{"detail": …}` otherwise).
Writes take `expected_version` where a belief is involved and answer 409 on a
mismatch.

| method | path | body / query | returns |
|---|---|---|---|
| GET | `/me` | | `{principal}` |
| GET | `/beliefs` | `?status=active\|superseded\|rejected\|all` | `[{id, status, current_version, created_at, current: <version>, confirmed: <latest kyle_confirmed version or null>}]` |
| GET | `/beliefs/{id}` | | `{belief, versions: [<version>], predictions: [{id, scenario, predicted_choice, timing, created_at}], feedback: [<feedback>]}` |
| POST | `/beliefs/{id}/confirm` | `{expected_version}` | new version: current claim/scope as `kyle_confirmed` |
| POST | `/beliefs/{id}/correct` | `{expected_version, claim, scope?, reason?}` | new `kyle_confirmed` version in Kyle's words |
| POST | `/beliefs/{id}/reject` | `{expected_version, reason}` | status `rejected` (new version records the reason) |
| DELETE | `/beliefs/{id}` | | `{deleted: {beliefs, versions, links, feedback}}` |
| GET | `/predictions` | `?state=pending\|resolved\|all` | `[{…prediction, alternatives: [..], resolution, flags, feedback_count}]` |
| GET | `/predictions/{id}` | | `{prediction, links: [{belief_id, belief_version, claim \| null}], feedback, resolution, flags}` |
| POST | `/predictions/{id}/feedback` | `{kyle_words, outcome, belief_id?, belief_version?}` | feedback, confirmed on creation |
| DELETE | `/predictions/{id}` | | `{deleted: {...}}` |
| GET | `/feedback` | `?confirmed=false\|true\|all` | `[<feedback>]` |
| POST | `/feedback/{id}/confirm` | `{kyle_words?}` | the feedback, confirmed |
| GET | `/feedback/{id}/delete-preview` | | `{versions: [{belief_id, version, claim}], beliefs_emptied: [id]}` |
| DELETE | `/feedback/{id}` | | `{deleted: {...}}` |
| GET | `/review` | | `{counts: <schema.review_counts>, items: [{prediction, feedback, resolution, flags}]}` (resolved prospective only) |

`<version>` and `<feedback>` are the `schema.COLUMNS` rows as JSON
(timestamps ISO 8601); `alternatives` is decoded to a list.

### Phase C — integrate, review, ship (orchestrator)
- [x] **T5** (merged into `feat/judgment-app`) Merge T2–T4, full local suites, design-acceptance walk in tests.
- [x] **T6** (`6d68530`: nine findings fixed) Code review by Sol + Fable; fix findings.
- [x] **T7** (commit `c84ec27`) Docs: `docs/building-blocks/judgment.md`, index row, glossary.
- [x] **T8** (PR #31, `05d49d5`) PR, CI green, merge.
- [x] **T9** (helm rev 88) Deploy: build frontend + App image, import, helm upgrade with
  `judgment` enabled; confirm the provisioner made `app_judgment` and
  `app-judgment-db`; App pod healthy; page loads for the admin session and
  refuses a reader key.
- [x] **T10** (Kai version 9) Kai: grant `mcp__platform__judgment`, append the prompt
  section (live edit, change-log version).
- [x] **T11** (Kai runs `fc77e517`, `b6baa985`; Kyle-session step pending, see the design's AS BUILT) Live verification: through Kai, create a belief and a
  prospective prediction, recall in a second run, attach relayed feedback;
  confirm on the page; prove another agent and a reader are refused; delete
  and confirm the rows are gone. Record evidence in the design's AS BUILT.
