# Changes (self-edit)

**What:** how configuration changes land. The platform edits its own repo —
every config mutation the UI offers ultimately becomes a git commit or a pull
request, authored either deterministically (editors) or by a coding agent
(wizards, freeform instructions).

**Lives in:** GitHub. The Changes page lists the platform's open PRs with
rendered diffs; accept merges to `main` (which agents-sync pulls into the
cluster within a minute), delete discards branch and PR.

**The contract:**

- **Deterministic editors** (agent definition, SKILL.md) write exactly what
  you typed and ALWAYS open a PR on a deterministic branch
  (`coder/agent-<name>`, `coder/skill-<name>`). While that PR is open the
  editor is locked — one pending change per thing, no branch clobbering.
- **Agent-authored changes** (New Agent / New Skill wizards, "edit this agent"
  freeform instructions) dispatch platform-coder, whose diff lands as a PR the
  same way.
- Nothing a wizard or agent writes goes live until the change is accepted —
  review is the trust boundary of the self-hosting loop.
