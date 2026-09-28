# 34 — Personas, workers, and typed Relay

Status: implemented and deployed after four adversarial reviews, 2026-09-28. Extends designs
[28](28-connected-chats-and-unified-routing.md),
[32](32-agent-types-teams-projects.md), and 33. Existing runtime behavior below
was checked against the repository; deployment evidence belongs in the final
implementation record.

## Outcome

Personas own relationships and external communication. Workers produce useful
platform state: reports, activities, market data, software, tickets and artifacts.
Relay is their internal workspace and a read-only archive of external chats.
Changing Pai's preferred channel from Discord to another connector must not
require changes to News, Running, Stockmarket, health checks or coding workers.

A health worker records an actionable incident and asks Pai internally to
consider notifying Kyle. Pai uses her tools, memories and judgment to choose
whether and how to interrupt him. The platform does not automatically broadcast
worker output externally. Routine collection and artifact generation continue
without a persona run. Human notification costs an explicit persona invocation.

## Definitions and source of truth

`agent_type` remains `persona` or `worker`. Both are persistent identities with
ordinary runs and grants. Personas can collaborate internally and invoke workers;
workers can communicate internally and retrieve permitted platform outputs.
Neither type substitutes for an execution role or Tool grant.

System ownership is separate from type. A system worker implements a platform or
connector capability and has a versioned definition in code. User workers and
personas are DB definitions. A system worker's enabled state, supported operational
overrides and schedule choices are DB state. System workers cannot own external accounts. A materialized
system definition in `agent_defs` is a derived runtime cache, never a second
independently editable source. Its source key and revision must be visible.

A Chat Identity is an external account owned by exactly one enabled persona when
operational. One persona may own several identities, including future providers.
An unassigned identity may exist for setup but cannot receive agent routing or
send messages. Ownership is a privileged grant, not an editorial prompt change.
Changing an owner changes both inbound routing and outbound authority together.
`ChatIdentity.owner_agent` is authoritative; the old per-agent Discord identity
field is a temporary compatibility view, not independent authority.

## Existing behavior that must change

- `Conversation.home` already records `relay` versus `external`, but open Relay
  membership currently includes all enabled agents regardless of origin.
- `relay_store.outbound_for_message` mirrors any bound-room message, including
  worker messages, into Discord. A binding currently also represents permission.
- Binding uniqueness is `(connector, external_ref)`, preventing two account
  identities from observing the same external endpoint independently.
- Discord's `CONNECTOR_AGENT` supplies an inbound fallback independent of the
  agent's `discord_identity_id` grant. They can disagree.
- Apps publish `discord.channel.post`; `discord_chat` can send outside the room
  path. Existing deployment inventory identifies health-monitor as the legacy
  default-identity grantee, Pai without an identity, and Kai owning discord-kai.
- System rows originate from seed helpers but retain editable prompts/grants;
  `system` currently mostly controls lifecycle/display, not definition ownership.

## Channel and permission contract

Reuse `home` as the authoritative channel type instead of introducing another
partly overlapping enum. Keep `kind` (channel/group/DM), reply threading, project
scope and archived state orthogonal. An external room is never an open internal
channel, even if its historical `open` flag is true.

| Caller | Open internal | Closed internal / native DM | External mirror | External send |
|---|---|---|---|---|
| Enabled worker | Read/write with Relay grant | Explicit membership | Denied by default | Denied |
| Worker with global observer grant | Same | Explicit membership | Read-only across mirrored external rooms | Denied |
| Enabled persona | Read/write with Relay grant | Explicit membership | Read with current owned-account access | Connector Tool with owned identity and provider permission |
| Human operator | Existing authorized Relay reads/writes | Existing human policy | Read-only archive | Existing explicit connector administration, never Relay composition |
| Connector service | No general Relay access | No general Relay access | Ingest/update its authenticated identity's observations | Only approved, current-identity delivery |

The observer grant is explicitly broad: it includes private external chats
retained by the platform. Label this scope in the editor. It does not expose
private native DMs, grant provider credentials, permit participation, or make
workers summonable in external rooms. Disabled agents have no operational access.

