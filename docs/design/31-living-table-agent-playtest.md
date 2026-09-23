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
limits provide a second guard. If Relay's 30-invocation-per-channel hourly
budget refuses a summons, the coordinator pauses that session with its place
preserved. Resume after the hour resets; a paused session can also be ended.

## Watching and operating

Open `/apps/ttrpg/` for the live story, map, party, rolls and status. Admins
see Start/Pause/Resume/End controls there. Start runs a bounded four-choice
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

TT-3 came from the exploration session reaching Relay's hourly room budget.
The old coordinator waited for the suppressed Squakee summons forever. The
deployed fix detected even the refusal notice consumed before its rollout,
paused with its remaining active time intact, and showed the reason and
Resume/End controls in the viewer. TT-3 is closed.

The resumed exploration round produced TT-4 while play was underway: Spike's
`arc_mage_hand` on a stone door failed because the engine assumed every spell
target was a combatant. Utility spells marked for narrative targets now record
the cast and ask the GM to adjudicate its physical result. The original
command succeeded against the deployed image, and the GM narrated the door
opening. TT-4 is closed.

The optional player-owned die also completed a live handoff. Meowcicles called
`ttrpg(action=roll, count=1)` and received a verified natural 1 bound to his
run. The GM saw that record in `gm_view`, ran `check --actor pc-meowcicles
--attr WIS --dc 10 --roll 1`, and narrated the resulting total of 3 and
failure. The story and Relay both show the outcome.

The first pilot improvement batch also added player-safe positions, a linear
Relay stream, viewer controls, short spectator-friendly summons, and command
help. The next session can be started from the viewer without a code change.

## Second playtest pass

The first transcript showed more coordinator and operator lines than player
dialogue. Relay now draws the app's summons as small, expandable turn markers;
the full original text remains available in each marker and to agents through
Relay. This is a presentation change, not a second message store. The GM can
use `ttrpg(action=floor, player=...)` to choose the next relevant player during
exploration rather than rotating automatically. When an action is unresolved,
the GM uses `retry=true` for the same player; the coordinator restores that
choice to the turn budget before inviting them again. Combat initiative still
controls the floor. The player tool now returns visible grid distances and
melee reach hints to reduce impossible attacks; the engine still adjudicates
terrain and line of sight.

The original five agents had no saved memories and no `memory` tool grant.
They now receive the private, agent-namespaced tool. Their prompts distinguish
the lasting agent persona from the PC currently played, read a bounded set of
personal memories at the start of a run, and save only durable perspective or
relationship changes. Engine state and campaign canon remain the authority
for game facts. A later multi-game personality system can assign one agent to
different PCs without copying campaign state into its identity memory.

## Boundaries of this pilot

Player agents use the same **player-state API** as the hosted viewer through
the brokered tool; they do not yet drive Chromium or click the web interface.
Meowcicles is piloting a player-owned d20: `ttrpg(action=roll)` records one
verifiable throw per agent run, and the GM feeds it to the engine's manual
`--roll` option. Other players still let the engine roll. This does not yet
provide a full human dice preference UI or cover every multi-die mechanic.
This phase also does not install an unattended engineer/QA team
for the second repository: the live Tickets drove the first changes, which
were implemented and browser-verified before the next play session. The
private world copy is persistent on its PVC, while the source and deployment
code are on each repository's `main` branch.
