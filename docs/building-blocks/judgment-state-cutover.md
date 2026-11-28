# Judgment state App cutover

The Judgment state App is live: App `judgment`
(`94ebba1ddacb4b5da98d071650b4f265`), owned by Kai, copied from the coded App
on 2026-10-04 by `judgment_cutover.py`. The `judgment` tool (`tools/judgment/run.py`)
already runs `state.py` against it. `judgment_migration.py` holds the reviewed
bundle and the copy conversion. Neither runs on a fresh install or on a
schedule.

The 2026-10-03 source preflight found 41 beliefs, 66 belief versions, four
predictions, one prediction link, six feedback records, and 25 earlier belief
snapshots. The copy transaction refuses a nonempty destination and rolls back
if any record, version or document differs.

**Kyle's review actions (ENG-10).** The pages bind reviewed tool actions
(`page_tool_actions.REVIEWED`): confirm, correct, reject and delete a belief;
add feedback to a prediction or delete it; confirm or delete feedback. Each
runs only as a Kyle-session page intent (`intents.dispatch`), signed, single
use, replayed from its receipt on retry, and refused for a fresh confirmation
if the target, delete plan or page moved. Every collection's create, update
and delete is tool-only, and only Kyle may write `provenance:
kyle_confirmed`, so a direct record call, an API key or Kai's own credential
can't bypass the guard. A delete also removes the deleted versions from the
surviving belief's storage history and drops every other pending Judgment
confirmation, since those quote record text.

**Upgrading the live App (ENG-8, ENG-10).** These rules, `feedback.shared_as`
and the new views were published to the live App as its approved version 2
on 2026-11-27, by a one-off script that has since been removed (see git
history for `judgment_upgrade.py`). The tool now assumes that definition.

What remains of the cutover: remove the coded workload from Helm after a
browser walkthrough passes, and keep its schema until a later backup and
restore drill.
