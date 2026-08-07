# Agents

**What:** the unit of work — a Claude Code agent the platform can run in a pod.

**Lives in:** git, one folder per agent:

```
agents/<name>/
  agent.md          # portable Claude Code definition (runnable with bare `claude --agent`)
  manifest.yaml     # the platform layer
  entrypoints.yaml  # durable triggers (optional — see entrypoints.md)
```

**manifest.yaml shape** (all optional):

```yaml
description: One line for listings.
role: operator          # operator | coder (coder gets the github-app for PRs)
skills: [git, discord]  # mounted into the pod; their secrets get bound
secrets: [my-secret]    # extra direct secret bindings
model: sonnet           # claude model override; empty = CLI default
concurrency: 1
timeout_seconds: 1800
system: true            # platform-internal; protected from UI deletion
# (memory: true is retired — declare the `memory` tool instead; design/12)
can_invoke: true        # may trigger other agents (depth-guarded)
```

**Readiness (derived, never declared):** an agent's secret dependencies are
computed from `manifest.secrets` plus each of its skills' declared secrets. An
unmet *required* dependency makes the agent **blocked** — runs are rejected
before a pod launches, with the exact reason recorded as a failed Run.
*Blocked* (fix the secret) is distinct from *quarantined* (broken
manifest/entrypoints — fix the agent).

**How to add one:** create the folder and open a PR — or use the New Agent
wizard in the UI, which has a coding agent do exactly that.
