# Local persona sessions

Run an explicitly selected AP persona in native Claude Code or Codex on your
laptop. Its AP identity, private memory namespace and grant ceiling come from
AP. Its conversation is separate from ordinary Relay DMs. AP records
**device-reported** user/assistant messages and tool activity; it does not attest
who typed a message or whether a laptop reported every event.

Ordinary harness launches receive no persona hooks or configuration from this
package. Local mode uses an invocation-only credential fence, including a cut to
ambient MCP access. The native harness still uses your own provider login and
normal permission system. A human-approved Codex sandbox escalation can bypass
the fence. Resume persona work through the launcher; a plain native resume is
unsupported and can retain a persona prompt while dropping recording hooks.

## Install and enroll

Requires Python 3.11+, macOS sandbox support, Claude Code 2.1.299/2.1.300 or
Codex 0.162.1. Other versions fail closed until their compatibility is checked.
The provider must already work in your ordinary local harness.

Install the reviewed package, for example with `pipx install ./clients/persona`.
Installation does not edit global hooks, managed policies, user harness config
or repository settings.

An administrator signs into AP, opens **Settings → Local persona sessions**,
selects an enabled persona and creates a device enrollment. The token is shown
once. Keep it outside model context and paste it at the private terminal prompt:

```sh
ap-persona enroll --persona pai --origin https://ap.example
ap-persona launch --persona pai --harness claude --cwd /path/to/work
ap-persona launch --persona pai --harness codex --cwd /path/to/work
```

The origin must use HTTPS, or HTTP on loopback through an authenticated tunnel.
For the reference LAN installation, a user-managed SSH forward to the web
service is an alternative to TLS; point both enrollment and launch at its
`http://127.0.0.1:<port>` origin. Do not send persona credentials over LAN HTTP.
Enrollment creation itself should use the same secure browser transport.

The default fence covers user/project harness config/auth, persona state, SSH/AWS credentials,
shell startup files, the helper interpreter environment and known `exports.sh` /
`.env` files in the working-directory and helper-checkout ancestry. Enrollment
remembers these paths for later launches in other projects. Add any custom AP
credential locations with `--credential-path /absolute/path` at enrollment or
launch. Both logical paths and symlink targets are fenced.

Enrollment secrets live in owner-only files under
`~/.local/state/ap-persona/enrollments`. Invocations, context and the SQLite
spool live under the same protected state root. Files and parents may not be
symlinks. AP stores credential hashes, never the raw enrollment, renewal or
session bearer. No token is a command-line argument. This is a bearer protocol,
not device attestation or a Keychain presence gate; the invocation sandbox is
the protection against another persona's credential files.

Each launch runs a credential-fence self-test. Codex uses its deterministic
sandbox command. Claude uses one small fixed Haiku request that must demonstrate
shell and native Read/Edit denials against a disposable canary. A failed test
refuses launch. Persona mode never falls back to an ordinary session.

## Work, resume and recover

The launcher prints the AP session id. Continue that exact conversation with:

```sh
ap-persona resume --persona pai --harness codex <session-id>
ap-persona status <session-id>
ap-persona sync <session-id>
ap-persona sync --recover <session-id>
```

A resume is an explicit writer takeover. It replaces the renewal secret and
increments the server and local writer epoch. Old hooks, MCP credentials,
executor credentials and local spool writers cannot become the new writer.
Pending history survives takeover. Identity, harness, enrollment and native
conversation must match; resuming cannot adopt another persona's transcript.

A native `/clear` or fork changes the native conversation id. The integration
suspends AP domain authority instead of silently adopting the new history. Exit
that native process and use the launcher to resume the original conversation or
launch a new AP session. Native SessionEnd is only a hint; the launcher drains on
actual process exit. No daemon or heartbeat is installed.

The current lifetimes are 30 days for enrollment, 12 hours for domain sessions,
15 minutes for access bearers and one hour for upload draining. MCP header
helpers renew within the current writer epoch. Renewal cannot extend the
absolute session lifetime or reactivate a drained session. Explicit historical
recovery can upload pending records after that lifetime while the enrollment is
valid; it does not restore domain authority. Human revocation stops recovery too.

Initial launch requires AP. Established offline work keeps its fixed identity
and cached context, and stores history locally. Failed AP memory writes remain
failed writes; they are never queued and replayed later as memory mutations.
The spool is retained when a hook times out, a process crashes, or a drain TTL
expires. Status reports local pending records even if remote status is unavailable.

