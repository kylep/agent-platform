# News migration: quota and execution retrospective

2026-10-04. This closes one complete App migration, then pauses as Kyle requested.

## Outcome and evidence

News now runs as a database-defined App. A guarded cutover copied and verified
353 stories and 7 topics, 360 records in one transaction, after a successful
encrypted cluster backup. The coded News service was removed from Helm and
the active App catalogue; its source tables remain available for rollback.
The platform dispatcher now handles News ingestion. The reader supports search,
topic and calendar-day navigation, and old News URLs redirect to the state App.
The news-librarian now reads the state App rather than the retired query tool.

A real `morning-news` run succeeded and raised the story count to 356; a real
news-librarian run succeeded. A scripted browser journey verified the App home,
search, calendar day, legacy redirect, and absence of a duplicate catalogue
entry, with no page errors. Focused backend and browser tests passed, the web
build passed, and the deployed workloads were healthy. The migration shipped
on main at `08bd49e`.

## Quota result and bets

Kyle supplied a 71%-left starting reading. A fresh platform reading at
2026-10-04 23:03:10 UTC showed 68% left, an observed 3-point change. The
[locked bets](2026-10-04-next-app-quota-bets.md) ranged from 60% to 66% left;
Astra was closest at 66%, 2 points below the observed reading. Codex's 62%
bet was 6 points low. Other work on the account could contribute to the
window's change, and weekly percentages are coarse, so this is not a precise
per-App token bill.

## What worked and what to change

One complete vertical slice kept the framework work bounded. A local source
snapshot and count/hash checks made the data cutover reviewable. Focused tests,
one production writer run, one librarian run, and one scripted browser journey
provided useful evidence without repeated broad suites or review loops. The
result used substantially less of the weekly allowance than any forecast.

The rollout still needed a second web pass after stale Apps-card and bookmark
paths surfaced, and one backend test retained an assumption about the old News
route. Next time, inventory the App catalogue, legacy links, agent readers,
and deployment references before the first image build. Update those paths in
the same batch, then perform one build and one rollout. Keep the final live
browser journey: it found navigation issues that unit tests did not.

No further App migration starts in this session. Resume from the current main
branch and recheck quota and live state before choosing the next slice.
