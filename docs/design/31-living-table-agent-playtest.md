# The Living Table: agent playtest pilot

The pilot runs a **copy** of `family-main` on pai. The original family save is
unchanged. The private working copy lives on the `ap-ttrpg-world` PVC; it is
not in Git. The TTRPG source is `kylep/claude-ttrpg`, while Agent Platform owns
the app declaration, ingress, brokered tool, Relay room, Tickets, and agents.

## The table

`ttrpg-gm` owns NPCs, monsters, rules adjudication, and engine writes. Four
Codex player agents each own one existing PC: Meowcicles, Squakee, Spike, and
Fluffy. Their prompts preserve the family characters' sheets and canon without
making up their histories. Players can read a player-safe view and talk in
`#ttrpg-table`; only the GM can read hidden state or run engine commands.
The view now gives visible combatants' grid positions, so players can check
reach before declaring a melee attack. A hidden monster is omitted from both
that data and the map.

The app's coordinator grants one floor at a time by posting a short Relay
mention. The ordinary Relay router creates each run, so its hop and hourly
guards still apply. The coordinator waits for the run to finish, then reads the
engine's active actor. A monster turn stays with the GM; an illegal PC move
leaves that PC up for a revision. Sessions cap player choices and elapsed time.
Before every invitation the coordinator reads the platform's **cached** Codex
weekly quota snapshot. It pauses at 90% used (10% free) or when the snapshot
is unavailable; it does not send a quota probe per turn. Agent-level quota
limits provide a second guard.

## Watching and operating

Open `/apps/ttrpg/` for the live story, map, party, rolls and status. Admins
see Start/Pause/Resume controls there. Start runs a bounded four-choice
session; a completed session leaves the world where it is. The header links to
`#ttrpg-table` in Relay. That channel's `reply_mode` is `linear`, so agent
answers appear inline, in order, rather than hiding under each invitation.
Both the hosted viewer and Relay were checked with a real browser against pai.

The hosted viewer forces the player lens for ordinary users. Only a platform
admin can open its GM route or control a session. The tool-executor's
`ttrpg` tool uses a provisioned app key, the verified caller's agent name,
and a run-prefixed idempotency key. The GM's command endpoint serializes
commands, denies path and world overrides, and refuses an ambiguous retry
after a crash. App pods can be reached only from the authenticated web/API
route or the tool executor's scoped ingress policy.

## Feedback loop and observed fixes

Agents can file Tickets while playing. In the first live session the GM filed
TT-1 after a valid attack was rejected: the broker's run-ID prefix exceeded
the host's request-ID limit. The host limit and error text were fixed, and
the tool gained `action=help`. The rerun reached real engine range validation;
Meowcicles revised the move and the engine rolled a miss. TT-1 is closed.

TT-2 exposed a coordinator that rotated player names even when a monster held
initiative. The coordinator now checks the live active actor before each
summons; a later session verified that Spike acted on Spike's turn and Fluffy
was invited again after an out-of-range action. TT-2 is closed. The GM then
ended the encounter through the engine, awarding 50 XP per PC and 9 party gold.

The first pilot improvement batch also added player-safe positions, a linear
Relay stream, viewer controls, short spectator-friendly summons, and command
help. The next session can be started from the viewer without a code change.

## Boundaries of this pilot

Player agents use the same **player-state API** as the hosted viewer through
the brokered tool; they do not yet drive Chromium or click the web interface.
The engine currently rolls for them. Manual dice entry already exists in the
CLI, but per-player roll preference and a player-owned dice tool are future
product work. This phase also does not install an unattended engineer/QA team
for the second repository: the live Tickets drove the first changes, which
were implemented and browser-verified before the next play session. The
private world copy is persistent on its PVC, while the source and deployment
code are on each repository's `main` branch.
