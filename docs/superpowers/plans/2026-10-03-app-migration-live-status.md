# Agent-built Apps: live migration status

**Update 2026-10-05:** Running, News, Judgment, TCMS and Stockmarket are live
as state Apps. Kyle removed TTRPG from this migration's scope and requested
that its Agent Platform App be retired without changing `claude-ttrpg`.
The TTRPG world was archived and its PVC retained; see
[retirement and recovery](../../building-blocks/ttrpg-retirement.md).
The retired Running, Judgment and TCMS service manifests were removed from
the App catalogue; their source code and old tables remain for recovery.
The status and quota floor below describe the earlier 2026-10-03 checkpoint,
not today's deployment.

The user's finish line is all six coded Apps moved into database-owned state,
with maintainers and browser workflows working and the old workloads removed.
Minor UI changes are acceptable. Stop before Codex weekly quota falls below
75% left. Check a fresh reading at each cutover boundary.

| App | State | Next gate |
|---|---|---|
| Running | **Live on state App**, 2026-10-03 | Disposable-cluster restore drill; grant builder tools only through an approved Kyle-session path |
| Judgment | Source snapshot and copy command prepared; coded App still live | Preserve Kyle's review actions on tool-only collections, then copy and switch |
| News | Coded App live | Inventory ingestion, freshness, topic/search/calendar parity |
| TCMS | Coded App live | Size result copy and restore first; preserve case/run workflows |
| Stockmarket | Coded App live | Preserve charts, watchlist, briefs and backtests |
| TTRPG | Coded App live | Cross-repo contract, game-state checkpoint and full turn proof |

Running copy: 50 activities, two briefs and 52 verified records. Running Coach
SYNC then wrote two activities directly to the state App. Playwright verified
the page, 50 visible activity rows, both briefs, the sidebar entry, readable
week labels and no browser errors. Helm revision 93 removed `ap-app-running`.
Pre- and post-cutover encrypted backups uploaded to GCS. The old Running
database is retained. The post-cutover file matched its SHA-256 receipt and
passed offline decryption/manifest inspection. An existing weekly report
was re-saved successfully through `running.report`.

Judgment preflight: 41 beliefs, 66 versions, four predictions, one link and
six feedback rows; 25 earlier belief snapshots. The state adapter and bundle
exist, but the rich Kyle review workflow is not yet represented in state
pages. The guarded copy command is staged, not run live. Do not freeze Kai or
remove Judgment until this gap is closed. Track the missing signed page-intent
tool actions and browser acceptance in **ENG-10**.

The foundation and Running path are on `main` through `8875f93`. The current
next batch should finish Judgment's review workflow and cutover together;
avoid separate foundation deployments. Kyle paused the migration on
2026-10-03 to preserve the 75% weekly Codex quota floor. See the
[resume checkpoint](2026-10-03-app-migration-resume.md) for exact state,
commands and remaining gates. A separate admin-key authority change was
deployed as Helm revision 94 during this pause; it did not migrate another
App. The earlier
`2026-10-02-agent-built-apps-paused-checkpoint.md` is historical and its
0/6 count is superseded by this file.
