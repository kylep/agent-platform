# Judgment state App cutover

Judgment remains on the coded App. `judgment_migration.py` defines the reviewed
state bundle and copy conversion; `judgment_cutover.py` is a guarded one-shot
copy command. Neither runs on a fresh install or on a schedule.

The 2026-10-03 source preflight found 41 beliefs, 66 belief versions, four
predictions, one prediction link, six feedback records, and 25 earlier belief
snapshots. The converter accepted a consistent read-only snapshot. There is no
Judgment state App yet; `--apply` creates it.

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

The cutover order is: encrypted backup; disable Kai's
Judgment writes and any concurrent run; run the default dry-run in the API
pod; run `python -m agentplatform.appdata.judgment_cutover --apply` once; check
current and historical parity; switch the `judgment` tool to `state.py` and
the UI to the published pages; re-enable Kai; test a belief revision,
prediction, feedback, and each Kyle review action; then remove the coded
workload from Helm. Keep its schema until a later backup and restore drill.
The copy transaction refuses a nonempty destination and rolls back if any
record, version or document differs.
