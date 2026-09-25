# Design 33 live baseline — 2026-09-25

This records the starting boundary for migrating the five existing Apps. It is
an observation of pai and the current code, not a proposed replacement for the
domain services. The action-by-action inventory is in
[33-operation-inventory](33-operation-inventory.md).

| App | Current service and state | Published DB page | Presentation decision |
|---|---|---|---|
| Running | Ready; Postgres projection; `app.running.inbound` and `app.running.brief.posted` | `e653369d2d814705a2104afb272815c7`, v3 | Typed summary, ten recent activities, Ticket action and snapshot; keep coaching, PRs, heatmap and calendar in the linked domain UI until parity is demonstrated |
| News | Ready; Postgres archive; three news Kafka topics | `b753850e165544309b9701d5c29c3a1e`, v2 | Typed counts/dates and ten recent stories; keep topic search and full item browsing in the linked domain UI |
| Stockmarket | Ready; Postgres archive; inbound and brief Kafka topics | `838ff69d3f9c4a8984e660c063f0e349`, v2 | Typed counts/dates and user watchlist; keep charts, watchlist editing and briefs in the linked domain UI |
| TCMS | Ready; Postgres evidence; `app.tcms.run.recorded` | `e21cdc52364b4db1b5ad73634b1c04a0`, v2 | Typed attention counts and recent test runs; keep cases and evidence detail in the linked domain UI |
| TTRPG | Ready; separate `claude-ttrpg` image and dedicated world PVC; no app Postgres or Kafka declaration | `4fa2c80ea71c4184a570779dbde123f0`, v3 | DB collection/navigation plus ten recent Relay messages; keep the real-time game/player/spectator interface in its specialized UI |

The five rows above came from authenticated `GET /api/apps` on pai: each
declared service was ready. The published pages and domain links returned 200
in browser checks. A second direct browser pass found all five specialist
routes returned 200 with zero page errors after their initial render: Running
showed calendar, mileage, records and coach brief; News showed today's archive
and topics; Stockmarket showed brief and watchlist; TCMS showed overview and
evidence sections; TTRPG showed its story/map/party view. Running's
owner-only read returned ten rows with the five
approved fields, and its page passed desktop and 390px browser checks with no
horizontal overflow. At this checkpoint the Running projection reported 37
runs, 48 activities, 339.9 km and latest activity on 2026-09-20. These are
observations, not fixtures or performance targets.

News v2 returned ten recent stories with four approved text fields. TCMS v2
returned four test-run rows; Stockmarket v2 correctly returned an empty
watchlist for this principal. All three rendered at 1280px and 390px without
page errors or horizontal overflow. Their specialized interfaces remained
available at the linked routes throughout the cutover.

TTRPG v3 reads the fixed `#ttrpg-table` room ID through the reviewed Relay
adapter. The API rechecks current room membership on each read, returns only
ten bounded text rows, and refuses snapshots of the chat read. The published
page displayed ten conversation cards at 1280px and 390px with zero browser
errors or horizontal overflow. The dedicated game interface remains linked.

The platform API authenticates the viewer and restricts a Live View to the
collection owner or an admin. New page reads use a closed operation name,
published binding and bounded server adapter. Ticket creation additionally
requires a browser session, role and explicit operation grant; intent and
receipt rows are durable. App services still own ingestion and calculations.
The API sends them a gateway identity only after its own viewer check. Live
read and snapshot responses use `Cache-Control: private, no-store`.

App collection identity, page drafts/versions, grants, intents, invocation
receipts and snapshots are Postgres rows. Snapshots have a 30-day expiry and
owner-only Resource read; deletion and expiry are checked at read time.
Binary artifacts remain in the existing artifact store. The domain-specific
data stores and TTRPG world PVC remain in place. Database backups cover the
new rows; there is no new cache, queue or object store in this design.

The platform runner images pin Claude Code 2.1.214 and Codex 0.155.1. The
laptop's installed clients observed for plugin registration were Claude Code
2.1.282 and Codex 0.156.1. The `agent-platform-coding` package is installed
locally in both and injected into exact pinned runner images as assigned
SKILL.md files. Image/version behavior was checked in the package smoke tests;
model use is a separate evaluation gate.

Still to measure before retiring a specialized screen: representative page
latency, CPU/RSS on pai, operation denial/receipt rates, and domain-specific
output parity (Running coaching/PRs/calendar; News freshness and archive;
Stockmarket units/formulas; TCMS case evidence; TTRPG player/spectator
interaction). The DB pages currently serve as new collection entry points,
not replacements for those richer interfaces.
