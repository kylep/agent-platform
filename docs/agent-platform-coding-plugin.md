# Agent Platform Coding plugin

The source package is [`plugins/agent-platform-coding/`](../plugins/agent-platform-coding/). It supplies three short skills to both Claude Code and Codex: `platform-orientation`, `platform-change`, and `platform-regression`. The first finds the current source of truth, the second guides a focused change, and the third verifies behavior across the API, runner, and browser. Assign only the skills an agent needs; the coder and QA agents can share the package without sharing their jobs or grants.

`release.json` pins every package file by SHA-256. The platform loads its skills only when the complete file set and both harness manifests verify. The runner installs only the assigned `SKILL.md` files into each harness's skill directory. It does not execute package code or copy a plugin's manifests, hooks, commands, or MCP configuration into a run. Skills add instructions, not Tools, network access, secrets, or publish permission. Those remain explicit agent and Tool grants.

To change the package, edit a skill, review it as code, update `release.json` with the new hashes, and validate both manifests and the pinned runner images before assigning it. A failed verification makes the package unavailable and blocks agents that require it rather than silently running them without their requested workflow. Roll back by restoring the previous reviewed package revision, or remove the skill assignment from an affected agent. Existing runs keep the files installed at their start; new runs use the current verified package.

Developer hosts use the repo-owned marketplace manifests in
`.agents/plugins/marketplace.json` and `.claude-plugin/marketplace.json`:

```sh
codex plugin marketplace add /Users/kp/gh/agent-platform
codex plugin add agent-platform-coding@agent-platform
claude plugin marketplace add /Users/kp/gh/agent-platform --scope user
claude plugin install agent-platform-coding@agent-platform --scope user
```

On Kyle's laptop, Codex 0.156.1 and Claude Code 2.1.282 both installed version
0.1.0 from this repo on 2026-09-25. `codex plugin list` and `claude plugin
details` showed the package enabled, Claude reported exactly three skills and
zero hooks/MCP servers, and all six cached skill hashes matched the reviewed
source. A new CLI session picks up an installation. The platform does not
silently edit a developer home directory; the one-time local installation was
made explicitly during this migration.

For an update, edit and validate the source, bump the matching package and
marketplace versions, regenerate `release.json`, then update/reinstall through
each CLI. Retain the previous reviewed commit and package version for rollback.
The platform's version and checksums remain the source of truth for agent
workloads.

This package deliberately contains no provider terms, credentials, installation scripts, or product-specific secrets. Its guidance is about this repository's current architecture and evidence needed to ship safely.
