# 41 — Room watchers

Status: revised after review (2026-10-08). Author: Claude (Kyle's session).
Reviewed by: Fable (architecture), Opus (adversarial), Sonnet (implementability),
Codex gpt-6-astra (correctness), Codex gpt-6-sol (operability). Review
disposition is at the end.

## Problem

A human post in a mirrored Discord room wakes a persona only when it natively
@mentions that persona's bot (or is a DM / the bot's own thread). Every other
human post is dropped in `RelayRouter.handle` (`relay_router.py:186`, the
`not observation.addressed` return). The only cover for unaddressed posts is
Pai's 15-minute `discord_unaddressed` sweep, which can only *nudge* owners by
Relay DM — and a Relay DM is untrusted `<relay-messages>` text, so the owner
answers in Relay instead of Discord. Kyle posts in #digital-twin or
#persona-daily and nobody answers.

## Goal

A general room capability: **a room's dispatch mode can be `watchers`, with an
ordered watcher list.** Unaddressed human posts in such a room open a *watch
round*; each watcher takes a turn in order, sees earlier watchers' replies,
and either answers publicly through its own identity or declines. This is the
ordered generalization of the existing `dispatch_mode="default"` +
`default_agent` ("unaddressed post → this agent"), which today is dead for
external rooms.

Initial assignment (Kyle, 2026-10-08):

| Discord room (mirror) | Watchers, in order |
|---|---|
| persona-daily (external_ref 1556836472462508064; mirror titled "persona-roundtable" — confirm name at build) | pai, kai, olu |
| research | olu |
| digital-twin | kai |
| general, news, log, status-updates, planning, running, persona-rpg | pai |

DMs and threads cannot be watched (they are addressed by construction for
their owner; other bots' observations of an owned thread would otherwise
become watch turns).

## Design

### 1. Data

Additive, via db.py's existing `create_all` + `_ensure_columns`
(`ALTER TABLE ... ADD COLUMN`); no data migration.

- `room_watchers(channel_id, agent, position, created_at, created_by)`,
  PK `(channel_id, agent)`, `UNIQUE(channel_id, position)` declared on the
  model. Replace = delete-all + insert in one transaction.
- `conversations.dispatch_mode` gains the value `watchers` (column exists).
- `watch_rounds(id, channel_id, anchor_message_id, last_message_id, state
  open|done, created_at, closed_at)` — one open round per room at a time.
- `watch_turns(round_id, position, agent, identity_id, observation_id,
  state pending|running|done, outcome, reason, run_id, delivery_id,
  created_at, started_at, finished_at)`, PK `(round_id, position)`.
  `outcome` ∈ answered | declined | failed | delivery_failed |
  skipped_no_observation | skipped_access | skipped_removed | skipped_budget.

`watch_turns` is also the observability record: "why didn't Kai answer?" is
one row with a reason.

Watch rounds deliberately do **not** reuse `RelayMentionQueue`: its advance
gate requires a run-linked Relay message *and* an accepted delivery, both of
which a declined, failed, or undelivered external turn never produces
(`relay_router.py:361-375`, `recorder.py:205-214`), and it marks any
unaddressed row done (`:329-335`). Those latent stalls also affect today's
multi-mention queue; that fix is a follow-up ticket, not this PR.

### 2. Admin API + UI

- `GET/PUT /api/relay/channels/{id}/watchers` (`{"agents": [...]}`), and
  `GET /api/relay/channels/{id}/watch-turns?limit=` (recent rounds+turns).
- `require_admin` (not `INVOKE_ROLES`); an agent API key gets 403. Agents
  cannot edit watchers.
- PUT with a non-empty list sets `dispatch_mode="watchers"`; an empty list
  restores `mentions`.
- Validation: room is a channel (not dm/thread); agents exist, are enabled,
  max 5, no duplicates; each owns an active Discord identity that currently
  has `can_read` + `can_history` on the endpoint. `can_send` is a warning in
  the response (leases refresh), re-checked at turn start.
- Schemas in `api/schemas.py`, SDK regenerated (`sdk/regenerate.py`), facade
  restarted after deploy.
- UI: "Watchers" row on the room settings panel in `pages/Relay.tsx`
  (ordered chips, up/down buttons, add/remove) + a small recent-turns list.

### 3. Classification (connector + router)

A post is a **watch candidate** only from message-level facts, identical for
every bot's observation:
- human author (`author_bot` false; connector already excludes bots and
  webhooks), in a `watchers`-mode channel;
