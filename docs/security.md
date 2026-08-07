# Security model

How the agent-platform contains untrusted model behavior, what protects
each credential, and the workload-identity roadmap (design/13). Status
tags: **[LIVE]** deployed today · **[PLANNED-NN]** scheduled in design NN.

## Threat model

The primary adversary is **the agent itself**: a model processing
untrusted input (web pages, news items, chat) that can be steered by
prompt injection. Secondary: a compromised pod (supply chain, runtime
bug). We assume the k3s cluster and its operator (Kyle) are trusted; the
web UI sits behind an authenticated admin session.

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
  core broker tools, app services, and (design/12) git-reviewed custom
  tools run in the tool-executor. The model chooses *arguments*, never
  code.
- **Anthropic token brokering (design/09):** runner pods hold no
  Anthropic API key; the claude-proxy injects it per-request (njs), so
  rotation is instant and pods have nothing to leak.
- **Default-deny NetworkPolicy:** agent pods have no internet egress;
  the executor (design/12) is the single third-party-egress point; the
  API accepts ingress only from web/runner/mcp-broker; app pods only
  from web/api.
- **Secrets:** values live only in k8s Secrets (never git, never pg);
  declarative blocks describe + verify them (probe or sandboxed
  verify script). Skills bind secrets via envFrom only into pods of
  agents that declared them — being phased toward call-time-only
  injection in the executor (design/12).
- **Untrusted-content hygiene:** report fragments are nh3-sanitized and
  rendered in a sandboxed, CSP-deny-all iframe; news ingestion is
  privilege-separated (gatherer holds zero credentials); connector
  output is length-capped with mass-pings defanged.

## Broker authentication: current state and target

Today **[LIVE]**: the dispatcher mints a per-run, role-scoped API key
into the pod env (`AP_API_TOKEN`); the runner sends it as a bearer to
the MCP broker, which forwards it verbatim to the platform API (broker
holds no credentials — no confused deputy). Revoked at run end; a 15-min
sweep reaps orphans; single-owner semantics revoke predecessors.

Weakness: it is a **bearer secret in an env var** — replayable if it
ever leaks, and owned by anything that executes in the pod. The target
is *identity, not secrets*: five layers, additive.

### Layer 1 — Projected ServiceAccount tokens [LIVE]

One ServiceAccount per agent; run pods mount a **projected, bound SA
token** (TokenRequest API): audience=`ap-broker`, TTL ~10 min,
auto-rotated by the kubelet, never minted or stored by the dispatcher.
The broker validates via TokenReview/OIDC and derives the caller
identity from `system:serviceaccount:<ns>:agent-<name>`. Kills secret
distribution entirely; audience-binding blocks cross-service replay.

### Layer 2 — Attested mTLS via SPIFFE/SPIRE [LIVE]

SPIRE (spiffe/spire chart, trust domain `pai`) attests every pod by its
ServiceAccount and issues rotating X.509 SVIDs
(`spiffe://pai/ns/<ns>/sa/<sa>`). ghostunnel sidecars carry the mTLS so
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
is never a privilege-escalation path past its user). Today the platform
is single-operator, so this ships as a real principal model with one
seeded `admin` user: `users` table, `runs.initiated_by`, user claim in
the run token, entitlement checks trivially passing for admin. The
family-access roadmap is then additive: more user rows, per-user web
sessions, per-user entitlements — no re-architecture.

### Layer 5 — Central authorization + audit at the broker [LIVE]

The broker is the single chokepoint, so it carries: per-tool
authorization from the agent's *declared* tool grants (manifest as
policy; design/12's tools-only scoped tokens are the substrate), an
append-only audit log of every tool call (verified identity, tool,
args digest, decision, latency), per-identity rate limits, and alerting
on denials/anomalies (health-monitor reads the audit stream).

## Change control [LIVE]

All configuration (agents, skills, tools, secrets-as-code, report
types, apps) lives in git behind the PR change loop — nothing goes
live unreviewed. CI scans (vuln + secret hygiene); `git add -A` is
banned to keep secret values out of history.

## Operational invariants

- A new tool/capability cannot exist undocumented (TOOL_HELP lockstep
  test) or unauthorized (allow-list: unknown tool = no grant).
- Credential rotation: k8s Secret update only; nothing else to touch
  (proxy reads per-request; executor injects per-call).
- When in doubt, the answer to "can the agent just have a shell for
  this?" is no — build the capability as reviewed code instead.
