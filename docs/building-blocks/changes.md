# Changes — the change loop

**What:** the ONE way configuration changes land. Every edit to a **building
block** — the platform's collective name for its git-declared citizens
(agents, skills, secret declarations, entrypoints) — rides the same rails:

```
Propose  →  Review  →  Accept  →  Deploying  →  Live
   └──────────────────→  Discard
```

**Lives in:** GitHub (the PRs) + the cluster's synced checkout (what's Live).

## The loop, stage by stage

1. **Propose.** An editor save, a wizard, or a platform-coder run opens a PR
   on the block's **deterministic branch**: `coder/agent-<name>`,
   `coder/skill-<name>`, `coder/secret-<name>`. Deterministic editors validate
   *before* proposing (broken YAML/frontmatter is rejected at save time with
   the parse error — a change that would quarantine its block can't be
   proposed). The UI confirms immediately with a link to the review.
2. **Review.** One pending change per block, and the block's editors **lock**
   while it exists — no branch clobbering, no stacked edits. The Changes page
   shows each pending change with a *building block chip* (what it touches,
   deep-linked), the rendered diff, and Accept/Discard. Editors link straight
   to their change via `/changes?open=<pr>`.
3. **Accept** merges to `main`. **Discard** (confirmation modal — it's a
   one-way door) closes the PR and unlocks the block.
4. **Deploying.** agents-sync pulls `main` into the cluster within its sync
   interval (60s). The accept returns the merge sha and the UI tracks it
   against `GET /api/sync-status` (the synced checkout's HEAD, read from the
   shared volume): the Changes page shows **deploying… → live ✓** per accepted
   change.
5. **Live.** Pages watching a block (agent page, skill editor, secrets page)
   notice their pending change resolved, wait for the sync, **auto-refresh the
   content, and flash** "✓ Live". No hand-refresh, no guessing.

## Who produces changes

| Surface | Branch | Author |
|---|---|---|
| Agent definition / entrypoints editors, capability checkboxes, New Agent wizard | `coder/agent-<name>` | deterministic |
| "Edit with platform-coder" freeform instruction | `coder/agent-<name>` | coding agent |
| SKILL.md editor | `coder/skill-<name>` | deterministic |
| New Skill wizard | `coder/skill-<name>` (+ may touch `secrets/`) | coding agent |
| Secret declare wizard / secret.yaml editor | `coder/secret-<name>` | deterministic |

Secret **values** are deliberately outside the loop: they're set immediately
via the API into k8s (nothing to review — values never enter git).

## Judgment calls (made autonomously — flag if wrong)

- **"Live" detection is heuristic off the accept page.** The Changes page
  knows the merge sha and confirms it exactly. Editor pages that see their PR
  resolve only watch for the checkout sha to *move* (≤90s), then refresh —
  proving ancestry client-side isn't worth the machinery. A discarded change
  also triggers a (harmless) refresh.
- **`live (unconfirmed)`** appears if the checkout head moves *past* the merge
  sha (another commit landed after) — almost certainly live, reported honestly
  instead of spinning.
- **Discard ≠ delete branch.** The branch survives on GitHub (closed PR);
  the lock only tracks open PRs, so the block unlocks. Re-proposing reuses the
  branch.
- **Emptying the entrypoints editor deletes the file** (no durable triggers)
  rather than committing an empty file.
- **The declare wizard covers probe-verified secrets only**; script-verified
  ones (rare — JWT signing etc.) are hand-written folders or New-Skill-wizard
  output.
- **"Set a bare value" stays** (secondary button): a value with no declaration
  is legal but chipped `undeclared` and can't be verified or hinted.
