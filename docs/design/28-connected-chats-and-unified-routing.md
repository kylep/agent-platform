# 28 — Connected chats and unified Relay routing

Status: **implemented 2026-09-21**; supersedes the Discord-thread compatibility
behavior in design [19](19-relay-agent-messenger.md) while preserving its room,
message, session and loop-guard model.

## The problem

The first Discord connector predated Relay. Mentioning the bot opened a public
Discord thread and created a single-agent `Conversation`; Relay later migrated
every such row as a DM so it could preserve history and CLI resume state. That
made the transport's shape appear to be the relationship's shape: a public
Discord thread was labelled a direct message, and it continued through the old
`continue_conversation` path while channels used Relay's guarded router.

The split had operational consequences. A follow-up received while the agent
was busy could be rejected before its message was stored, connector restarts
forgot which threads were active, Kafka replay had no external message id to
deduplicate, and a message from one Discord endpoint was suppressed from every
Discord endpoint rather than only its source.

## Decision

Keep the existing Relay room, message, binding and per-room/agent session
tables. Separate the behaviors that had been inferred from `kind`:

- `home`: `relay` or `external`; provenance and lifecycle, never a privacy
  assertion.
- `reply_mode`: `linear` or `threaded`; where agent replies render.
- `dispatch_mode`: `facade`, `mentions`, or `default`; who owns invocation.
- `default_agent`: fallback target for eligible unaddressed human text.

A mention-created Discord thread is an external, linear room with default-agent
routing. It appears under **Connected chats**, keeps its stable room id and
session, and enters through the same `relay.messages` router as every shared
room. Existing Relay DMs keep the synchronous compatibility facade until that
API is retired. Bound channels remain Relay-owned, threaded, and mention-routed.

`RelayBinding` is the durable external endpoint. It records the provider room
kind, parent, display name, source link and lifecycle state. The Discord
connector hydrates channel bindings and thread bindings separately on startup;
threads use bot replies and channels use webhooks.

The connector authenticates that startup read with a projected,
audience-bound Kubernetes ServiceAccount token. The API maps only that exact
ServiceAccount to the narrow `connector` role, which can list its binding feed
and cannot use ordinary reader routes. There is no long-lived platform API key
to copy into another Kubernetes Secret.

`RelayMessage` records `source_binding_id` and the provider's immutable message
id. Their unique pair makes inbound replay idempotent. Echo suppression compares
the source binding, so another endpoint on the same provider still receives the
message.

## Routing contract

1. Valid explicit addresses replace the default target.
2. Multiple valid addresses invoke those targets.
3. Any explicit address, including an invalid or unavailable one, suppresses
   fallback. It must never make the default agent impersonate the requested
   agent.
4. Only unaddressed human text may use a default target. Agent replies, system
   notices and event cards never do.
5. The existing membership, hop, hourly budget, cooldown and coalesced-wake
   guards apply equally to default and explicitly mentioned targets.
6. A busy default agent keeps one wake anchored at the first unseen follow-up;
   accepted messages remain durable instead of disappearing.

External participants do not gain the right to admit arbitrary agents. An
explicit mention of a non-member is retained as text and invokes nobody.

## Transcript and session compatibility

Connected Discord rooms remain linear. Reclassifying their provenance must not
nest answers into Relay threads. Agent replies consult `reply_mode`, not
`kind`. Session scope remains `(room, agent)` and preserves both Claude's opaque
session blob and Codex's thread id. Historical room ids, messages, runs and
session blobs are not merged or rewritten.

Historical Discord usernames cannot be safely mapped to snowflakes. They remain
as recorded. New events always use the immutable Discord user id and carry the
current display name separately.

Likewise, old rooms whose only title was `discord:<snowflake>` receive a short
`Discord thread · <suffix>` fallback. The next real message replaces it with
Discord's current thread name; meaningful historical titles are preserved.

## Migration and rollback

The additive boot migration fills only null metadata. Historical connector-owned
single-agent rooms become external, linear and default-routed; shared rooms stay
Relay-owned. Historical bindings for those rooms become `thread` endpoints.
Codex session ids gain their missing `relay_sessions.codex_thread_id` column.

The old columns and conversation facade remain during the rollout. Rollback may
restore facade dispatch without deleting messages or source ids. A connector
must never run both dispatch paths for one room: `dispatch_mode` names the owner.

## Deliberately deferred

- Delivery acknowledgements, retries and a transactional Kafka outbox. Source
  identity now prevents duplicates, but durable delivery state is a separate
  operational feature.
- Inviting an additional agent into a connected room. The authorization and UI
  must be explicit before external users may expand membership.
- Mapping native Discord threads inside an already-bound channel onto Relay
  message threads. A mention-created assistant thread remains its own linear
  room.
- Removal of `/api/conversations`, the legacy columns and the old Kafka topic
  names. They remain compatibility surfaces until observed callers are gone.