Centralize separate decisions for read, write, summon and external send. Apply
them to listings, snippets/counts, search, SSE, threads, reactions, project lookup,
run context, API, broker, Tickets and Live View adapters. An external mirror must
reject Relay posts/reactions and legacy Conversation continuations server-side,
including writes by its owner. Tickets remain internal; a mirrored room cannot
be a ticket project. Read-only must not merely mean a hidden composer.

Native private memberships remain explicit. System ownership never bypasses
membership. Existing hop limits, wake coalescing, budgets and broadcast opt-in
remain in force; observer status cannot satisfy summon authorization.

## Provider access and transport

Keep historical conversation/message IDs as audit references. Active canonical mirrors may have new room IDs; do not transfer ambiguous historical transcripts into them.
Represent an endpoint canonically by provider plus its stable endpoint ID, with
identity-scoped bindings unique on `(connector, identity_id, external_ref)`.
Several identities can observe the same canonical room, but each binding records
its own current read/send permission and last verification. A binding's existence
alone never proves access. Private endpoints whose IDs are account-local also
include the provider's account namespace in their canonical key.

The connector reports accessible servers/channels, threads and DMs through its
existing projected-ServiceAccount authenticated API. The server derives the
identity from authentication; it does not trust a request's identity or owner.
Discovery includes readable endpoints even before someone speaks. Missing access,
account disable/delete, owner removal, permission changes and stale connector
observations revoke operational visibility and queued delivery. On startup or
reconnect the account is unavailable until a complete permission inventory has
succeeded. A failed refresh must not be mistaken for an authoritative empty list,
but stale access expires; use an explicit bounded freshness interval and publish
its value in implementation evidence. Revocation cannot erase already disclosed
model context; it must prevent new reads, resumptions and sends.

Canonical provider-message deduplication prevents two identities receiving one
Discord event from duplicating the mirror. Record delivery observations separately
where needed for account provenance. Historical source-binding/message IDs remain
valid. An account-specific visibility epoch prevents newly assigned personas from
silently inheriting a prior owner's resumed CLI session. Reassignment invalidates
pending runs/wakes/deliveries and starts new persona session context; historical
sessions remain stored for audit.

External ingress routes only to an eligible owning persona. Explicit addresses
cannot invoke workers or another account's persona through this identity. Shared
server channels wake on addressing that account; direct chats and established
persona threads may default to their owner. Mere channel discovery or ambient
traffic must not wake every persona. Provider-native bot mentions, rather than
arbitrary `@pai` text, establish the addressed identity. Account-local invalid
addresses suppress fallback as in design 28.

Personas use connector-backed messaging Tools for discovery, reads and sends.
Use provider-neutral identity ownership in the model while keeping provider
operations explicit where their semantics differ. Retire `discord_chat` as the
legacy agent-native integration. The replacement `discord` connector Tool is available through identity ownership
rather than an independent send checkbox. It requires the current run's
persona, owned identity, exact endpoint ID and action permission at dispatch.
No silent default bot, ambiguous channel-name selection, or token in agent pods.

A persona's final answer to an external conversation is a connector-owned send
through the same authorization path, not generic Relay mirroring. An explicit
Tool send records its mirror after provider acceptance; transport echoes dedup.
Track whether the addressed turn was already answered so automatic final delivery
does not repeat a Tool-sent response. Internal replies never become sends.
Do not cross-post from one external endpoint/account to another implicitly.

Reuse Postgres and Kafka for queued sends and durable delivery receipts. A send
references a server-created request ID, owner/permission generation, run and
endpoint. The connector revalidates authority immediately before effect, rejects
legacy/unattributed payloads, and records provider message ID or failure. Retry
only when known not delivered; a timeout after possible delivery is `unknown`,
not successful and not blindly retried. This is not a claim of exactly-once
provider delivery. Paused connectors preserve pending requests; revoked requests
become denied rather than finding another identity.

## Workers, system definitions, and output paths

Create a small registry of code-owned system definitions. Platform infrastructure
workers live with backend capability code; connector collectors live with their
connector/capability package. Registry entries specify source key/revision, base
definition and an explicit override allowlist. Do not introduce a new service or
agent execution engine. Existing cron, Kafka events and Relay invocations execute
them through the current dispatcher and per-run credentials.

