# Running state App cutover

The legacy Running service owns `app_running.activities` and `briefs`. The
replacement is a database-owned App, `running`, owned by `running-coach`. Its
definitions are the reviewed recipe in `appdata/running_migration.py`; the
one-shot copy is `python -m agentplatform.appdata.running_cutover` in the API
image. Fresh installs do **not** create this App automatically.

## Preflight and cutover

1. Take an encrypted platform backup and confirm the backup job succeeded.
   Record the legacy activity and brief counts, latest sync, latest report,
   and Running Coach's current agent version.
2. Deploy the backend, broker, tool executor and web images containing this
   change; restart the MCP facade after the API. Let `agents-sync` fetch the
   corresponding `tools/running` and `tools/strava` manifests from main.
3. Disable Running Coach's scheduled entrypoints temporarily and scale
   `deploy/ap-app-running` to zero. This freezes both old writers. Do not run
   a Strava sync or coaching job during the copy.
4. In the API pod, run `python -m agentplatform.appdata.running_cutover`
   first. It validates the exact bundle and source shape and reports counts
   without writing. Then run the same command with `--apply`. It creates the
   approved, Running Coach-owned App only if absent; it refuses a nonempty
   destination. The source share lock, copy and per-record comparison are in
   one transaction, so any failure rolls the destination back.
5. Update Running Coach in one definition write: grant `running`; remove
   `query_app`; clear `result_topic`; replace its prompt and crons with the
   state-backed schedule below. The direct Strava sync tool writes the App
   using its scoped tool-call credential. `apps` and `app_data` are separate
   Kyle-session grants; this migration does not grant them to an API key or
   to Running Coach.
6. Run one bounded Strava sync and compare activity counts, total distance,
   personal records, heatmap and the two copied briefs in the browser. Run
   `running.recover_reports`; it retries at most two unposted reports per
   call. Confirm the latest report exists and there are no duplicate posts.
   Only then disable the old Running deployment in Helm and leave its database
   intact until the next verified backup and a restore drill.

## Live cutover (2026-10-03)

The encrypted preflight backup uploaded successfully. The copy preserved 50
activities and two briefs (52 verified records). A Running Coach SYNC run
then wrote two activities directly to the state App and reported zero pending
briefs. Playwright loaded the published page with no errors, verified the
sidebar link and twelve readable weekly labels, and Helm revision 93 removed
the old Running deployment. The legacy database schema is retained for
recovery. Post-cutover backup `ap-cloud-backup-manual-gzd4c` succeeded and
uploaded `ap-recovery-20261003T041032Z.tar.age` to the configured GCS bucket.
A disposable-cluster restore drill remains outstanding.

The SYNC prompt reads `running.coach_context` to get `sync_after`, calls
`strava.sync` with that date, then calls `running.recover_reports`. The Monday
COACH prompt reads `running.coach_context`, writes a short note with
`running.brief`, and reports the returned receipt. No activity archive or
brief JSON passes through `result_topic` or through model output.

## Recovery

Before the first new App write, rollback is straightforward: restore the
saved agent definition and scale the legacy service back to one. Once a Strava
sync or brief has written to the state App, the old database is stale; do not
switch back without reconciling those records or restoring the pre-cutover
backup. Keep the old schema and image until the data parity and restore
checks have passed. The copy command never overwrites an existing state App.
