# Design 12 — Tools: executable capabilities as a building block

Status: SHIPPED 2026-08-07 (all 7 phases live-verified on the NUC)

## AS BUILT (deltas from the design below)

- Tool secrets bind by **block name** (like skills), not per-key env
  mappings — secret blocks already declare env-style keys.
- Memory kept the admin ORM surface: the memories table lives in the
  tool's `tool_memory` schema via a per-dialect `schema_translate_map`
  (postgres → tool_memory, sqlite tests → default), so /api/memories and
  the UI needed zero changes while the storage became tool infra. Old
  rows migrated; `public.memories` renamed `memories_pre_d12` as backup.
- Role ladder instead of a single tools-role: core broker tools forward
  the caller token to our API so they earn an **annotator** per-run
  token; custom-only declarations earn the whoami-only **tools** role.
  `memory: true` and the memory/can_invoke flag special-casing for
  tokens are retired (system/can_invoke flags remain).
- `linear` = one tool with canned actions + `raw_graphql` escape hatch;
  `discord_chat` posts via REST with API-side mention suppression
  (allowed_mentions parse:[]) instead of text munging.
- The executor fetches secrets with a namespace-wide read Role
  (resourceNames tightening deferred to design/13 E).
- Live bugs caught by verification: the executor was missing from the
  HTTPS egress allow-list (first pai stocks call failed honestly —
  never-pretend held), and the deploy image name is
  `agent-platform-backend`, not `-api`.
- **Design-15 (2026-08-23) added two more core brokered tools:**
  `agents_edit`/`agents_grant` (agent-definition RBAC — see
  `docs/design/15-db-first-agents.md`). They join the "Core brokered tools"
  table below. They needed a *third* list alongside `PLATFORM_MCP_TOOLS`
  (the original core tools, which forward the caller's token and promote the
  holder to the `annotator` role ladder rung) and the custom-tool registry:
  `PLATFORM_MCP_AGENT_TOOLS`, whose holders stay on the `tools` rung — they
  earn a per-run token scoped to exactly those two tools, same as a
  custom-tool-only agent, deliberately not promoted to `annotator`.
  `GRANTABLE_PLATFORM_TOOLS` is the union used for validation/Help. A
  `tools/agents_edit/` custom-tool directory is refused at the registry as a
  name collision with these core tools, same as any other core-tool-shadow
  attempt.

## Problem

Skills carry *knowledge* but nothing on the platform can *execute* for a
normal agent: the runner unconditionally strips Bash/Read/Write/Edit from
every non-self-edit agent, so a script shipped inside a skill is inert.
The only executable surface is the fixed set of `mcp__platform__*` tools
hardcoded in `services/mcp-broker/broker.py` — 11 fine-grained functions
that have accreted without a taxonomy. Concretely: a stocks capability
that wants kytrade's `providers/yahoo.py` has nowhere to run it, and
"skills" like linear/discord are really curl recipes that only a
shell-bearing agent could ever follow.

## Kyle's direction (2026-08-07)

1. Skills and MCP tools are comparable — extend the Skills page to cover
   tools, full CRUD, same change loop.
2. ALL execution is centralized (no in-pod skill scripts, no scoped
   Bash, no conditional Bash). Skills stay pure instructions.
3. **discord, linear, yfinance, memory become tools.** Tools can declare
   the infra they use — secrets AND db schema (memory is the motivating
   example).
4. **Consolidation convention: 1–2 tools per domain**, not one per
   endpoint: runs (currently 4 tools) → `runs_read`/`runs_write`;
   metrics merged; memory read+write merged (namespacing means there is
   no world where read-only memory makes sense); post_message + the
   discord skill → `discord_chat` (later maybe `discord_admin`);
   `app_api` needs at minimum a much better description. TodoWrite
   should present as "Todo".

## Goals

- **Tools are the 6th building block** (agents, skills, secrets,
  reports, apps, tools): defined in git, PR change loop, CRUD in UI,
  live after sync (deps permitting).
- A custom tool = trusted code + declared infra + JSON-schema'd args.
  The model controls **arguments only**, never code.
- Coarse, meaningful grants: an agent checks `memory`, `discord_chat`,
  `linear`, `stocks` — not seven micro-functions.
- Tool-owned infra: secrets bindings and a provisioned pg schema, using
  the same provisioner machinery apps get.
- First consumers prove each tier: `stocks` (deps only), `discord_chat`
  (secret), `linear` (secret), `memory` (db schema + caller identity).

## Non-goals

- In-pod skill scripts / scoped Bash (dropped — a second softer boundary,
  complicates the runner's one unconditional deny, and would need agent-pod
  internet egress; the executor stays the single egress point).
- Replacing the *core* platform-API tools with git-defined ones — runs/
  metrics/app query stay in the broker (they forward the caller's token
  to our own API); they just get consolidated + renamed.
- `apps/stocks` build-out (follow-on; the stocks tool is the tracer).