Reconciliation is idempotent and preserves identity, runs, memories and version
history. Reconcile only named registry entries, not every row whose historical
`system` flag is true. Reject edits and rollback attempts to code-owned fields
through all API/broker paths; supported DB overrides survive code upgrades.
Code removals disable the worker without deleting its history. Source collisions
and unknown overrides stop that entry's reconciliation visibly, not the API.

Migrate existing infrastructure workers (including health-monitor, Wiki and
system image generation) according to their actual ownership. News/market/running
collection jobs qualify only where they are shipped feature implementation;
user-authored analysts, coaches, artists, coder and QA remain DB configuration
unless the baseline identifies an existing code-owned service contract. Preserve
current meaningful behavior rather than turning every specialist into a system
worker solely because it has a schedule.

News freshness rules, market projections, Strava imports, coaching reports,
artifacts and TCMS evidence ingestion stay operational. Remove only external
broadcast production and consumption. Completion remains visible through platform
state and internal Relay when useful. Update prompts that currently demand Discord
posts; no worker should fail a job because an obsolete send Tool disappeared.

Health stores evidence in an OPS Ticket and an internal intervention request addressed to Pai with
a stable incident key, summary and artifact/ticket links. Persist unresolved
requests using existing Tickets/Relay state so Pai's unavailability or a suppressed
wake cannot lose them. Repeated checks update the same incident; recovery closes
it. Pai retrieves pending requests on her next eligible internal invocation and
records disposition. Avoid adding a replacement automatic external alert path.

## Migration and rollout

| Existing state | Migration |
|---|---|
| Ordinary internal channels/DMs | Preserve IDs, memberships and histories; no external delivery |
| Existing external conversation | Preserve ID and restricted external history for administration; create separately authorized active mirror |
| One-endpoint legacy bound internal room | Preserve internal room and audience; disable bridge; create separate external mirror |
| Mixed or multi-endpoint bound workspace | Disable bridge; retain internal workspace; create separate external mirror(s), link historical origin explicitly |
| Null Discord identity binding | Assign explicit default identity only from verified historical provenance; quarantine ambiguity |
| Default bot granted to health-monitor | Remove worker identity/send grant; assign default account to Pai |
| Kai identity | Preserve Kai ownership; do not claim functioning transport until provider intents and deployment verify |
| Olu account without persona | Keep unassigned and inactive for routing; do not invent a persona |
| Discord grants/secrets | Remove legacy Tool grants; reject connector credentials through both direct and skill-derived secret bindings |
| Editable system seed row | Snapshot/version existing state, classify ownership, reconcile registry plus allowed overrides |
| App Discord broadcasts | Stop producers and refuse legacy topic effects; retain data/report ingestion |

First export a redacted inventory: agents/grants/system flags, schedules, binding
shapes, identities and provider access, outstanding wakes/runs, and app broadcast
callers. Record stable IDs and expected mappings. Migration must be idempotent,
transactional where rows change together, and must not rewrite historical prose
or delete messages, artifacts, reports, versions or sessions.

1. Add ownership, observer grant, provider access and delivery metadata; implement
   shared server policy and focused regression coverage. Freeze legacy sends
   before changing live account ownership.
2. Add connector inventory/permission reporting, account-scoped ingress and the
   guarded send path. Migrate endpoint bindings and preserve history. Cut over
   API, router, recorder and connector together; old payloads cannot pass.
3. Ship code-owned worker registry and override controls. Migrate identified
   system workers and health handoff; update persona/worker instructions and
   remove app broadcasts and deprecated Tool surfaces.
4. Update agent editor, connector account ownership, Relay grouping/read-only
   UI, source labels and error states. Regenerate affected operation catalogs
   and SDK contracts rather than leaving stale names callable.
5. Deploy and verify with disposable internal/external endpoints, then observe
   existing scheduled feature outputs and persona conversation paths. Record
   what is verified, blocked by provider setup, and intentionally inactive.

Rollback restores service availability while retaining the stricter authorization
checks. Never roll back by re-enabling unrestricted mirroring, app broadcast
consumption or worker token access. Keep legacy fields read-compatible for one
cutover; remove only after caller inventory and migration checks pass.

## Acceptance and adversarial cases

