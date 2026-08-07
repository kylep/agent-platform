# Tools

**What:** the executable capability block (docs/design/12). A tool is
PR-reviewed code the MCP broker serves to agents as an `mcp__platform__*`
tool and the tool-executor runs in a locked-down subprocess. Skills carry
*knowledge*; tools carry *execution* — an agent picks arguments, never code,
which is why agents can trigger real work without ever holding a shell.
`stocks`, `discord_chat`, `linear`, and `memory` are the shipped references.

**Lives in:** `tools/<name>/` in the synced checkout, behind the standard
change loop (wizard or raw editor → PR on `coder/tool-<name>`):

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

1. An agent that declares the tool (a checkbox on its page / `tools:` in
   agent.md) calls it via the MCP broker. Declaring any platform tool makes
   the run token-bearing: core tools earn an annotator per-run token, custom
   tools alone earn the whoami-only `tools` role — a credential-free agent
   declaring only `stocks` gains zero platform-API surface.
2. The broker verifies the caller's token via `/api/whoami` and checks the
   agent's definition actually declares the tool. The token is **never
   forwarded outward** — only the verified identity.
3. The executor validates the args against `params`, then runs `run.py` with
   a **minimal env**: the declared secrets' keys (fetched from k8s at call
   time — never baked into any pod), `TOOL_DB_URL` when `database: true`,
   and `TOOL_CALLER_AGENT` / `TOOL_RUN_ID`. Timeout enforced, output capped
   at 256 KiB, non-zero exit → structured error the model can read.

## Declarative provisioning

The dispatcher's heartbeat converges `infra`: `database: true` gets a pg
role + schema `tool_<name>` and secret `tool-<name>-db` (re-keyed if the
secret is lost). Declared secrets must exist as secret blocks. Idempotent;
never tears down.

## Rules of the game

- The executor is the platform's single third-party-egress point; agent pods
  keep zero shell and zero internet egress.
- `run.py` must never assume `os.environ` carries anything undeclared.
- Exit non-zero with a stderr message to signal failure honestly — the model
  reads it verbatim.
- New pip dependency ⇒ executor image rebuild; tool.yaml/run.py edits are
  live on the next checkout sync.
- A tool name cannot shadow a core broker tool (`runs_read`, `runs_write`,
  `metrics`, `query_app`), and a tool without `run.py` is a surfaced error,
  not a silently dead capability.