## Final tool surface

### Core brokered tools (stay in broker.py; forward caller's token to platform API)

| tool | replaces | notes |
|---|---|---|
| `runs_read(action, …)` | list_runs, get_run, list_tags | action: `list` \| `get` \| `tags` |
| `runs_write(run_id, summary, tags)` | annotate_run | the only run mutation today; more actions later |
| `metrics(scope)` | metrics_overview, metrics_agents, kafka_health | scope: `overview` \| `agents` \| `kafka` |
| `query_app(app, path, params)` | app_api | renamed + rewritten description: "Call a read-only API endpoint of an installed platform app (see the app's skill for endpoints), e.g. the news archive. GET only, traversal-guarded." |
| `agents_edit(action, name?, definition?)` *(design-15)* | — (new) | create/update/delete an agent's prose/config; no grant fields. `PLATFORM_MCP_AGENT_TOOLS`, not `PLATFORM_MCP_TOOLS` — see the AS BUILT note above. |
| `agents_grant(action, name, field?, values?, …)` *(design-15)* | — (new) | assign/revoke an agent's harness/platform tool, skill, secret, `can_invoke`, or `role` grants. Same list as `agents_edit`. |

### Custom tools (tools/ blocks; run in the executor)

| tool | replaces | infra |
|---|---|---|
| `memory(action, …)` | read_memory + save_memory | `database: true` — owns pg schema `tool_memory`; namespaced by broker-verified caller identity |
| `discord_chat(channel, text)` | post_message + discord skill | secret: discord bot token; posts to Discord API directly (connector keeps handling pai's conversational presence). Later: `discord_admin` |
| `linear(action, …)` | linear skill | secret: linear-api-key. action: `search` \| `create` \| `update` \| `comment` + `raw_graphql` escape hatch; the recipe knowledge moves from the skill into tool code |
| `stocks(symbol, range)` | — (new; kytrade yahoo port) | requirements: yfinance |

Retired when their tool ships: `skills/discord`, `skills/linear` (their
markdown shrinks into the tool description; delete the skill dirs),
broker functions per the tables above, and the `memory: true` manifest
flag (declaring the `memory` tool is the grant — manifests migrate).

**TodoWrite → "Todo":** Claude Code's built-in tool id is fixed by the
harness, so this is presentation only: TOOL_HELP entries gain an
optional `display_name`; picker + Help show it, the frontmatter keeps
the real id.

## Block layout

```
tools/<name>/
  tool.yaml          # manifest
  run.py             # entrypoint: JSON args on stdin, JSON/text on stdout
  requirements.txt   # optional; baked into the executor image by CI
  test_run.py        # optional; run by CI's tools job
```

`tool.yaml`:

```yaml
name: memory
description: >-               # what the model sees; also Help → Tools
  Read and write your persistent memory. action="read" searches your own
  namespace (q optional); action="save" stores content (key only for
  state you overwrite in place). You can never see another agent's memory.
params:                       # JSON Schema for arguments
  type: object
  properties:
    action: {type: string, enum: [read, save]}
    q: {type: string}
    content: {type: string}
    key: {type: string}
    tags: {type: array, items: {type: string}}
  required: [action]
infra:
  secrets: []                 # [{name: linear-api-key, env: LINEAR_API_KEY}]
  database: true              # provision pg role + schema tool_<name>; subprocess gets TOOL_DB_URL
timeout_seconds: 30           # default 30, max 120
```

Registry: `toolregistry.py`, mirroring skill/report registries — parses
`tools/*/tool.yaml` from the synced checkout; validation errors on bad
schema, name collision with core tools/CLAUDE_TOOLS, unknown secret
refs. `GET /api/tools` (+ `/{name}` raw files for the editor).

## Execution model

**Persistent executor service** (`services/tool-executor/`), not per-call
k8s Jobs (pod-start latency would make chat unusable):

- FastAPI deployment; mounts the agents-checkout; `POST /run
  {tool, args, caller}` accepted from the broker only (netpol).
- Validates args against the tool's schema, then runs
  `python tools/<name>/run.py` as a subprocess with a **minimal env**:
  the tool's declared secret envs + `TOOL_DB_URL` (if database) +
  `TOOL_CALLER_AGENT`/`TOOL_RUN_ID` + args on stdin. Never the
  executor's own env. Wall-clock timeout, 256 KiB output cap, non-zero
  exit → structured error to the model.
- Secrets fetched at call time via ServiceAccount, readable only when
  labeled `agent-platform.io/tool-secret` — nothing env-baked into the
  executor pod.
- **Deps:** CI bakes the union of `tools/*/requirements.txt` into the
  executor image (and runs each tool's tests against it). New dep =
  image rebuild; new/edited tool with existing deps = live on sync.
- Netpol: executor has internet egress (the single place platform code
  calls third-party APIs for agents); ingress from broker only; pg
  access for database tools.

## Tool infra provisioning

The dispatcher's provisioner heartbeat (same loop that provisions apps)
handles tools: for `database: true`, create pg role + schema
`tool_<name>` and secret `tool-<name>-db` (env-ready keys, remint on
loss — reuse the app provisioner's machinery, generalized). Declared
secrets are validated to exist as secret blocks; binding is at call
time in the executor, not pod-env.

## Broker changes

- On startup + checkout sync, load the registry; register each custom
  tool with FastMCP. Core tools are consolidated/renamed per the table
  (broker.py shrinks).
- Custom tool call path: verify the caller's token against the platform
  API (resolve → agent, run_id), then forward `{tool, args, caller}` to
  the executor. The caller's token is **never** forwarded to custom
  tools — they face outward; platform data stays with core tools. The
  verified caller identity is how `memory` namespaces without trusting
  model args.

## Tokens

Today MCP config + per-run token exist only for memory/can_invoke/system
agents. New rule: declaring ANY platform tool (core or custom) triggers
MCP injection; the token's role is scoped to exactly the declared tools
(custom tools: execute-only; core tools: their API surface). A
credential-free agent that declares only `stocks` gets a token that can
execute `stocks` and nothing else.

## Agent manifest + capability picker

- `AVAILABLE_TOOLS` = CLAUDE_TOOLS + consolidated core + registry-derived
  customs. TOOL_HELP becomes registry-aware (descriptions from
  tool.yaml) + gains `display_name`; the lockstep test still guarantees
  no undocumented tool.
- Agent manifests referencing old tool names / `memory: true` are
  migrated in the same commit that renames (health-monitor,
  run-summarizer, news-librarian, pai).

## Memory migration (the delicate one)

- Data moves from the platform `memories` table to the `tool_memory`
  schema (one-time migration at deploy).
- Agent read/write path: only via the `memory` tool.
- Admin/UI path (Memories page, AgentDetail, Settings): the platform API
  keeps its /api/memories read/delete endpoints, now backed by a
  SELECT/DELETE grant on `tool_memory` for the api role — the web UI is
  admin surface and doesn't go through the tool.

## UI (Skills page → Skills & Tools)

Route stays `/skills`; two sections:

- **Skills** — unchanged (table + wizard + raw SKILL.md editor).
- **Tools** — table (name, description, infra chips: secret/db/deps,
  used-by), rows expand to raw editors over tool.yaml + run.py
  (+ requirements.txt), quick-edit → PR on `coder/tool-<name>` (runner
  `_target_block` learns `tools/`). Core broker tools listed too but
  read-only-annotated ("core — defined in the platform, not editable
  here").
- **New Tool wizard** — mirrors the skill wizard: name, what it does,
  args, credential?, needs storage?, notes → platform-coder authors
  tool.yaml + run.py + test as a pending change (prompt teaches the
  run.py contract).
- Help → Tools regenerates from the registry-aware TOOL_HELP.

## Security summary

| Path | Code | Args | Runs where | Env | Boundary |
|---|---|---|---|---|---|
| Custom tool | PR-reviewed | model | executor subprocess | declared secrets + tool db + caller identity | hard (no shell in agent pod) |
| Core tool | platform code | model | broker → platform API | caller's own token scope | existing |
| Self-edit | model-written | — | ephemeral clone pod | no external secrets | existing |

Agent pods keep zero shell and zero internet egress. The executor
executes only files from the synced checkout — never request-supplied
code. Caller identity is broker-verified, never model-supplied.

## Testing

- Registry parse/validation; agentspec lockstep incl. dynamic tools.
- Executor: env minimalism (canary tool dumps env; assert only declared
  keys), timeout kill, output cap, schema rejection, caller propagation.
- Broker: consolidation behavior (`runs_read` actions), token scoping
  (tools-only token 403s on /api/memories etc.).
- Memory: migration test; namespace isolation (agent A cannot read B).
- CI `tools` job: build executor image, run tools' tests. Web: fixtures
  for /api/tools, smoke + axe.

## Rollout

1. **Consolidation first** (no new services): broker tools →
   `runs_read`/`runs_write`/`metrics`/`query_app` (+ descriptions),
   agentspec/TOOL_HELP/display_name, agent manifests migrated, Help
   verified. Ships value immediately and shrinks the surface the rest
   builds on.
2. Backend tool registry + /api/tools (can ship empty) + agentspec
   dynamic layer.
3. Executor service + broker dynamic layer + caller verification +
   scoped tokens + chart/netpol/CI.
4. `stocks` tool (deps tier) → live-verify in chat.
5. `discord_chat` + `linear` (secret tier) → retire those skills +
   post_message.
6. `memory` (db tier): provisioner generalization, data migration,
   UI read-path swap, retire `memory: true` + read_memory/save_memory.
7. UI: Skills & Tools + wizard; docs (`building-blocks/tools.md`);
   design doc AS-BUILT pass.

Follow-on (separate decision): `apps/stocks` full kytrade port;
`discord_admin`.
