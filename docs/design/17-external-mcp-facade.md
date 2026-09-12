# 17 — External MCP facade (the platform API as MCP tools)

## Problem

External LLM clients (Claude Code on a laptop, side projects embedding Claude)
have no first-class way to manage the platform — the REST API works but means
curl-through-Bash, and the in-cluster MCP broker is deliberately agent-identity
scoped (design-13) and unreachable from outside.

## Decision

A new, separate `ap-mcp-facade` service: an MCP server generated from the
platform's own OpenAPI specification via `FastMCP.from_openapi()`, proxied at
`/mcp` through the existing web ingress.

- **Single source of truth.** The same OpenAPI document that generates `sdk/`
  (CI drift-pinned) generates the MCP tool surface. Full API capability by
  construction; the facade can never drift from the API.
- **Separate from the broker on purpose.** `ap-mcp-broker` authenticates run
  identities and scopes tools to an agent's grants; the facade authenticates
  platform API keys and lets the existing role ladder authorize each call.
  Mixing the two would blur the security domains design-13/15 separated.
- **Per-request auth passthrough.** The caller's `Authorization: Bearer ap_…`
  header is forwarded verbatim on every upstream request; the facade holds no
  credential of its own and adds no authority. A caller without a key gets the
  API's own 401s. Attribution (`agent_versions.changed_by`, audit) works
  unchanged because the API sees the caller's key.
- **Exclusions** (RouteMap EXCLUDE, list pinned by a test): session auth
  endpoints (`/api/login`, `/api/logout`, `/api/setup`), run-scoped internal
  endpoints (`/api/runs/{id}/session`, `…/agentdef` — session-token-only), and
  webhook ingress (`/api/webhooks/{path}` — that surface is for external
  services with webhook secrets, design-16). Beyond these, the surface is
  curated into three tiers (see Curation below) rather than "everything else is
  a tool".

## Exposure

`pai:8090/mcp` → ap-web nginx proxy → `ap-mcp-facade:8000` → `ap-api:8000`,
with network policy edges to match (web→facade, facade→api). Streamable-HTTP
transport. Client setup:
`claude mcp add --transport http ap http://pai:8090/mcp --header "Authorization: Bearer ap_<key>"`.

## Spec acquisition

The facade fetches `/openapi.json` from ap-api at startup (retry until ready),
so a redeployed API automatically refreshes the tool surface on the facade's
next restart; a facade restart is part of the documented deploy flow for API
changes. No spec is baked into the image.

## Curation (2026-08-24)

"Everything else is a tool" over-served an external bearer client, so the
surface is curated into three tiers. Each of the 92 graded `/api` operations
(the universe once the six design-17 session/internal exclusions above are set
aside — `verify_secret`, `POST /api/secrets/{name}/verify`, made it 92 not 91)
gets one decision, pinned by the facade tests against the real OpenAPI document:

- **KEEP (54)** — the day-to-day management surface: observe/operate runs,
  conversations, jobs, schedules; manage agents; review/merge pending changes;
  memory, metrics, health, DLQ, reports, registries, apps, help. Always tools.
- **GATE (21)** — authorized-but-sharp: the credential/secret plane (API keys,
  password, secrets, webhook secrets), admin audit reads, and
  destructive/bulk/irreversible ops. OFFERED only when the facade env flag
  `AP_MCP_ADMIN_TOOLS` is truthy (chart value `mcpFacade.adminTools`, default
  false). Gating is offering, not authorization: the caller's bearer is still
  forwarded verbatim and the API's role ladder authorizes every call either
  way — the flag controls the menu, not the kitchen.
- **EXCLUDE (17)** — UI form-feeders, reviewer digests the client can compute
  from the diff, git-edit conveniences redundant with having the repo, and
  system-agent endpoints. Never tools regardless of the flag.

Default surface at that pass: **54 tools**; with `AP_MCP_ADMIN_TOOLS=1`,
**75** (KEEP + GATE) — see Relay below for the current totals. A handful of
ambiguous auto-generated names are clarified
(`overview` → `metrics_overview`, `notify` → `notify_channel`, `set_enabled` →
`set_schedule_enabled`, …). The surface is computed once at startup, so a
facade restart is required after flipping the flag. Full per-tool rationale:
`.superpowers/sdd/2026-08-24-facade-curation/scope.md`.

## Curation: Relay (design-19, 2026-09)

Relay's 13 `/api/relay/*` operations were graded the same way:

- **KEEP (9)** — the day-to-day room surface: list channels and read one, page
  and post messages, toggle a reaction, open a DM, search, presence, stats.
- **GATE (3)** — the channel lifecycle: `POST /api/relay/channels`, and
  `PATCH`/`DELETE /api/relay/channels/{id}` (rename/topic, archive). Naming and
  retiring the rooms of the place is the operator's, not any bearer's.
- **EXCLUDE (1)** — `GET /api/relay/channels/{id}/events`, the SSE stream. It
  is a `text/event-stream` that by design never ends, so as a tool it would be
  a call that never returns — the one shape MCP cannot represent.

Totals after Relay: graded universe **104** operations — KEEP **63**, GATE
**24**, EXCLUDE **17**. Default surface: **63 tools**; with
`AP_MCP_ADMIN_TOOLS=1`: **87 tools**.

## Curation: Tickets (design-20, 2026-09)

Tickets' 10 `/api/tickets*` operations were graded the same way:

- **KEEP (9)** — the whole board: list and read a ticket, file one, edit it,
  move it between columns, assign it, comment on it, plus the `stats` and
  `projects` rollups. A ticket is how work is asked for, so an MCP client that
  cannot file or move one is missing the point of the board.
- **GATE (0)** — nothing new. A ticket lives in a Relay channel, and the
  channel lifecycle (create/rename/archive) was already gated by Relay.
  `assign` and `create`-with-assignee summon an agent run by default
  (`notify`), which an operator-role key can reach for every project; that
  stays KEEP rather than GATE because the summons is an ordinary Relay
  mention bound by the same per-channel and global invocation budgets as a
  posted `@name` — the facade adds no way around them.
- **EXCLUDE (1)** — `GET /api/tickets/events`, the board's SSE feed, for the
  same reason as relay's: a stream that never ends cannot be a tool call.

Totals after Tickets, counted from a fresh spec: **126** graded operations —
KEEP **73**, GATE **26**, EXCLUDE **27** (18 curated out, 9 session/internal/
streaming). Default surface: **73 tools**; with `AP_MCP_ADMIN_TOOLS=1`: **99
tools**. These per-tier figures are computed from the OpenAPI document and the
rule tuples, so they supersede the hand-counts in the passes above.

## Explicitly not now

Write-scoping beyond the role ladder (a reader key already gets 403s from
write tools) — noting the curation's GATE tier now hides the sharpest write
tools at the OFFERING level too, independent of that authorization; OAuth;
exposing the facade off-LAN; resources/resource-templates (everything is a
tool — simplest for Claude Code's tool-search).
