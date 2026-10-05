# Quota bets: finish the four remaining App migrations

**Scope changed 2026-10-05:** Kyle explicitly requested retiring TTRPG as an
Agent Platform App and leaving `claude-ttrpg` alone. The original four-App
finish condition will not occur, so these bets cannot have a fair winner.
The three completed migrations and actual quota reading belong in the final
retrospective; the forecasts below remain unchanged as a historical record.

Locked on 2026-10-04 before implementation. The baseline is the last verified
Codex seven-day reading: **68% left** at 2026-10-04 23:03:10 UTC, resetting
2026-10-09 21:10:18 UTC. That reading was approximately 15 minutes old when
the bets were requested. A fresh platform read was unavailable, so the final
comparison must report any later baseline change. Assume completion before
the reset and no unrelated account use; those assumptions may fail.

The finish line is all four remaining coded Apps—Judgment, TCMS, Stockmarket,
and TTRPG—operating as database-owned state Apps with preserved data and
reader/writer behavior, live functional proof, old services retired, and code
pushed to main. The previous News migration changed the observed weekly
reading from 71% to 68% (three points), versus earlier predictions of 60–66%
remaining. The observed change may include other account activity.

| Forecaster | Judgment | TCMS | Stockmarket | TTRPG | Total points spent | Bet: % left |
|---|---:|---:|---:|---:|---:|---:|
| Codex | 9 | 7 | 7 | 9 | 32 | **36%** |
| Astra | 5 | 5 | 4 | 6 | 20 | **48%** |
| Fable (`fable` CLI alias) | 5 | 6 | 4 | 6 | 21 | **47%** |
| Opus (`opus` CLI alias) | 5 | 4 | 4 | 5 | 18 | **50%** |
| Sonnet (`sonnet` CLI alias) | 4 | 3 | 4 | 3 | 14 | **54%** |

The supported Claude CLI aliases were used because exact names such as
`sonnet-5.5` failed selection; the wrapper does not expose the resolved model
ID. Fable, Opus and Sonnet ran as independent, read-only Claude subscription
sessions. Astra was an independent Codex subagent. No forecaster saw the other
current bets before answering; Codex stated its 36% bet first.

Judgment's guarded review actions, TCMS data volume and restore proof,
Stockmarket's chart/backtest behavior, and TTRPG's cross-repo adapter are the
largest uncertainties. Record each completed App's quota reading and any reset
or unrelated usage. Do not revise these bets after committing this file.
