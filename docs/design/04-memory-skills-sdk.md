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

## Progress (2026-07-20)

- [x] **Memory** — `memories` table (agent-namespaced), `POST /api/memories`
      (save; reusing a `key` overwrites), `GET /api/memories?agent=&q=` (search;
      portable term-match over content/key, no engine-specific FTS),
      `GET/DELETE /api/memories/{id}`. Namespace isolation is the security
      boundary: an agent key (its `agent` set) is locked to its own namespace —
      a mismatched target is 403, another namespace's memory reads as 404.
      Manifest `memory: true` injects an annotator-scoped, per-run token
      (revoked on terminal); demo `notetaker` agent remembers across runs.
      `MEMORY_ROLES` gate the API. **Memories** UI page (pick agent, search,
      delete).
- [x] **SDK + platform skill** — hand-written, dependency-free Python SDK in
      `sdk/` (`agent_platform_sdk.Client`: list/inspect agents, create/get/list
      runs, save/search memory, health) mirroring the live OpenAPI, with an
      injectable transport for tests. Published `skills/agent-platform/SKILL.md`
      so any Claude session can operate the platform with one `ap_` key.
- [x] **Skills-as-components** — `SkillStore` reads `skills/<name>/SKILL.md`
      (frontmatter: name/description/secrets); `GET /api/skills[/{name}]` lists
      each skill, its required secrets, and which agents use it. The runner
      copies an agent's manifest-declared skills (`AP_SKILLS`, set by the
      launcher) from the synced `/agents/skills` tree into `~/.claude/skills/`.
      **Secret-binding enforcement:** the launcher binds a pod to exactly the
      union of its manifest `secrets` + its skills' secrets
      (`bound_secrets()`), injected via `envFrom` secretRefs (optional, so a
      not-yet-configured secret doesn't wedge the pod) — unbound secrets are
      never referenced. Skills UI page. Shipped a documented `git` skill. Still
      open: a hardened `discord` skill.
- [x] **Adversarial hardening** — a probe of the M03/M04 surface found RBAC /
      namespace isolation / injection all enforced; one robustness gap fixed:
      malformed `/api/memories` input (NUL bytes, over-length namespace) now
      422s at the edge instead of a DB-level 500.
- [ ] OpenAPI-generated SDK + platform-skill exercised in CI against a live
      chart install (currently: hand-written SDK + live manual exercise).

## Done when

An agent saves a memory in one run and recalls it in the next; memories
are auditable in the UI; a fresh Claude session with the platform skill
can list agents and trigger a run using only an API key.
