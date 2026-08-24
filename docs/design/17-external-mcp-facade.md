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
  services with webhook secrets, design-16). Everything else is a tool.

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

## Explicitly not now

Write-scoping beyond the role ladder (a reader key already gets 403s from
write tools); OAuth; exposing the facade off-LAN; resources/resource-templates
(everything is a tool — simplest for Claude Code's tool-search).