- A worker reads/posts to an open internal channel, cannot enter a private native
  DM, and cannot read external metadata/search/SSE without its observer grant.
  Observer can read external mirrors but cannot write, react, send or be summoned.
- Pai and Kai can independently observe a shared provider channel; one provider
  message produces one mirror row and only the addressed owner wakes. Workers
  remain unsummonable even if stale participant rows or explicit mentions exist.
- Revoking provider visibility, identity ownership or agent enablement blocks
  reads/context and pending sends; unassigned/Olu and stale access fail closed.
  Reassignment does not reuse the old owner's session or queued reply.
- A persona Tool send and automatic final reply do not duplicate one addressed
  response. Delivery failures/unknown outcomes remain visible; Kafka replay and
  echoes do not fabricate successful delivery or send under a fallback account.
- A direct API post, legacy conversation continue, broker call, Live View action,
  Ticket operation or recorder event cannot write/send through an external mirror.
- Connector token references, including dynamic names and skill-derived grants,
  cannot enter worker or persona run pods. Worker type changes and rollback
  cannot retain identity authority; grant writers cannot bypass uniqueness.
- Registry edits are rejected, supported overrides survive reconcile, code
  revision creates auditable definition versions, and a restart does not
  resurrect removed identities, schedules or deprecated Discord grants.
- News/Stocks/Running still ingest useful data and save outputs without emitting
  external broadcasts. One actionable health incident creates one durable Pai
  handoff; ordinary healthy checks remain quiet and recovery is recorded.
- Migration runs twice with identical results, preserves counts/history links,
  handles mixed bound rooms without exposing internal history to a new audience,
  and refuses ambiguous ownership rather than guessing.

Use focused backend/broker/connector authorization and migration tests, then a web
build and targeted browser checks for agent editing and Relay mirrors. One real
provider conversation and one internal health handoff validate the integration;
do not spend quota on repeated full-suite subagent runs. Reviewers must receive
this design and the actual affected code surface, with accepted/rejected findings
recorded below before implementation begins.

## Review decisions and implementation evidence

Completed: pass 1 Sol + Astra, revised design, pass 2 Sol + Astra, accepted decisions below.

### Pass 1 decisions (root, 2026-09-28)

Accepted Sol S1–S6 and Astra A1–A6, with the following concrete contracts.
These resolve earlier broad wording; implementation uses this section where
it is more specific.

**Ingress and canonical observations.** Discord no longer publishes an
untrusted `conversation.inbound` payload. It submits observations to an API
route authenticated by its existing identity-scoped projected service account.
The server derives the account, validates its current ownership/access and
writes through the existing Postgres/Kafka Relay path. Legacy Discord inbound
Kafka events are refused. A canonical message has provider endpoint/message
uniqueness; an observation is separately unique on message, account and owner
generation. An addressed observation may publish routing work even if another
account already inserted the canonical message. Invocation identity includes
that observation; replay/concurrent arrival cannot lose Kai's addressed turn
merely because Pai observed the same text first. The router selects only the
observed identity's eligible owner. Discovery and ambient messages create no
agent run. Provider bot-ID mentions and actual direct/assistant-thread context
supply the addressed signal; plain display-name text does not.

**Permission discovery.** Discord supports enumerable guild text channels and
currently accessible threads, plus DMs individually observed and verified by
that account. A complete scan means a complete scan of those enumerable scopes,
not a claim to enumerate every historical DM or archived private thread. Unknown
endpoints stay unavailable until an exact endpoint observation verifies them.
Threads require their own membership/permission evidence. Archive access requires
provider history permission as well as present visibility; it is not inferred
from another account's binding. Other future providers can express a conservative
visibility start time when history entitlement is unavailable. Do not implement
new provider integrations as part of this migration.

The connector reports connected state and permission snapshots every 60 seconds;
leases expire after 180 seconds. Ownership generation and monotonic snapshot
sequence fence delayed snapshots. Disconnect and known membership/permission
changes invalidate access immediately, followed by a fresh scan. An unsuccessful
scan does not delete the last inventory, but cannot refresh its lease. A reconnect
must establish a new complete supported-scope snapshot before routing/sending.
Periodic scans do not use paid model calls. Delivery also checks the exact
provider endpoint/action immediately before sending.

