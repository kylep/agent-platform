# Local persona sessions — implementation and local verification

Date: 2026-10-15. Starting repository revision: `31d50d6`.
Implementation authorized by the user's request to read the plan/Phase 0 report
and build it. The original planning files and unrelated working-tree files are
preserved. Implementation has been deployed for the human-driven Pai laptop memory pilot.
See the live rollout evidence below.

User contract and installation:
[Local persona sessions](../../building-blocks/local-persona-sessions.md).
Package: `clients/persona`, `ap-persona` version 0.1.0.

## Implemented decisions

- **Distinct local identity:** `local_persona_*` tables, `/api/local-persona/*`
  control plane, `execution_location=laptop`, private `home=local_persona` group
  rooms. Existing provider-account `external` semantics are unchanged.
- **R04:** one AP execution run per session writer epoch, with separate turn
  records. Background calls remain attributed to the session execution rather
  than a later foreground turn. A takeover terminalizes the previous execution.
- **R07:** ingestion and projection share a transaction. ACK means projected,
  not merely queued. Contiguous session-wide sequences, event ids and payload
  hashes provide retry/conflict handling. Durable pending fanout is retried on
  subsequent ingest/status events. Kafka carries cursor notifications and the
  normal Relay messages; router/recorder/store guards prevent dispatch/delivery.
- **R17 option (b):** existing active states are retained. Cluster capacity,
  K8s reconciliation, health, operational presence and timing queries exclude
  laptop execution. Local close/revoke terminalizes its run as `interrupted`;
  native turn completion lives on the turn record.
- **R03:** epoch-bound 15-minute bearers, renewable by per-invocation header
  helpers, with a 12-hour absolute domain lifetime. Enrollment lasts 30 days;
  draining/recovery is upload-only and bounded to one hour. An explicit recovery
  cannot reactivate domain authority or extend its absolute lifetime.
- **R16/R19:** roles use the frozen/current grant intersection on every call.
  Access snapshots include owned accounts, endpoint permissions and ordinary
  room membership, including open rooms. Benign generation refresh rewrites the
  epoch run's generation and marks loaded context stale. Access loss drains.
- **R18:** local history is readable by the initiating browser principal and
  the owning active local session. In-cluster agent credentials, other local
  sessions and reader/API keys merely named `admin` cannot read recordings.
- **Identity integrity:** memory/profile/avatar writes have server-derived
  session/run provenance. The human reviews changes before undo; undo is atomic
  and compares current values to avoid overwriting later differing writes.
  Memory keeps its established last-writer-wins key semantics.
- **Safe cutover:** old runs are backfilled as cluster execution. A readiness
  gate requires the SQL memory execution fence and the synced memory tool's
  matching protocol marker before local launch/resume. Updated memory code can
  continue ordinary cluster writes before the new provenance function exists.
- **R12/R13:** an optional separate broker container/listener on 8001, web-only
  NetworkPolicy ingress, bearer-only authentication before MCP metadata, finite
  direct REST fencing, per-call MCP grant filtering, and preserved SPIRE
  executor access. HTTPS or an explicit loopback tunnel is required by the
  helper. `localPersona.enabled` defaults to false.

## Active-state consumer inventory

Fresh searches covered both constants and literal state lists.

| Consumer | Decision |
|---|---|
| `dispatcher.handle`, global/per-agent capacity | Reject laptop request replay/cancel and exclude laptop runs from capacity. |
| `joblauncher.JobWatcher.poll_once` | Cluster-only K8s lookup; local runs never become “job disappeared.” |
| `api/health`, `api/metrics` | Cluster-only capacity, overview/streak and duration measurements; local client usage is not provider billing. |
| `relay_router` busy queries, `api/relay` presence, `scheduler` dedup | Cluster-only operational activity. Router also refuses recording-room messages before mention parsing. |
| `recorder.handle`, `_post_reply`, `reconcile_replies` | Local runs excluded; no result-topic, provider-health, connector or placeholder-reply effects. |
| `relay_store.post_relay_message` | Non-ingest recording writes refused, including service callers. Ordinary room API mutations are also fenced. |
| `api/tickets` thinking | Local runs can be linked as actual ticket work, but never mean the cluster persona is thinking. |
| `conversation` facade | Local rooms are groups with disabled dispatch; this DM-only path does not run them. |
| `api/runs` kill | Revoke session authority; no pod cancellation message. |
| `auth._current_run`, `authority.ensure_run_authority` | Local session/epoch, active run, enrollment, lifetime and live grants checked without lease-generation side effects. |
| `api/agent_self`, `api/health_incidents` active actor | Shared active states intentionally permit legitimate local persona operations. |
| `api/tasks`, `task_scheduler` | Task runs remain cluster executions; local source rooms cannot be Relay delivery targets. |
| `room_watchers` | Account-owned watcher runs only. Local room mutation/registration is refused and local imports never route. |
| `relay_feed` terminal classification | Shared states remain truthful; local projection does not emit cluster run events. |
| `apikeys` terminal sweep | Local authority uses separate credentials; ordinary cluster key cleanup unchanged. |
| `prsummarizer` | Fixed code-owned worker cannot be enrolled as a local persona. |
| `pruning` | Local tool-event payloads share transcript retention; receipt hashes/cursors remain for replay protection. |

## Verification

