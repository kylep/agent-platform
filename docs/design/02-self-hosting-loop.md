# Milestone 02 — Self-Hosting Loop (MVP)

The platform edits itself: clicking "edit agent" in the UI dispatches the
platform-coder agent, whose change lands via the tiered git write path and
appears in the Pending Changes view. This is the reason the project
exists.

## Scope

- **RBAC:** roles (`admin`, `operator`, `coder`, `reader`), per-agent
  `ap_...` API keys minted/revoked in the UI, scope enforcement in api and
  re-check in dispatcher.
- **Git service** in the api: repo-scoped deploy key, tier computation
  from the diff, tier-1 deterministic direct commits (safe manifest
  fields, agent.md body), tier-2 branch + PR via GitHub API.
- **platform-coder agent** (`agents/platform-coder/`): coder role, git
  skill, receives structured edit requests, works in an ephemeral
  workspace clone.
- **Edit flows in the UI:** "edit this agent" (freeform instruction →
  platform-coder run) and deterministic quick-edits (schedule, prompt
  field) that skip the agent entirely.
- **Pending Changes page:** platform-authored branches/PRs, rendered
  diffs vs synced definitions, unmerged-changes badges on agents,
  merge/close proxied through the GitHub API.
- **Sync hardening:** webhook-or-poll main→volume sync with commit
  provenance shown in the UI.
- **Change admin password:** a Settings flow for rotating the admin
  password (today the only path is deleting the principal row in postgres
  and re-running first-launch setup — see docs/setup.md troubleshooting).

## Done when

Kyle asks the UI to create a new agent; platform-coder opens a PR; the PR
shows in Pending Changes; merging it makes the agent appear and runnable —
with zero terminal use.

## Progress (2026-07-20)

Backend machinery built and tested (all merged to main; see
`docs/plans/2026-07-20-milestone-02-self-hosting-loop.md`):

- [x] **RBAC primitives** — `require_role`, roles reader<operator<coder<admin.
- [x] **API keys** — `ap_` bearer tokens, admin mint/list/revoke, auth path.
- [x] **Tier computation** — `classify_tier` (fails closed to PR).
- [x] **Change computation** — `compute_changes` (workspace → FileChange).
- [x] **GitWriter** — clone/commit/branch/push (tested vs local bare remote).
- [x] **GitHub PR client** — open/list/merge/close (request-shape tested).
- [x] **EditService** — end-to-end tier routing (commit vs branch+PR).
- [x] **platform-coder agent** — coder role, edits under `agents/` only.
- [x] **Quick-edit endpoint** — `POST /api/agents/{name}/quick-edit` (prompt).
- [x] **Change admin password** — `POST /api/change-password`.
- [x] **Self-hosting loop LIVE** — quick-edit through the API tier-1 commits
      an agent's prompt and pushes to `origin/main` (authored by
      platform-coder, zero terminal). Auth is a repo-scoped **ssh deploy key**
      (`github-deploy-key` secret, push-only); backend image gained
      `openssh-client`; secrets passed via GIT_SSH_COMMAND/GIT_ASKPASS only.

- [x] **GitHub App (PericakAI) wired** — installation tokens (`githubapp.py`)
      drive push + PRs; verified live (quick-edit push + a real open/close PR).
- [x] **Freeform platform-coder flow LIVE** — `POST /api/agents/{name}/edit`
      dispatches platform-coder; the dispatcher mints an App token and marks
      the run self-edit; the runner clones the repo, runs the agent
      (`--permission-mode acceptEdits`), and opens a **PR** for the change.
      Verified end-to-end: a freeform instruction produced PR #2 editing
      hello-world, authored by the app. Never commits straight to the branch.

Remaining:
- [ ] **Pending Changes** page (list/​diff/​merge platform PRs in the UI; the
      run transcript currently carries the PR link).
- [ ] Frontend: API-keys management, quick-edit UI, password Settings.
- [ ] Sync hardening (webhook-or-poll provenance).
- [ ] Make git config declarative via `helm upgrade` (currently set with
      `kubectl set env` on api + dispatcher); migrate off the deploy key.