**Current authority and sessions.** Add a monotonically increasing agent
authorization generation and freeze it on each run. Increment it on sensitive
grant/type/owner changes and loss of owned-account access. Internal runs that may
have read external context are conservatively fenced by that same generation;
there is no attempt at content taint tracking. Existing run credentials must
check the current generation, not just run ownership. Session keys carry the
run's immutable generation, and GET/PUT must reject old-generation credentials;
a late old session upload cannot contaminate a new session. Apply the guard to
Claude blobs, Codex thread IDs and text fallback. Recheck authority before
external invocation materialization and final-result delivery.

Protected reads include Relay metadata/search/counts/SSE/context, legacy
Conversation reads, external-run prompts/results/transcripts/events/session
and directly run-associated artifacts. Ordinary human administration remains
available. A reader Tool must not bypass the policy by requesting an old run
ID. Historical freeform prose copied into memories/reports/artifacts is not
retrospectively classified; explicit persona sharing of information internally
is a normal intentional workflow, not an automatically blocked dataflow.

**Delivery.** Retire webhooks that impersonate arbitrary Relay authors. Sends
come visibly from the owning account. A durable request has a unique addressed
turn/automatic-reply key where applicable, immutable authority generations,
endpoint and expected chunks. A connector must atomically claim a request via
the API before sending, and persist each accepted chunk's provider message ID.
Concurrent/replayed claims cannot send twice. A claimed attempt abandoned after
a crash, a partial multipart outcome or an ambiguous provider timeout becomes
`unknown`; it is not automatically replayed. Known pre-effect denial/failure is
recorded explicitly. No claim of provider exactly-once semantics is made.

An explicit send can identify the addressed turn it answers. Only a pending,
accepted or unknown send associated with that same turn/account/endpoint
suppresses an automatic final answer; unrelated sends and known pre-effect
failures do not. The connector's provider observation reconciles accepted
outbound messages into the read-only mirror. The receipt remains durable even
if an echo is delayed. Old `discord.channel.post` and generic Relay outbound
payloads cannot cause effects.

**Historical migration.** Existing internal rooms remain internal. Disable
all legacy bridge effects and create canonical external mirrors independently.
This is deliberately more conservative than automatic one-room reclassification:
external-only legacy rooms may remain as restricted history records and acquire
an independently authorized origin reference, but no prior internal or ambiguous
agent posts are exposed to a new owner. New mirrors start with newly authenticated
provider observations. Provenance does not mean a hyperlink grants access: any
origin/history/thread/run target enforces its own ACL and cannot leak forbidden
previews. Retain all original IDs, messages, runs and sessions for administration
and historical access under their existing internal/private audience, subject
to removing inappropriate external-agent memberships. Permission tests use
representative private/internal/external messages, not only row counts.

**Secrets.** Exclude all account `secret_refs` (including dynamic and deleting
identities) from raw agent grants, independently of whether a provider token is
valid. At grant save and immediately before pod creation, validate against the
current account references and the frozen pod injection list. An ordinary secret
cannot be rebound as an account credential while agent grants reference it.
Standard identity secrets use their account-owned names, and raw Discord/token
execution through the old custom Tool is retired. Skills already grant no secrets;
remove the editor's outdated explanatory text.

**System registry.** Initial code-owned entries are exactly
`change-summarizer` (platform changes), `run-summarizer` (run history),
`health-monitor` (platform health), `wiki` (wiki capability) and
`codex-artist` (Codex image capability). Their code manages prompt, description,
type, system ownership, Tool/skill/secret grants and capability identity. They
cannot own external accounts. Preserve operator configuration of runtime, model,
enabled state, concurrency, timeout, retention, quota ceilings and supported
cron cadence. Existing operational settings are migrated into explicit overrides;
code defaults initialize new installations. Prompt/grant changes and historical
rollback into code-owned fields are rejected. User-defined News, stock workers,
Running Coach, artist, coder, QA and TTRPG GM remain user state. Registry removals
disable only their own materialized entry and managed schedules; historical
versions and operator-disabled state survive reconciliation. Arbitrary `system`
flags cannot masquerade as code-owned entries.

