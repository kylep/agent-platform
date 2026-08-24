# Security model

How the agent-platform — a self-hosted platform that runs Claude Code agents
as Kubernetes Jobs — contains untrusted model behavior, what protects each
credential, and how workload identity works
(`docs/design/13-workload-identity.md`). This is the engineering version; the
plain-language one that ships in the product's Help is
`docs/building-blocks/security.md`, and component names used here (runner,
dispatcher, mcp-broker, tool-executor, claude-proxy, the platform agents) are
defined in `docs/building-blocks/glossary.md`.

**[LIVE]** marks what is deployed. As of 2026-08-07 every layer below is live
on the reference deployment (a single-node k3s cluster, Helm release `ap`).

## Threat model

The primary adversary is **the agent itself**: a model processing
untrusted input (web pages, news items, chat) that can be steered by
prompt injection. Secondary: a compromised pod (supply chain, runtime
bug). We assume the Kubernetes cluster and its operator (Kyle, the project
owner and sole administrator) are trusted; the web UI sits behind an
authenticated admin session.

The core discipline is breaking the **lethal trifecta** — no single
agent context should combine (a) untrusted input, (b) credentials worth
stealing, and (c) an exfiltration channel.

## Containment today [LIVE]

- **No shell, ever:** the runner unconditionally denies
  Bash/Read/Write/Edit/NotebookEdit for every non-self-edit agent
  (`--disallowedTools`, not a default). Declaring them does nothing.
  Self-edit (platform-coder) runs in an ephemeral clone pod holding no
  external secrets.
- **Execution is centralized:** anything executable is platform code —
  the broker's built-in tools, app services, and the git-reviewed custom
  tools (`docs/design/12-executable-capabilities.md`) that run in the
  tool-executor. The model chooses *arguments*, never code.
- **Anthropic token brokering
  (`docs/design/09-token-brokering.md`):** runner pods hold no Anthropic API
  key; the claude-proxy injects it per-request from an njs (nginx JavaScript)
  hook that re-reads the secret each time, so rotation is instant and pods
  have nothing to leak.
- **Default-deny NetworkPolicy:** agent pods have no internet egress; the
  tool-executor is the single third-party-egress point; the API accepts
  ingress only from web/runner/mcp-broker; app pods only from web/api.
- **Secrets:** values live only in Kubernetes Secrets — never in git, never
  in Postgres. Declarative secret blocks describe and verify them (a probe or
  a sandboxed verify script). Skills bind secrets into the pods of agents
  that declared them (`envFrom`); tools do better, and take their secrets
  call-time-only inside the executor, which is why a capability that needs a
  credential belongs in a tool.
- **Untrusted-content hygiene:** report fragments are HTML-sanitized
  (nh3) and rendered in a sandboxed, CSP-deny-all iframe; news ingestion is
  privilege-separated (gatherer holds zero credentials); connector
  output is length-capped with mass-pings defanged.

## Broker authentication: current state and target

The original scheme, still the fallback for agents that hold platform API
keys: the dispatcher mints a per-run, role-scoped API key into the pod env
(`AP_API_TOKEN`); the runner sends it as a bearer to the mcp-broker, which
forwards it verbatim to the platform API (the broker holds no credentials of
its own — no confused deputy). Revoked at run end; a 15-minute sweep reaps
orphans; single-owner semantics revoke predecessors.

Weakness: it is a **bearer secret in an env var** — replayable if it
ever leaks, and owned by anything that executes in the pod. The target
is *identity, not secrets*: five layers, additive, all now live.

### Layer 1 — Projected ServiceAccount tokens [LIVE]