| Check | Result |
|---|---|
| Complete backend suite, Python 3.12.13, four workers | **3,098 passed, 3 existing skips.** The CI/verifier suite mapping was updated for the new helper suites and the full suite rerun. |
| Broker suite, including real FastMCP ingress/discovery/call runtime | **435 passed.** |
| Management facade / Discord connector | **37 / 8 passed.** Persona credential discovery is explicitly refused even if upstream whoami would accept it. |
| Executor suite | **91 passed.** Socket tests ran with loopback binding available. |
| Local helper unit/normalization/fencing tests | **18 passed.** |
| Fresh PostgreSQL 16 contract | **Passed.** Additive migration, actual provisioned memory subprocess, provenance, membership/grant removal at execution time, close denial, human undo. |
| Complete web smoke/axe/interaction suite | **374 passed.** |
| Final rebuilt web session UI, smoke and axe subset | **102 passed.** Includes read-only recordings and session activity. |
| Web build, Storybook, lint and design-token check | **Passed.** Existing unrelated lint/build warnings remain. |
| Helm lint, flag off and flag on with SPIRE | **Passed.** |
| SDK regeneration and drift check | **Passed; no generated SDK changes.** New control routes are excluded from OpenAPI/facade generation. |
| Repository secret hooks | **Passed:** forbidden filenames, private keys and gitleaks, on exactly the intended files. |
| CI-pinned Semgrep 1.170.0 | No new findings. Explicit changed-source comparison scanned 54 files with zero scan errors; the same 17 findings existed across the 35 tracked baseline files. These are 15 existing SQL-text audit findings and two existing XML-parser test findings. The ordinary full-service scan also has existing SQL audit findings; this report does not claim a clean repository-wide security scan. |
| Trivy visibility scan | Not run locally: Trivy is not installed. The existing CI step is report-only. |
| Standalone wheel and isolated installation | **Built and installed in `/tmp`; isolated hook entry point/CLI help passed.** No global installation performed. |

Native smoke evidence used a disposable loopback AP, real API/auth/broker/
executor code, provisioned PostgreSQL memory and two synthetic personas in the
same working directory. No production AP state was used:

- **Claude Code 2.1.300 / Haiku:** exit 0, native binding established, correct
  persona memory saved, nine normalized records after final flush, including
  user/assistant/tool/turn-start/turn-end. No adapter gaps.
- **Codex 0.162.1 / gpt-6-luna:** exit 0, native binding established, correct
  persona memory saved, ten normalized records after final flush with the same
  event families. No adapter gaps.
- The generated Codex configuration was inspected through its effective MCP
  listing: ambient AP, code-review, app, CUA, node and Playwright servers were
  disabled; only the persona server remained enabled.
- Both native credential-fence canaries passed. Claude's final test used the
  actual generated directory Read/Edit rules without probe-only deny rules.
  Codex's deterministic sandbox denied the fake credential and protected files.

Native testing caught and fixed two Codex configuration hazards: quoting names
in dotted `-c` keys created literal quoted server names, and plugin-provided MCP
servers needed complete inert transports to be disabled without creating an
invalid config. It also established that final source re-reading after process
exit is necessary. Private Claude request/attachment/cost records are explicitly
omitted rather than reported as unknown transcript content.

Final bounded-input/normalization checks also passed with all local-session API tests (**32 focused checks**).

## Pilot limits and remaining work

No production images were deployed, feature flag enabled, real persona enrolled,
or messages sent to external people/accounts. Follow the rollout order in the
user contract and perform a human-authorized actual-persona pilot over TLS or an
authenticated tunnel before treating production behavior as verified.

Raw child transcripts, binary attachment uploads and reasoning are outside v1
fidelity. Child sources produce a visible gap; attachment metadata is kept.
Claude TUI-only interrupt/reconnect behavior and human-approved Codex escalations
remain the Phase 0 limitations; this implementation does not turn them into
stronger guarantees. Unsupported native resumes are detected as recovered gaps
when new records are found at the next launcher resume. Same-UID determined
bypasses remain the explicitly accepted reasonable-tamper-resistance limit.


## Live rollout — 2026-10-15

Implementation `f909f51` was pushed to main. Built/imported amd64 backend,
broker, web and management-facade images; deployed the backend migration and
restarted dispatcher/recorder, broker, web and facade. Enabled
`localPersona.enabled` using the current Helm values. Kubernetes validation
caught missing names on the broker's now-multiple Service ports; named the
local, mTLS and plain HTTP ports and reapplied successfully.

The installed `ap-persona` 0.1.0 pipx package and Codex 0.162.1 preflight passed
from `~/pai-laptop-test`, including the actual sandbox fence and effective MCP
allowlist. An authenticated localhost SSH forward on port 18090 protects the
laptop transport. Pai's enrollment is stored privately in the launcher state,
with the repository credential paths included in the fence.

A live Pai session successfully loaded canonical identity, assigned skills and
20 seeded memories. Authenticated MCP discovery exposed 27 granted tools and
an actual memory search succeeded through nginx, the separate persona broker,
SPIRE executor tunnel and the provisioned memory tool. The check performed no
memory writes and closed/finalized its session. A second installed-client
preflight session was also finalized. All deployments reported ready. Helm revision 101 completed with status
`deployed`; its topic hook created `local-persona.events`.

The human memory test is deliberately still unperformed: launch Pai locally,
explicitly ask her to save a unique fact in AP memory, then ask Pai in her
ordinary platform DM to retrieve it. Read-only setup checks did not pre-seed
that fact or send any persona messages.
