# Milestone 04 — Memory, Skills, SDK

Agents remember, skills become first-class, and the platform becomes
programmable from outside.

## Scope

- **Memory:** `memories` table (agent-namespaced, postgres FTS), memory
  API (save/search/recall/list), a memory skill giving agents access to
  their own namespace only, and a UI browser for reviewing/editing/
  deleting memories per agent.
- **Skills as components:** Skills UI page (list, detail, which agents
  use each, bound secrets), manifest-declared skill references mounted
  into runner pods, secret-binding enforcement (an agent's pod gets the
  union of its skills' secrets, nothing else).
- **Shipped skills:** `git` and `discord` hardened from 02/03 usage into
  documented, reusable form.
- **SDK + meta-operation:** OpenAPI → generated python SDK (`sdk/`),
  published platform Claude skill so any Claude session can operate the
  platform, both exercised in CI against a live chart install.

## Done when

An agent saves a memory in one run and recalls it in the next; memories
are auditable in the UI; a fresh Claude session with the platform skill
can list agents and trigger a run using only an API key.
