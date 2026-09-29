# Agent self-management

The `agent_self` Platform Tool lets an agent maintain its own identity without
permission to edit other agents or its security grants. It is granted by default
to existing enabled, valid definitions and to new agents. Remove its checkbox in
Grants to opt out; `AP_SELF_DEFAULT_GRANT=false` disables the platform default.
User-agent opt-outs remain removed across restarts; system-agent grants are
maintained by their code registry. Existing runs keep their frozen
Tools, so a newly added grant becomes available on their next run.

| Action | Behavior |
| --- | --- |
| `get` | Read your own prompt, description, model, runtime, avatar and version. |
| `models` | Discover the known Claude and Codex model choices. |
| `avatar` | Assign a readable image artifact to yourself; null restores the emoji. |
| `model` | Set your next-run model, optionally switching between Claude and Codex. Empty model uses the platform default. |
| `persona` | Replace your own complete prompt and/or one-line description. |

For `model` and `persona`, first read `get`, then pass its `expected_version`.
A conflict means another edit landed; reread before applying your change. A
runtime switch requires an explicit model or an empty model for the new runtime's
default. Updating your persona means replacing your instructions, not changing
your Persona/Worker type, name or execution profile. Use memory for experiences
and evolving facts; change your prompt when your enduring identity needs revision.

Model and persona changes apply to future runs. The run making the change may
finish with its existing instructions and grants, but cannot persist its old
Claude/Codex session into the new context. Other concurrent runs and previous
resume sessions lose authority. History and memories remain intact. Each profile
edit creates an attributed, rollback-capable agent version; avatar changes use
the existing artifact event feed and appear everywhere the agent's face is shown.

System agents can choose avatars and operational models. Their code-owned
prompts and descriptions remain protected. No self action changes grants, secrets,
account ownership, quotas, scheduling, type, name or permissions. Avatar assignment
does not grant Image Gen: use the separate generator grant to create a new image,
then supply its artifact ID. This avatar belongs to the platform/Relay; it does
not change a Discord bot's provider profile.

Backup-model selection and automatic error fallback are a separate follow-up.
They are not implemented here; that design must distinguish retryable provider
failures, resume compatibility and duplicate external effects, and tell the
fallback model what happened without discarding the agent's identity.


## Verification — 2026-09-29

Focused API/session tests cover self-only targeting, forbidden grant fields,
optimistic conflicts, system prompt protection, private-artifact rejection,
version attribution, old-run revocation, fresh sessions, and default opt-out
persistence. Broker and browser checks cover action-specific request bodies
and creation-wizard opt-out. The live disposable Codex run
`37f67d6331df4efdb76599bdf783e3fe` assigned an avatar, changed its prompt/description,
changed its next-run model and finished with `SELF-MANAGEMENT PASS`. It held only
`agent_self`, with no broad definition editor grant or external connectors.
The disposable agent and artifact were removed afterward. Kai's grant and the
creation default were verified in the deployed UI at desktop and mobile widths.

An initial live attempt exposed the Postgres `changed_via` width limit that
SQLite did not enforce. The fix uses the bounded `tool:agent_self` source label
and the full authenticated run ID in `changed_by`; tests check both database
column bounds. The failed update rolled back without altering the persona.
