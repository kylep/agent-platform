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

- [x] **Pending Changes** page + endpoints — lists the platform's open coder/*
      PRs with Merge/Close; verified end-to-end live: merged PR #2 from the UI,
      the edit landed on main. (Also fixed a Gate regression: the probe's
      'valid' status was treated as blocking.)

- [x] **Full UI** — "Edit this agent" box on the agent page dispatches
      platform-coder; Pending Changes lists/merges its PRs; a Settings page
      does password rotation + API-key mint/list/revoke. Verified live: the
      whole loop is browser-driven (edit box → run → PR #3 → Pending Changes).

**Done-when: essentially met** (via the edit path; "create a new agent" uses
the identical machinery). Remaining hardening (non-blocking):
- [x] **Git config is declarative** (2026-07-29) — `AP_GIT_REMOTE_URL` and
      `AP_GITHUB_REPO` are set in `values-pai-nuc.yaml`, so `helm upgrade`
      reconciles them; the imperative `kubectl set env` is no longer needed.
- [x] **Deploy key retired** (2026-07-29) — the `github-deploy-key` credential
      path is gone from the code (its `GitWriter` ssh support, the pinned
      known-hosts blob, and the `_build_writer` branch), the secret is deleted
      from the cluster, and the secret registry no longer offers it. The App is
      primary and `github-token` (a PAT) remains the fallback. Rationale: a
      deploy key can't call the REST PR API, so it silently degraded tier-2
      edits to "branch pushed, no PR opened" — strictly worse than the PAT
      fallback it sat in front of.
- [x] **Sync hardening** (2026-07-30) — `agents-sync` now verifies git objects
      on fetch (`fetch.fsckObjects`/`transfer.fsckObjects`) so a malformed object
      can't land, and gained an **opt-in fail-closed provenance gate**: with
      `agents.verifyCommits=true`, each synced head must be signed by an allowed
      signer (`git verify-commit` against an `allowedSigners` Secret) before the
      checkout advances — an unsigned/untrusted head is refused and the last good
      checkout is kept. Agent definitions are executable behavior, so this proves
      the running definitions came from a trusted author, not "whatever is on the
      ref". **Off by default** (activation needs main's commits SSH-signed +
      `allowedSigners` populated, or sync refuses everything — a deliberate
      go/no-go for the operator). fsck integrity is always on.