**Health handoff.** Reuse OPS Tickets with a stable incident key and dispositions
`open`, `awaiting-persona`, `acknowledged`, `resolved` represented through existing
ticket status/metadata/comments, not another workflow engine. One incident has
one durable handoff thread. Health updates evidence without repeatedly paging
Pai. Recovery resolves the incident and obsolete pending intervention. Pai's
internal invocation context includes a bounded query for unresolved assigned
interventions, so a suppressed/failed initial wake does not lose the request.
Pai's prompt describes recording her disposition; it does not compel an external
notification. Existing scheduled feature projections remain operational, but
app Discord broadcast producers and consumers are removed.

Implementation workstreams after review pass 2: (1) schema/migration and shared
read/write/summon/session policy; (2) authenticated account discovery, observations,
provider connector Tools and delivery receipts; (3) code-owned registry, health
handoff and removal of app broadcasts; (4) agent/Connections/Relay UX and docs.
Run focused tests in each stream, then one combined integration/browser round.
No additional adversarial review agents beyond the requested four are required.


### Pass 2 decisions (root, 2026-09-28)

Both Sol and Astra confirmed the first-pass corrections and independently found
the same silent-expiry gap. Lease expiry is an authorization loss even without
a connector callback. Before materializing a run or validating any run credential
or session operation, synchronously process expired owned-account leases and
advance the owner's authorization generation once for that expired lease.
Endpoint permission reduction also advances that generation. A reconnect may
permit new sessions but cannot restore the old generation. Test both internal
and external Claude/Codex resume, late uploads and text fallback.

First-version Discord archive and invocation access requires verified
`read_message_history` for the exact endpoint. An endpoint visible to a bot but
without history permission is shown as unavailable for agent participation;
receiving one new observation never unlocks another account's older canonical
messages. This deliberately bounded behavior avoids silently substituting live
observation access for archive entitlement. Observer workers retain their
explicit broad read grant. All snippets/counts/context/search use the same rule.

A successful send receipt itself upserts the accepted content and provider
message ID into the canonical read-only mirror via the authenticated connector
path. Gateway echoes are optional reconciliation, never required for visibility.
Receipt replay and later echo must leave exactly one mirror row.

The earlier migration table now matches the separate-mirror decision. Four
reviews are complete (Sol and Astra in each of two passes); subsequent work is
implementation and focused verification, not another design review cycle.


## Implementation and verification record — 2026-09-28

Implemented all four workstreams above. Two rounds of independent Sol and Astra
adversarial review produced four reviews; their accepted decisions are recorded
in this document. The generated SDK and live-operation catalog match the API.

Deployed on pai at Helm revision 79 using the existing cluster and workloads.
Created and completed a database backup before migration. The migration retained
all 906 existing Relay messages, 35 original conversations, 20 agent definitions
and 34 artifacts; discovery added eight separate external mirrors. Existing
public native rooms were not converted into external archives. Pai owns the
default Discord identity; Kai retains its configured identity. Unowned accounts
remain unowned, and no additional bot deployments were invented.

Focused local validation covered ownership and generation revocation, silent
lease expiry, private archives and artifact/session access, authenticated routing,
permission discovery, receipt deduplication and ambiguous delivery handling,
system reconciliation, health incidents, application ingestion, and UI behavior.
The browser checks confirmed editable operational settings, locked system source
fields, identity ownership, and read-only external mirrors. A direct admin POST
to an external mirror returned 403.

A live Pai run successfully called the Discord identities and endpoints Tool
operations and discovered eight permitted endpoints. No external messages were
sent during verification: delivery and provider receipts were tested with
automated connector/API fixtures. A live health-worker run created durable OPS
incidents and handed them to Pai internally. That round exposed a false historical
alert for retired `engineer`; metrics now distinguish enabled current agents,
and the health prompt filters failure streaks accordingly. The false ticket was
cancelled with evidence. The genuine DLQ intervention remains tracked in OPS-26,
with Pai's disposition recorded; it was not blindly replayed.

Worker application broadcasts and the legacy `discord_chat` Tool are retired.
News, Stockmarket and Running ingestion/report persistence passed their focused
tests. No human credentials or provider tokens are part of this change.
