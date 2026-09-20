# 26 — Claude and Codex runtimes

An agent definition has a `runtime` field: `claude` (the default for existing
rows) or `codex`. The dispatcher and Kubernetes launcher treat both as peers;
triggers, concurrency, Workbench preparation and publishing, Kafka transcripts,
the MCP broker, and run history remain platform concerns.

The runner invokes Claude with `--output-format stream-json` and Codex with
`codex exec --json`. Raw provider events are retained. For Codex, the runner
also emits the platform's existing terminal `result` frame from the last
`agent_message`, so recording, Relay replies, result topics, and reporting keep
one provider-independent contract.

## Authentication

`codex-credentials` contains the complete native `~/.codex/auth.json` created
by `codex login`. Codex runners point a custom Responses provider at the
in-cluster Codex broker
and send a harmless placeholder bearer. The broker alone mounts `auth.json`,
replaces authentication headers, refreshes OAuth under a single-flight lock,
and persists rotated tokens through an authenticated internal API route. The
runner therefore needs no nested filesystem sandbox or node-local AppArmor
profile; Kubernetes remains the portable execution boundary.

The model does not receive OAuth tokens in its environment, filesystem, or
request headers. Brokered deployments disable the legacy run-scoped credential
download and upload routes, and provider credentials are reserved from normal
agent secret bindings. Codex runner pods never mount either provider credential.

Add the credential in Settings → Secrets by copying the contents of
`~/.codex/auth.json` from a machine where `codex login` completed. A Codex agent
is blocked before dispatch while that secret is absent. Credential health is
tracked separately for each runtime.

## Sessions and skills

Relay sessions store Claude session blobs and Codex thread IDs independently.
A failed resume falls back once to the platform's flattened conversation
prompt. The same declared skills are copied to `~/.claude/skills` for Claude
and `~/.agents/skills` for Codex.

The runner images pin both CLIs. The agent editor selects the runtime and offers
that runtime's supported models in a dropdown. The catalog follows the
subscription-backed CLI model names; an existing custom value remains selectable
when editing an older agent.