One zero-RBAC ServiceAccount per agent (`agent-<name>`, created lazily at
launch); run pods mount a **projected, bound SA token** (TokenRequest API):
audience `agent-platform`, TTL 7200s, auto-rotated by the kubelet, never
minted or stored by the dispatcher, read by the runner from
`AP_API_TOKEN_FILE`. The API validates it via the Kubernetes TokenReview API
(which needs the `system:auth-delegator` ClusterRole) and derives the caller
identity from `system:serviceaccount:<ns>:agent-<name>`; the role comes from
what the agent's own definition declares — a Postgres row since
[design-15](design/15-db-first-agents.md), formerly git. Kills secret
distribution entirely, and the
audience binding makes the token useless anywhere else — including at the
Kubernetes API itself.

### Layer 2 — Attested mTLS via SPIFFE/SPIRE [LIVE]

SPIRE (spiffe/spire chart, trust domain `pai`) attests every pod by its
ServiceAccount and issues rotating X.509 SVIDs
(SVIDs are the short-lived certificates SPIRE issues, named
`spiffe://pai/ns/<ns>/sa/<sa>`). ghostunnel sidecars carry the mutual TLS so
app code stays TLS-ignorant: broker + executor bind localhost with an
SVID-authenticated front door on 8443 (namespace workloads → broker;
ONLY the broker's identity → executor), and MCP-talking run pods get a
native-sidecar client tunnel whose startupProbe (a full TLS dial) gates
the runner until the pod's identity works. The run JWT + SA token remain
required — layers, not alternatives. `spire.enabled=false` is the
break-glass helm flip. broker→API stays netpol+token by choice (the API
also serves the web UI).

### Layer 3 — Sender-constrained run tokens [LIVE]

Workload identity says *which agent*; a short-lived **run JWT** says
*which run* (scope, TTL, audit correlation) and is bound to the pod's
key via a `cnf` claim (RFC 8705 style). The broker checks both. Even a
full transcript + env disclosure yields nothing replayable off-pod.

### Layer 4 — User identity propagation (on-behalf-of) [LIVE, admin-stubbed]

Every run records the **initiating principal**; broker/API decisions
use the intersection of agent capability and user entitlement (an agent
is never a privilege-escalation path past its user). The platform is
single-operator, so this ships as a real principal model with one seeded
`admin` user: a principals table, `runs.initiated_by` (the *root* principal,
inherited down a chain of agents invoking agents), a user claim in the run
token, and entitlement checks that trivially pass for admin. The
family-access roadmap is then additive: more user rows, per-user web
sessions, per-user entitlements — no re-architecture.

### Layer 5 — Central authorization + audit at the broker [LIVE]

The broker is the single chokepoint, so it carries: per-tool
authorization from the agent's *declared* tool grants (the agent's own
row — `agent_defs.platform_tools`/`harness_tools`, design-15 — is the
policy; the tools-only scoped tokens from
`docs/design/12-executable-capabilities.md` are the substrate), an
append-only audit log of every tool call (verified identity, tool,
args digest, decision, latency), per-identity rate limits, and alerting
on denials/anomalies (health-monitor reads the audit stream). Two of the
core tools — `agents_edit`/`agents_grant` — write that policy itself; see
the RBAC split and its accepted indirect-escalation caveat in
[design-15](design/15-db-first-agents.md).

## Change control [LIVE]

Capability (skills, tools, secrets-as-code, report types, apps) lives in git
behind the pull-request change loop (`docs/building-blocks/changes.md`) —
nothing there goes live unreviewed. Agent *definitions* are the one
exception since design-15: they are Postgres rows, editable immediately by
whoever holds `agents_edit`/`agents_grant`, with an append-only,
fully-attributed change log (`agent_versions`) instead of pre-merge review.
CI scans for vulnerabilities and secret hygiene; `git add -A` is banned in
this repository to keep secret values out of history.

## Operational invariants

- A new tool or capability cannot exist undocumented (a test keeps the
  in-app tool help in lockstep with the grantable-tool list) or
  unauthorized (allow-list: unknown tool = no grant).
- Credential rotation: update the Kubernetes Secret, and nothing else — the
  claude-proxy re-reads per request, the executor injects per call.
- When in doubt, the answer to "can the agent just have a shell for
  this?" is no — build the capability as reviewed code instead.
