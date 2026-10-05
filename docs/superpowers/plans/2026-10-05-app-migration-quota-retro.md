# App migration quota retrospective — 2026-10-05

The locked four-App bets are in [the original bet sheet](2026-10-04-remaining-apps-quota-bets.md).
The original finish condition changed: Kyle asked to **remove** TTRPG from
Agent Platform Apps and leave `claude-ttrpg` alone. No four-App bet can be
scored as stated. Judgment, TCMS and Stockmarket were migrated to state Apps;
TTRPG was retired with an encrypted, verified world archive and retained PVC.

The last verified baseline was **68% weekly Codex quota left** at 2026-10-04
23:03 UTC. The platform reported **54% left** at 2026-10-05 02:02 UTC, fresh
at that reading. The observed difference was **14 percentage points**, across
the three migrations, their verification, the retirement work and any other
account activity in that window. It is not an isolated cost measurement.
For context only, the forecasters' first-three-App subtotals were Codex 23,
Astra 14, Fable 15, Opus 13 and Sonnet 11 points. Astra matched the observed
window, but the bets are not comparable to the changed finish condition.

What worked: batch a complete data-copy, reader, writer and browser path per
App; use one guarded transaction with source locks and full document checks;
keep old tables and take encrypted backups before cutover. A focused browser
script and one real tool call exposed problems more cheaply than broad suites.

What wasted effort: the first Stockmarket tool smoke reached the agent but
failed on the App-data response envelope (`version` is inside `values`). The
run itself was marked succeeded, so checking only run state would have missed
it. The same adapter bug affected TCMS and Judgment; one contract-shaped test
and a single fix across adapters resolved it. Future migrations should test
the returned tool result, not just the run status. The original TTRPG ledger
design was unsized and would have required invasive work in a separate engine
repo; the user chose retirement instead.

The five live state Apps are Running, News, Judgment, TCMS and Stockmarket.
There are no declared coded Apps or coded App workloads. Legacy source and
tables remain for deliberate recovery; removing the generic old-App framework
is a separate code cleanup, not a prerequisite for the visible migration.
