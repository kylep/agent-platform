# Milestone 02 — Self-Hosting Loop — implementation plan

Executes `docs/design/02-self-hosting-loop.md`. Built in vertical slices,
each independently tested and mergeable to main. The only hard external
dependency is a GitHub write credential (deploy key / token) for the
tier-2 PR path; everything else is buildable and testable without it, and
the GitHub credential is plumbed as a user-supplied secret (like
`claude-credentials`).

## Slice order

1. **RBAC primitives** — roles (`admin`, `operator`, `coder`, `reader`),
   `require_role(*roles)` dependency, role hierarchy, `require_admin`
   redefined in terms of it. Principals gain a listing/create surface.
   Pure api/auth work; no external deps.
2. **API keys** — `ApiKey` model (`ap_` prefix, hashed at rest), mint /
   list / revoke endpoints (admin only), and an API-key auth path parallel
   to the session cookie. Enables non-interactive + agent-invokes-agent.
3. **Git tier computation** — pure function: given a diff against the
   synced definitions, classify tier-1 (safe manifest fields + agent.md
   body → deterministic direct commit) vs tier-2 (everything else → branch
   + PR). Fully unit-testable, no git I/O.
4. **Git service** — repo clone in an ephemeral workspace, commit on the
   tier-1 path, branch+push+PR on the tier-2 path via the GitHub API using
   the supplied deploy credential. **← GitHub credential needed here.**
5. **platform-coder agent** (`agents/platform-coder/`) — coder role, git
   skill, structured edit-request contract, ephemeral workspace clone.
6. **Edit flows (api + UI)** — "edit this agent" (freeform → platform-coder
   run) and deterministic quick-edits (schedule/prompt) that skip the agent.
7. **Pending Changes** — list platform-authored branches/PRs, rendered
   diffs vs synced defs, unmerged-changes badges, merge/close via GitHub API.
8. **Sync hardening** — webhook-or-poll main→volume sync w/ commit provenance.
9. **Change admin password** — Settings rotate-password flow.

## Done when
Kyle asks the UI to create an agent; platform-coder opens a PR; it shows in
Pending Changes; merging makes the agent appear and runnable — zero terminal.

## Notes
- Progress ledger lives in commit history; update the design-02 checklist
  as slices land (mirror the 01 doc's verification-results section).
- Slices 1–3, 5 need no external creds. Slice 4 (and 6–7's merge actions)
  block on the GitHub credential — build the code + secret plumbing, then
  hand off the credential step like the claude-credentials flow.
