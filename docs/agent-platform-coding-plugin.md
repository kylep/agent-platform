# Agent Platform Coding plugin

The source package is [`plugins/agent-platform-coding/`](../plugins/agent-platform-coding/). It supplies four skills to both Claude Code and Codex. Three are short coding workflows: `platform-orientation` finds the current source of truth, `platform-change` guides a focused change, and `platform-regression` verifies behavior across the API, runner, and browser. The fourth, `app-building` (since 0.2.0), teaches App builders such as Pai, Kai and Olu when an App is the answer and how to build, publish and maintain one with the `apps` and `app_data` tools ([Apps](building-blocks/apps.md)). Assign only the skills an agent needs; the coder, QA and builder agents can share the package without sharing their jobs or grants.

`release.json` pins every package file by SHA-256. The platform loads its skills only when the complete file set and both harness manifests verify. The runner installs only the assigned `SKILL.md` files into each harness's skill directory. It does not execute package code or copy a plugin's manifests, hooks, commands, or MCP configuration into a run. Skills add instructions, not Tools, network access, secrets, or publish permission. Those remain explicit agent and Tool grants.

On a change to this package or its verifier, the dedicated
[`Coding plugin provenance`](../.github/workflows/plugin-release.yaml) workflow
checks the approved manifest, builds a deterministic tarball, and asks GitHub
Actions to attest its digest and source workflow. Download the named artifact
from that workflow run, then verify it before using the bundle as a release:

```sh
gh run download RUN_ID -n agent-platform-coding-VERSION -D /tmp/ap-plugin-release
gh attestation verify /tmp/ap-plugin-release/agent-platform-coding-VERSION.tar.gz \
  -R kylep/agent-platform \
  --signer-workflow kylep/agent-platform/.github/workflows/plugin-release.yaml \
  --source-ref refs/heads/main
```

The attestation establishes CI provenance for the retained workflow artifact.
The verified artifact's SHA-256 is pinned in `plugin_release.py`. Skill catalog
admission reconstructs the deterministic bundle from the runtime checkout and
requires that exact digest, in addition to the per-file and release-manifest
checks. The launcher freezes the approved release digest into a Job; the
runner checks that digest and the assigned skill bytes before installing them.
This binds active runtime skill registration to the attested release without
making the running pod fetch GitHub. The previous 0.1.0 release is no longer
admitted by the current platform build; roll back the platform image and
package together if that older release is needed.

To change the package, edit a skill, review it as code, update `release.json` with the new hashes, and validate both manifests and the pinned runner images before assigning it. A release takes two pins in `plugin_release.py`, in two pull requests:

1. **The reviewed manifest.** Bump the version in the three package manifests and `.claude-plugin/marketplace.json`, regenerate `release.json`, and add its SHA-256 to `APPROVED_RELEASES`. That entry is an approval of exact skill text, so Kyle adds or confirms it. The workflow verifies everything except the attested bundle (`verify_release(root, attested=False)`), builds the bundle and attests it when this merges to `main`.
2. **The attested bundle.** Download the run's artifact, verify it as above, and pin its SHA-256 in `ATTESTED_BUNDLES`, naming the run. Until this lands, the platform refuses the whole package, so agents assigned any of its skills are blocked: deploy only after the second pull request merges. A failed verification makes the package unavailable and blocks agents that require it rather than silently running them without their requested workflow. Roll back by restoring the previous reviewed package revision, or remove the skill assignment from an affected agent. Existing runs keep the files installed at their start; new runs use the current verified package.

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

Version 0.1.1 adds the server-side Live App action policy reminder. Its
attested bundle came from CI run `36195106940`; the signature check, source
workflow and `refs/heads/main` verification passed. The live skill catalog
accepted all three skills after the API and dispatcher rollout, and 78 focused
skill/runner tests passed. Both laptop CLIs updated from 0.1.0 to 0.1.1. A
rollback rehearsal installed the previous reviewed package from an isolated
local marketplace in each CLI, matched its cached skill hash to the source,
then restored 0.1.1 and removed the temporary marketplace. Claude's final
inventory remained three skills, zero hooks and zero MCP servers. This tests
installation and rollback mechanics, not the quality of agent decisions when
the skills fire.

Version 0.2.0 adds `app-building` and makes the provenance workflow take the
version from `release.json`. Its manifest approval and attestation are
pending; this page records the run once it is pinned.

Version 0.3.0 updates `app-building` for proposals, sharing, page templates,
App tools and tool views. Its reviewed manifest and provenance bundle are
versioned independently of 0.2.0, so older installations can be rolled back.

For an update, edit and validate the source, bump the matching package and
marketplace versions, regenerate `release.json`, then update/reinstall through
each CLI. Retain the previous reviewed commit and package version for rollback.
The platform's version and checksums remain the source of truth for agent
workloads.

This package deliberately contains no provider terms, credentials, installation scripts, or product-specific secrets. Its guidance is about this repository's current architecture and evidence needed to ship safely.