## Memory and AP authority

Startup includes the canonical prompt, verified assigned skill text, up to 20
own-namespace memories and explicit retrieval instructions. Identity,
preferences and continuity key prefixes receive priority, followed by recent
memory. The memory text budget is 12,000 characters and the full launch context
is capped at 200KB. These are retrieval/launch limits, not limits on the persona's
own memory tool. Memory key updates retain the existing last-writer-wins
semantics and are never replayed from an offline transcript spool.

Own DMs, ordinary Relay sends, owned-account messaging, Apps and other tools
continue through their existing grants and resource ACLs. The effective tool
set is the intersection of the launch snapshot and current grants. Removal is
immediate; a new grant does not widen an existing session. API roles follow
that effective set. A central finite method/path policy bounds direct REST as
well as MCP calls. The management facade refuses persona credential types.

Account ownership, endpoint permissions and room membership are checked against
the session's access snapshot on each protected call. A benign generation change
requires a hook/bootstrap refresh and marks loaded context stale. Lost access or
persona disable drains domain authority while allowing already captured history
to upload. Profile edits affect loaded canonical context on the next explicit
resume, rather than pretending a running model's prompt was replaced.

One AP run represents a session writer epoch; turns are separate records. AP
calls from foreground/background work are attributed to that session execution,
not falsely assigned to a later foreground turn. `local_session_history` reads
only the currently authenticated session's recorded events. It is an edge-only
recording capability, not a platform grant or a cross-session history tool.

## Recording and privacy

The private `local_persona` group is a recording, with the initiating human as
its only ordinary Relay participant. In-cluster personas, other local sessions,
reader keys and API keys merely named `admin` cannot read it. Its own local
session can use the scoped history tool while domain authority is active.
Posting or changing the recording room through ordinary APIs is refused.

Normalized events and message projections commit in one transaction. Acknowledged
history is durable; lost acknowledgments can be retried. Conflicting duplicates
fail. Kafka and the local Relay feed notify viewers after commit; failed fanout
remains retryable at a later ingest/status event. Imported mentions never summon
an agent, send a connector reply or publish an agent's result topic. Explicit
live tool sends retain their usual effects.

The adapters keep user text, assistant commentary/final messages, tool calls and
results, lifecycle boundaries and attachment metadata. They omit reasoning,
system prompt snapshots, hook attachments, request/auth snapshots, binary data
and environment/world-state records. Claude records use `uuid`; Codex records
use `ordinal`. Unknown content creates a visible gap. Child transcript files are
not independently uploaded in v1: the parent summary is retained and the source
gap is explicit. Files are never opened merely because a tool result names them.
Large records get a bounded preview/gap; records above 1MiB are omitted with a gap.
There is no claim of complete child, binary, or private reasoning capture.

Local tool-event payloads follow the persona's transcript retention setting.
Receipt hashes and cursors remain for replay protection. Projected messages keep
ordinary Relay retention. Identity mutation provenance remains available for undo.

In **Local persona sessions**, inspect recorded activity, revoke AP authority,
or review memory/profile writes and revert them after closing/revoking the
session. Undo is atomic and refuses to overwrite later outside changes. Revoking
AP authority does not kill the laptop process.

Uninstall with your package manager. The state directory is intentionally
retained for history recovery; remove it separately when no longer wanted.
Uninstall never deletes or rewrites ordinary harness configuration.

## Rollout and rollback

The Helm feature flag `localPersona.enabled` defaults to `false`. It enables a
separate broker container/listener on port 8001, accessible only from the web
pod at `/persona/mcp`; the existing SPIRE broker/executor path is preserved.
The API, broker, web bundle and synced memory tool all need their matching release.

Follow [deployment](../deployment.md). Deploy/migrate the backend first, then
its dispatcher/provisioner, broker and web. The additive migration backfills old
runs as `cluster`. Launch/resume refuse until both the SQL memory execution fence
and the synced tool's protocol marker are present. The updated memory tool can
still save ordinary cluster memory against an older backend during capability
sync, before the provenance function exists. Enable the flag only once those
checks are ready, over TLS or an authenticated tunnel, and pilot both harnesses
with synthetic personas before enrolling an actual persona.

Rollback disables new local sessions/ingress and revokes local enrollments or
sessions before reverting binaries. Preserve uploaded history and local spools.
Do not delete user harness configuration. No production rollout or real-persona
enrollment is performed by the local verification suite.
