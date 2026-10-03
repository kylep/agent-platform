# Judgment state App cutover

Judgment remains on the coded App. `judgment_migration.py` defines the reviewed
state bundle and copy conversion; `judgment_cutover.py` is a guarded one-shot
copy command. Neither runs on a fresh install or on a schedule.

The 2026-10-03 source preflight found 41 beliefs, 66 belief versions, four
predictions, one prediction link, six feedback records, and 25 earlier belief
snapshots. The converter accepted a consistent read-only snapshot. There is no
Judgment state App yet.

**Do not run `--apply` yet.** The legacy UI lets Kyle correct and reject
beliefs, confirm feedback, and remove versions. The current state page recipe
only reads records and history. Its generic page templates also refuse writes
to tool-only collections, which is intentional; a page must not silently
bypass Kai's guarded Judgment tool. Preserve these review actions through a
Kyle-authorized, explicitly scoped path before switching the writer or UI.

When that path is ready, the cutover order is: encrypted backup; disable Kai's
Judgment writes and any concurrent run; run the default dry-run in the API
pod; run `python -m agentplatform.appdata.judgment_cutover --apply` once; check
current and historical parity; switch the `judgment` tool to `state.py` and
the UI to the published pages; re-enable Kai; test a belief revision,
prediction, feedback, and each Kyle review action; then remove the coded
workload from Helm. Keep its schema until a later backup and restore drill.
The copy transaction refuses a nonempty destination and rolls back if any
record, version or document differs.
