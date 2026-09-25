# Design 33 operation inventory

The checked-in [compiled operation catalog](../../services/backend/agentplatform/live_operation_catalog.json)
currently contains 91 versioned entries. Its generator reads the broker's
callable branches and each custom Tool manifest; a lockstep test catches new
branches. Every current branch has a conservative effect and output class.
`query_app` and Linear's `raw_graphql` retain `unknown` effects because their
arguments choose arbitrary paths/operations. All broker/custom branches are
excluded from Live App admission until a separate human adapter is reviewed.
The nine admitted IDs are the eight bounded App reads and `tickets.create@1`.
The UI exposes the admitted list to page authors, and publication checks it
server-side; a catalog entry alone never grants permission.

This is the admission inventory for Live App calls, checked against the tool
manifests and broker implementation on 2026-09-25. It classifies **actions**,
not just MCP Tool names. A catalog row grants nothing: the viewer still needs
an explicit operation grant, the published page must bind that operation and
target, and the server must recheck both at dispatch. Existing agent Tool
grants and domain APIs keep their current behavior.

| Source | Actions / calls | Effect | Live-page admission |
|---|---|---|---|
| Running projection | `summary.read@1` | private projection read | Eligible; owner-only, scalar-normalized |
| Running projection | `activities.read@1` | private projection read | Eligible; owner-only, ten normalized recent rows |
| News projection | `summary.read@1` | private projection read | Eligible; scalar-normalized |
| News projection | `items.read@1` | private projection read | Eligible; ten recent normalized story rows, text only |
| Stockmarket projection | `summary.read@1` | private projection read | Eligible; user-scoped counts/dates only |
| Stockmarket projection | `watchlist.read@1` | private projection read | Eligible; user-scoped normalized symbol rows |
| TCMS projection | `overview.read@1` | private projection read | Eligible; scalar-normalized |
| TCMS projection | `runs.read@1` | private projection read | Eligible; ten normalized evidence-run rows |
| Tickets | `create` | platform write, wakes the ticket workflow | Eligible as `tickets.create@1`; fixed channel, human review, durable receipt |
| Tickets | `get`, `list`, `search` | platform read | Candidate; define object/thread ACL and a bounded output contract |
| Tickets | `update`, `move`, `assign`, `comment` | platform write, some actions summon agents | Excluded pending per-ticket target authorization and retry semantics |
| Relay | `read`, `channels`, `search` | conversation read | Candidate; room membership and private-thread ACL required |
| Relay | `post`, `dm`, `react` | conversation write, possible agent wake / external bridge | Excluded pending sender identity, target, hop-budget and external-effect contract |
| Wiki | `read`, `search`, `list`, `history`, `wanted` | shared knowledge read | Candidate with bounded result and page ACL |
| Wiki | `write`, `append`, `promote` | shared knowledge write | Excluded pending revision conflict and reviewer/author policy |
| Artifacts | `list`, `get` | private binary/metadata read | Use the existing authenticated Resource/API path, not a new page bridge |
| Artifacts | `save`, `delete` | private storage write/delete | Excluded pending class/retention and target policy |
| Image Gen | `models`, `generate` | catalog read; paid image creation | Existing Studio owns reservation, pricing and artifact lineage; no new admission yet |
| Agent definitions/grants | `agents_edit`, `agents_grant` branches | admin/control-plane write | Excluded from Live Apps; page authors cannot grant themselves authority |
| Runs/metrics/quota | `runs_read`, `runs_write`, `metrics`, `get_quota_usage`, `quota_ok` | mixed read/write and allowance probe | Excluded pending per-operation audience and budget semantics |
| `query_app` | arbitrary allowed app path | variable effect/read scope | Excluded; replace each use with a named, bounded adapter |
| Memory | `read`, `save` | agent-private state read/write | Excluded until a human namespace and delegation contract exists |
| TCMS Tool | `cases`, `case`, `coverage_gaps`, `runtime_report`, `flaky`, `prune_candidates` | quality reads | Candidate; the App overview uses its domain API instead |
| TCMS Tool | `sync_cases`, `record_results` | repository sync / quality evidence write | Excluded; require run/workspace provenance |
| TTRPG Tool | `view`, `gm_view`, `help` | player/GM-scoped reads | Excluded from generic pages; specialized UI enforces seats and visibility |
| TTRPG Tool | `roll`, `floor`, `command` | game state/dice write | Excluded from generic pages; engine/seat/request-id rules stay in game service |
| Strava Tool | `athlete`, `stats`, `activities`, `activity`, `gear` | private external read | Candidate only through a bounded projection adapter; current Running summary does not call it on load |
| Strava Tool | `sync` | import into Running projection | Excluded from passive views; explicit job/refresh ownership needed |
| Stocks, index movers | one read each | external market read | Candidate with rate and result-size policy; current Stockmarket summary uses its archive |
| Prices | price-bar import | external read + archive write | Excluded from passive views; scheduled loader owns it |
| Linear | `teams`, `search` | external read | Candidate after connection/user ACL and bounded output |
| Linear | `create`, `update`, `comment`, `raw_graphql` | external write or arbitrary GraphQL | Excluded pending scoped mutation contracts; `raw_graphql` is never admitted as-is |
| Discord chat | channel post | external send | Excluded pending Chat Identity binding and sender/target policy |

The custom Tool action sets come from `tools/*/tool.yaml`. Core branch names
come from `services/mcp-broker/broker.py`. A future operation catalog should
compile these reviewed declarations and fail publication on an unknown branch,
contract version or effect. The current server implements only the eligible
rows above in `api/live_views.py` and `api/live_invocations.py`; the
candidate/excluded rows are **not** callable through a Live App today.

The next adapter should prove a new policy shape rather than merely copying
the existing ticket adapter. Relay read is a useful candidate because it
forces a real conversation-membership check, bounded transcript output, and
revocation at read time. External sending and arbitrary queries remain behind
their specialized surfaces until equivalent constraints are enforceable.
