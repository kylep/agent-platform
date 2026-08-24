# Changes — the change loop

**What:** the way *capability* changes land. Every edit to a **building
block** that is code — the platform's collective name for its git-declared
citizens (skills, tools, secret declarations, report types) — rides the same
rails:

Agent *definitions* (prompt, config, grants, entrypoints) are the one
exception: since [design-15](../design/15-db-first-agents.md) they are
Postgres rows, edited immediately with no PR, and get their own append-only
record instead — the **change log** (`agent_versions`), visible on each
agent's History tab. See [Agents](agents.md).

```
Propose  →  Review  →  Accept  →  Deploying  →  Live
   └──────────────────→  Discard
```

**Lives in:** GitHub (the PRs) + the cluster's synced checkout (what's Live).

## The loop, stage by stage

1. **Propose.** An editor save, a wizard, or a platform-coder run opens a PR
   on the block's **deterministic branch**: `coder/skill-<name>`,
   `coder/secret-<name>`, `coder/tool-<name>`, `coder/report-<name>`.
   Deterministic editors validate
   *before* proposing (broken YAML/frontmatter is rejected at save time with
   the parse error — a change that would quarantine its block can't be
   proposed). The UI confirms immediately with a link to the review.
2. **Review.** One pending change per block, and the block's editors **lock**
   while it exists — no branch clobbering, no stacked edits. The Changes page
   shows each pending change with an **automatic AI reviewer summary** (a
   dispatcher loop runs change-summarizer over every open change and posts the
   result as a PR comment, keyed to the head sha — a push re-summarizes; the
   UI renders the comment at the top of the review), a *building block chip*
   (what it touches, deep-linked), a deterministic impact digest, and the
   change itself — brand-new files render as readable content (SKILL.md as
   real markdown), edits as a colored diff. Accept/Discard beside it; editors
   link straight to their change via `/changes?open=<pr>`.
3. **Accept** merges to `main`. **Discard** (confirmation modal — it's a
   one-way door) closes the PR and unlocks the block.
4. **Deploying.** agents-sync pulls `main` into the cluster within its sync
   interval (60s). The accept returns the merge sha and the UI tracks it
   against `GET /api/sync-status` (the synced checkout's HEAD, read from the
   shared volume): the Changes page shows **deploying… → live ✓** per accepted
   change.
5. **Live.** Pages watching a block (skill editor, tool editor, secrets page)
   notice their pending change resolved, wait for the sync, **auto-refresh the
   content, and flash** "✓ Live". No hand-refresh, no guessing.

## Who produces changes

| Surface | Branch | Author |
|---|---|---|
| SKILL.md editor | `coder/skill-<name>` | deterministic |
| New Skill wizard | `coder/skill-<name>` (+ may touch `secrets/`) | coding agent |
| Tool editor / New Tool wizard | `coder/tool-<name>` | deterministic / coding agent |
| Secret declare wizard / secret.yaml editor | `coder/secret-<name>` | deterministic |

Coding-agent runs derive the branch from the paths they touched (precedence
skill > secret > tool > report when one change spans kinds — a new skill plus
the secret it declares lands on the *skill's* branch). Report types are
currently hand-written or coder-authored (no wizard yet). Agent definitions
never appear here — they aren't a change-loop block; see the note at the top
of this page.

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
- **The declare wizard covers probe-verified secrets only**; script-verified
  ones (rare — JWT signing etc.) are hand-written folders or New-Skill-wizard
  output.
- **"Set a bare value" stays** (secondary button): a value with no declaration
  is legal but chipped `undeclared` and can't be verified or hinted.
- **AI summaries are automatic** (Kyle's call, reversing the earlier
  button-not-automatic default): every open change costs one summarizer run;
  the PR comment doubles as the state store and the GitHub-side record.