- addresses **no** platform bot. The connector's `mentioned_bot_ids` changes
  from a content regex to `message.mentions` bots **plus the author of a
  replied-to message if it is a bot** (reply-ping). A post addressed to any
  bot is never a watch candidate; it takes the existing addressed path only.

So a reply-ping to Pai can never be a watch turn for Kai/Olu.

### 4. Rounds (router + reconciler)

- On a watch-candidate observation: if the room has an **open** round,
  attach (update `last_message_id`, record this identity's observation on its
  pending turn); otherwise open a round with one `pending` turn per current
  watcher in order (re-check enabled + access; failures recorded as
  `skipped_access`). Opening is idempotent on `anchor_message_id`
  (unique) — concurrent observations from three connectors race safely
  (IntegrityError → re-read).
- A turn **starts** when every earlier turn is `done` and its own
  observation has arrived. At start: re-check the watcher is still configured
  (`skipped_removed`), access incl. send (`skipped_access`), and the watch
  budget (`skipped_budget`). Then create the run through `_decide`'s
  materialization with trigger message = round's `last_message_id`.
- **Coalescing by round, not by message:** posts that arrive mid-round join
  it; each later watcher's transcript includes them. Posts after the round
  closes open the next round. No per-message queues → nothing strands.
- A turn **finishes** (done) when its run is terminal **and** one of: a
  decline was recorded; no delivery exists; its delivery reached a terminal
  state (accepted, failed, denied); or its delivery has been pending/unknown
  for > 120 s (outcome `delivery_failed`, reason recorded). An earlier
  watcher's message can therefore not land after its successor's except in
  that timeout case.
- **Reconciler:** a 15 s loop in the router process (plus run-terminal and
  delivery events as fast paths) advances rounds: finishes turns, starts the
  next, marks `skipped_no_observation` after 60 s with no observation, and
  closes rounds whose turns are all done. Events only accelerate; the loop is
  the source of truth, so lost events or restarts cannot stall a room.

### 5. Delivery fence (security seam)

- `queue_final` and `queue_send(answer_to=…)` accept a non-addressed
  observation only if a `watch_turns` row exists with `run_id == run.id`,
  matching `identity_id`, state `running`, and `answer_to` equal to the
  round's `last_message_id` (the run's trigger).
- **A watch run may not send anywhere else:** during a run that holds a
  running watch turn, `queue_send` without `answer_to`, or to another
  endpoint, is refused. (Unaddressed human text now triggers tool-holding
  runs; this keeps that from widening what they can post.)
- Existing `answer_key` dedupe stays, so retries cannot double-post.

### 6. Declining (`NO_REPLY`)

- Recognized only for a run that holds a running watch turn. The final is
  normalized (strip whitespace, backticks, trailing punctuation;
  case-insensitive); if it equals `NO_REPLY`, the recorder skips
  `queue_final` and sets the turn's outcome `declined` in the same
  transaction (`recorder._post_reply`, before `queue_final`). Anywhere else a
  literal `NO_REPLY` is posted unchanged.
- Room content can induce a watcher to decline *its own* turn; it cannot
  suppress another watcher (decline is bound to the run). A human can always
  @mention. Accepted.

### 7. Prompt

Watch turns use `_EXTERNAL_RULES` (delivery is automatic; don't duplicate it
with a tool) **plus**:

> This post was not addressed to you; you are watcher {position} of {n} in
> this room. Earlier watchers' replies, if any, are in the transcript. Reply
> only if you have something distinct and useful from your role. Otherwise
> your entire final answer must be `NO_REPLY`. Do not repeat or summarize
> another watcher, and do not post anywhere else.

No Task hand-off from watch turns (avoids uncapped fan-out).
`build_mention_prompt` gets `watch=(position, n)`; `_spec` reads it from the
turn row.

### 8. Budget

Watch turns have their own cap, `relay_watch_turns_per_room_hour` (default
12), and are **excluded** from the channel/global caps that gate explicit
mentions, so chatter can never block Kyle's @mention. Over cap →
`skipped_budget`.

### 9. Pai's sweep

`scan_batch` (inside its endpoint loop, so `acknowledge_scan` and
`has_scan_activity` agree) skips endpoints whose channel is in `watchers`
mode. Its job prompt replaces "nudge by DM" with "only rooms without
watchers remain; post a note to Kyle in #general if something needs an
owner". With the assignment above every channel is watched and the sweep goes
idle.

### 10. Loop safety

