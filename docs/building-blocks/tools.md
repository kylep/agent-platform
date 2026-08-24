# Tools

**What:** the executable capability block
(`docs/design/12-executable-capabilities.md`). A tool is reviewed code that the
**mcp-broker** offers to agents as an `mcp__platform__*` tool over MCP — the
protocol Claude Code uses to call tools hosted outside its own process — and
that the **tool-executor** runs in a locked-down subprocess. Both are platform
services; see the [Glossary](glossary.md).

[Skills](skills.md) carry *knowledge*; tools carry *execution* — an agent picks
arguments, never code, which is why agents can trigger real work without ever
holding a shell or a credential. `stocks`, `discord_chat`, `linear`, `memory`,
`prices` and `index_movers` are the shipped custom-tool references.

Two tools are **core** — built into the broker rather than living under
`tools/` — because they write to the platform's own definitions table and
must be attributed to the calling agent, not to a shared executor key:
`agents_edit` and `agents_grant`, the RBAC split behind
[agent definitions](agents.md) (`docs/design/15-db-first-agents.md`). They are
grantable exactly like any other platform tool (`PLATFORM_MCP_AGENT_TOOLS`,
folded into `GRANTABLE_PLATFORM_TOOLS` alongside the older core reads below),
but holding one does **not** promote an agent to the `annotator` rung of the
role ladder — only membership in `PLATFORM_MCP_TOOLS` (the platform-API-facing
core tools) does that. An `agents_edit`/`agents_grant` holder sits on the same
`tools` rung a custom-tool-only agent does: it earns a per-run token scoped to
exactly those two tools and nothing else on `/api/*`.

Two of those show patterns worth copying. `prices` binds an **app's** DB secret
(`infra.secrets: [app-stockmarket-db]`) and writes rows itself, returning only
counts — a five-year backfill is ~3,800 rows, which is nothing for Postgres and
ruinous for a model's context. `index_movers` keeps arithmetic out of the
model: it computes each index holding's contribution in basis points and hands
back a ranking, because a model asked to multiply ten weights by ten returns
produces confident wrong numbers, and those numbers are the whole claim.

**Lives in:** `tools/<name>/` in the synced checkout — the platform's live
clone of this repository — and edits go through the standard
[change loop](changes.md) (wizard or raw editor → pull request on
`coder/tool-<name>`):

```
tools/<name>/
  tool.yaml          # manifest (below)
  run.py             # entrypoint: JSON args on stdin, result on stdout
  requirements.txt   # optional pip deps — baked into the executor image by CI
  test_run.py        # optional; CI's tools job runs it
```

## The manifest (`tool.yaml`)

```yaml
name: stocks              # snake_case; must match the directory
description: >-           # what the MODEL sees — write it as usage guidance
  Look up a stock by ticker via Yahoo Finance…
params:                   # JSON Schema (type: object) for the arguments;
  type: object            # the executor validates every call against it
  properties:
    symbol: {type: string}
  required: [symbol]
infra:
  secrets: [linear-api-key]   # secret BLOCKS, bound by name like skills do
  database: true              # provisioned pg role + schema tool_<name>
timeout_seconds: 45       # wall clock; 1–120
```

## How a call flows

1. An agent that declares the tool (a checkbox on its page, or a grant on its
   `platform_tools`/`harness_tools` list — see [Agents](agents.md)) calls it
   via the mcp-broker. Declaring any platform tool makes
   the run *identity-bearing*: what identity depends on what was declared.
   Core tools (which act on the platform) earn a per-run token with the
   `annotator` role; declaring only custom tools earns the `tools` role, which
   can do nothing but identify itself — a credential-free agent declaring only
   `stocks` gains zero platform-API surface.
2. The broker verifies the caller's token against the API (`/api/whoami`) and
   checks that the agent's definition really does declare the tool. The
   caller's token is **never forwarded outward** — only the verified identity
   travels onward. [security.md](security.md) walks the whole path.
3. The executor validates the args against `params`, then runs `run.py` with
   a **minimal env**: the declared secrets' keys (fetched from k8s at call
   time — never baked into any pod), `TOOL_DB_URL` when `database: true`,
   and `TOOL_CALLER_AGENT` / `TOOL_RUN_ID`. Timeout enforced, output capped
   at 256 KiB, non-zero exit → structured error the model can read.

## Declarative provisioning

The dispatcher runs a reconciliation heartbeat that converges whatever `infra`
declares: `database: true` gets a Postgres role plus a schema named
`tool_<name>` and a k8s Secret `tool-<name>-db` holding its connection string
(re-keyed if that secret is ever lost). Declared secrets must exist as
[secret blocks](secrets.md). The convergence is idempotent and never tears
anything down.

## Rules of the game

- The executor is the platform's single third-party-egress point; agent pods
  keep zero shell and zero internet egress.
- `run.py` must never assume `os.environ` carries anything undeclared.
- Exit non-zero with a stderr message to signal failure honestly — the model
  reads it verbatim.
- New pip dependency ⇒ the executor image must be rebuilt (its dependencies
  are baked in); `tool.yaml` / `run.py` edits are live on the next sync of the
  checkout, with no deploy.
- A tool name cannot shadow one of the broker's built-in core tools —
  `runs_read`, `runs_write`, `metrics`, `query_app`, `agents_edit`,
  `agents_grant` — a `tools/agents_edit/` directory is refused at the
  registry, loudly, rather than silently losing to (or fighting) the broker's
  own tool of the same name. A tool folder without a `run.py` is likewise
  surfaced as an error rather than becoming a silently dead capability.
