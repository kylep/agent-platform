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

## Done when

Kyle asks the UI to create a new agent; platform-coder opens a PR; the PR
shows in Pending Changes; merging it makes the agent appear and runnable —
with zero terminal use.