Hop counts don't bound external rooms (human authors are hop 0). Loops are
prevented by: bot/webhook posts are never observed as addressed or watch
candidates (connector), watchers' own posts are bot posts, and one open round
per room.

## Tests

`tests/test_room_watchers.py` (+ connector test):
- API: CRUD, order, admin-only (agent key 403), dm/thread rejected, no-read
  rejected, empty list restores `mentions`.
- classification: plain human post → round; bot post → nothing; @mention of
  Kai → addressed path only; reply-ping to Pai (connector reports Pai's bot in
  `mentioned_bot_ids`) → no round.
- ordering: 3 watchers; turn 2 starts only after turn 1 delivery terminal;
  transcript of turn 2 contains turn 1's reply.
- decline: normalized variants (`NO_REPLY.`, `` `no_reply` ``) decline on a
  watch run; literal `NO_REPLY` on a normal run is posted.
- failure: failed run advances; delivery denied/failed advances; pending
  delivery > 120 s → `delivery_failed` and advances.
- stall: watcher observation missing 60 s → `skipped_no_observation`, next
  runs (reconciler tick).
- burst: 3 posts while turn 1 runs → one round, turns 2–3 see all 3 posts,
  next post after close opens round 2.
- concurrency: three observations of one post → exactly one round.
- reconfiguration: watcher removed / access revoked before its turn →
  skipped; reorder affects only new rounds.
- fence: watch run cannot send without `answer_to` or to another room;
  addressed turns unchanged.
- budget: watch cap hit → `skipped_budget`; explicit @mention still invoked.
- sweep skips watched endpoints; restart mid-round resumes via reconciler.

## Rollout

1. Build backend + connector + web; full backend suite once; connector tests;
   web build; SDK regen drift check.
2. Deploy backend image (api, dispatcher, recorder), connector-discord, web;
   restart facade. **All before any watchers are configured** (an old recorder
   would post a literal `NO_REPLY`).
3. PUT watchers (the feature flag), confirm the persona-daily channel name.
4. Live check: one unaddressed post each in #persona-daily, #research,
   #digital-twin, #general; evidence script reads `watch-turns` and Discord.

Follow-up ticket: fix the same stall classes in the multi-mention queue.

## Dispatch plan

| Unit | Who | Scope | Test command | Est. weekly % |
|---|---|---|---|---|
| A: data, API, validation, sweep skip, SDK | main | §1, §2 (API), §9 | `pytest tests/test_room_watchers.py -k api -q` | 1.5 |
| B: classification, rounds, reconciler, fence, decline, prompt, budget | main (opus — seam: delivery fence §5) | §3–§8 | `pytest tests/test_room_watchers.py tests/test_relay_router.py -q --tb=short \| tail -40` | 4 |
| C: connector `mentioned_bot_ids` | main | §3 | `pytest services/connector-discord -q` | 0.3 |
| D: web Watchers row | sonnet | §2 UI | `npm run build -w web \| tail -5` | 0.8 |

- Full suites: one backend run by main after A–C, background, `| tail -40`.
- Reviews: done (five, pre-build). Post-build gate = targeted tests + live check.
- Gate: ≈ 10% weekly. Stop if Claude weekly headroom < 25%.

## Review disposition

Adopted: separate rounds/turns state machine instead of reusing the
multi-mention queue (all five); reconciler with observation and delivery
deadlines (Sol 1-2, Sonnet 3-4, Astra 4-6, Opus 4-5, Fable 4,6); round-level
coalescing (Sol 3, Sonnet 2, Astra 2, Opus 2, Fable 7); classification from
message-level facts incl. reply-pings (Opus 1, Sonnet 7); watch runs may only
answer their trigger (Opus 7-8, Astra 8); decline bound to run, tolerant,
watch-only, recorded on the turn (Opus 10, Astra 9, Fable 5, Sonnet 3);
keep `_EXTERNAL_RULES` (Astra 10); separate watch budget (Opus 9, Fable 8,
Sol 5); turn table as the "why" timeline + endpoint (Sol 6); reconfiguration
rules (Sol 4); no dm/thread watchers (Astra 7, Opus 14); `dispatch_mode=
watchers` generalizes `default_agent` (Fable 1); admin-only + SDK regen
(Sonnet 9); deploy-then-configure (Sonnet 12); no Task hand-off from watch
turns (Opus 11); honest hop note (Opus 12); soft can_send validation (Fable 13).
Deferred: receipt back-filling run_id onto pre-existing mirrors (Opus 3, Astra
3) — not needed once advancement keys on delivery state; multi-mention queue
fixes (ticket). Rejected: none.
