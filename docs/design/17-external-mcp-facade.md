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

Default surface: **54 tools**. With `AP_MCP_ADMIN_TOOLS=1`: **75 tools**
(KEEP + GATE). A handful of ambiguous auto-generated names are clarified
(`overview` → `metrics_overview`, `notify` → `notify_channel`, `set_enabled` →
`set_schedule_enabled`, …). The surface is computed once at startup, so a
facade restart is required after flipping the flag. Full per-tool rationale:
`.superpowers/sdd/2026-08-24-facade-curation/scope.md`.

## Explicitly not now

Write-scoping beyond the role ladder (a reader key already gets 403s from
write tools) — noting the curation's GATE tier now hides the sharpest write
tools at the OFFERING level too, independent of that authorization; OAuth;
exposing the facade off-LAN; resources/resource-templates (everything is a
tool — simplest for Claude Code's tool-search).
