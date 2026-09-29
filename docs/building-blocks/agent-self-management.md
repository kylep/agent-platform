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
| `get` | Read your own prompt, description, primary and backup model/runtime, avatar and version. |
| `models` | Discover the known Claude and Codex model choices. |
| `avatar` | Assign a readable image artifact to yourself; null restores the emoji. |
| `model` | Set your next-run model, optionally switching between Claude and Codex. Empty model uses the platform default. |
| `backup` | Set only your backup model and runtime; an empty model clears it. |
| `persona` | Replace your own complete prompt and/or one-line description. |

For `model`, `backup` and `persona`, first read `get`, then pass its `expected_version`.
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

## Backup models

Choose an explicit **Backup runtime** and **Backup model** in the agent editor,
or use `agent_self(action="backup", runtime="claude", model="claude-sonnet-5-5",
expected_version=...)`. `get` shows both selections. The server rejects a self
request that includes both primary and backup fields, even when the agent holds
broader definition-editor access. Update one, reread the version, then update
the other in a separate call. Humans may configure both together.

A provider capacity, rate-limit/quota, authentication, unavailable model or
server/connection failure can trigger **one** backup attempt, only before any
answer or Tool work begins. A known-invalid primary credential can select the
backup before dispatch. Failed tools, permission denials, cancellations, hard
time limits, incomplete output and failures after work begins are not retried.
The original run ID, persona, grants and conversation context stay the same.
The backup receives a platform instruction explaining the switch and uses a
fresh session with conversation replay. The prior primary resume cache is
cleared, so the next primary turn replays history including the backup answer;
the backup does not persist a provider-incompatible session. Every new run tries the configured primary again.

Both subscriptions use their existing credential proxies. No OAuth credential
is added to the runner. A backup needs its proxy configured; quota ceilings
remain the agent's own. Fallback does not bypass permissions or promise that an
exhausted second subscription can answer. The run detail shows the actual
model/runtime, fallback reason and transcript notice. Primary failure frames
retain their usage without posting a premature failure into Relay.

Model identifiers verified September 29, 2026:
[`gpt-6-sol`](https://developers.openai.com/api/docs/models/gpt-6-sol) and
[`claude-sonnet-5-5`](https://www.anthropic.com/claude-sonnet-5-5).
Runner images pin Claude Code 2.1.284 and Codex 0.159.0.


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

### Backup verification — 2026-09-29

Both live cross-provider checks returned `FALLBACK PASS` after an intentionally
unavailable primary model: `4734ebc8f78a44ffa9d490e19b1910e7` switched Codex →
Claude Sonnet 5.5, and `937fc9f9b20f4d39b281d05479a70ca2` switched Claude →
GPT-6 Sol. Their frozen primary definitions remained intact, the actual backup
runtime/model appeared in run detail, and the switch was recorded in the
transcript. No external connector or Tool action was granted to the test agents.

The first attempt exposed two actual CLI error shapes absent from the earlier
fixtures: Codex completes an `error` item for missing model metadata; Claude
emits an assistant frame flagged `is_api_error_message`. Those are failure
notifications rather than agent work. Parser regressions now cover both while
retaining the block on real tool calls and answers. Local API, dispatcher,
recorder, session, quota, broker and browser checks cover single-use fallback,
private run ownership, frozen configuration, primary-session isolation, correct
usage attribution, separate model edits and the backup picker. Pai and Kai are
configured with Sonnet 5.5 backups and retain their original primary models.
