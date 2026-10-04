# App migration retrospective: spend work on completed Apps

Date: 2026-10-04. This is an execution correction, not a new architecture.
The [resume checkpoint](2026-10-03-app-migration-resume.md) remains the source
for live state and safety gates. Kyle has paused implementation; this document
does not authorize resuming it.

## What the evidence says

- The foundation accumulated many small commits and review/repair cycles on
  October 2 before **any** App was migrated. The first completed user outcome,
  Running, shipped late that night. At pause, only 1/6 Apps was live on state.
- The weekly Codex bar went from a recent reset to about 78% left by the
  October 3 pause. That is an observed *window* change, not an App-by-App cost:
  there is no reliable attribution by prompt, review, build or deployment.
  Do not invent a cost-per-App or claim Running alone spent 22 points.
- The Running cutover was effective once it became a single vertical slice:
  guarded copy, direct writer, visible page, real report, one browser journey,
  old service retired, and encrypted backup evidence.
- Judgment work stopped at a predictable mismatch: its legacy Kyle review
  actions were not inventoried before its copy/adapter work began. The copier
  is useful but cannot produce the next live outcome until that UI/tool path
  exists. The next App should be chosen by a short parity inventory, not by
  sunk effort in staged code.
- Repeated design re-reading, tiny commits/status docs, broad security
  generalization and model-led live polling cost context and attention. The
  prior Claude 24/25 loop had the same failure mode at larger scale; see
  `kyle-quota-efficiency.md` in the shared Claude memory.

## Revised build loop for the five remaining Apps

1. **Size before selecting.** Spend one bounded read pass on each remaining
   App's *current* writer, reader, data tables, side effects and user actions.
   Produce a compact five-row matrix and choose the next App with the shortest
   complete path. Judgment is the default only if its review-action gap is
   smaller than a complete News or Stockmarket slice. Size TTRPG's other-repo
   adapter and TCMS's data volume early, before betting a week on either.
2. **One acceptance script per App.** State the small set of visible outcomes
   that make that App useful. A script checks source/destination counts and
   stable content hashes, one real writer action, browser behavior, side
   effects, denial of an unauthorized write, and rollback/restore evidence.
   Minor UI/data-layout changes are fine when those outcomes remain.
3. **Add primitives only when the selected App consumes them.** Implement
   generic calculation, action, query or history operations at the boundary
   needed by that App, with a focused contract test and the actual adapter as
   the consumer. Do not build speculative DSL variants, chart types,
   permission layers or migrations detached from a vertical slice. Preserve
   the existing signed-intent boundary for writes to tool-only data.
4. **Code and review as one milestone.** One primary implementer, no default
   subagent fan-out. Ask for one bounded independent review only when a new
   auth/credential/outbox or data-loss boundary appears. Make a small number
   of coherent commits; do not deploy every repair. Prefer one review of the
   complete App diff over per-primitive reviews.
5. **Verify by script, not conversation.** Focused tests while editing; one
   relevant integration run, one scripted Playwright journey, one batch
   build/import/rollout, then a concise evidence record. Scripts collect
   counts, status and failures; the model reads the summary once. No routine
   `gh run` polling or broad suites after each edit.
6. **Enforce a phase budget.** Refresh weekly quota before selecting an App
   and before cutover. With Kyle's 75%-left floor, the available headroom is
   at most 25 percentage points after a reset. Admit a slice only if it is
   likely to finish inside the remaining margin; if it will not, leave the
   coded service alone and checkpoint. Track the observed quota delta per
   completed slice to improve later forecasts, without treating percentages
   as a linear token meter.

## First resume action

Read the checkpoint and `git status`, refresh quota, check disk, then make
the five-row parity matrix. Decide whether Judgment's signed review-action
slice or another complete App is the next shortest end-to-end outcome. Keep
the existing Running and Judgment data unchanged during that decision. Do
not start with a framework task list or a new large design review.
