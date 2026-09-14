# Design note — brokering the shared Claude token out of runner pods

Status: **SHIPPED 2026-07-30 (helm rev 20), verified live.** Runner pods no
longer hold the Claude subscription token; it lives only in the `claude-proxy`
pod, an auth-injecting egress proxy. Tracks the long-standing residual from
[08](08-news-and-injection-hardening.md).

## The risk this closes

A token is only dangerous if an agent can both **read** it and **exfiltrate**
it. Runner egress must allow outbound 443 to any host (WebSearch/WebFetch
agents reach arbitrary sites), so exfiltration can't be closed by allow-listing.
The platform's first line is the **enforced trifecta break** (no agent has
untrusted input + token-read tools + open egress; `runner._permission_args`
denies Bash/Read/Edit/Write/NotebookEdit to every non-self-edit run). Token
brokering removes the remaining defense-in-depth gap: the token was *present*
in pods with open egress, one enforcement bug away from exposure. Now it isn't
in those pods at all — prevention by construction, no one watching anything.

## Feasibility spike (2026-07-30)

Run in-cluster as a throwaway Job (runner image + a stdlib Python proxy on
127.0.0.1 injecting `Authorization` from the mounted secret), so the token
never left the cluster. `claude` 2.1.214, subscription OAuth
(`claude setup-token` long-lived token):

| test | setup | result |
|---|---|---|
| control | real token, direct | works |
| A | **no credential**, `ANTHROPIC_BASE_URL` → proxy | **client refuses**: "Not logged in · Please run /login" — exits before any API call |
| B | dummy `sk-ant-oat01-…` token + proxy replaces Authorization | works end-to-end; client stays in subscription-OAuth mode (sends `oauth-2025-04-20` beta itself) |
| C | `ANTHROPIC_AUTH_TOKEN=dummy` + proxy | works, but flips the client into API-auth mode (different default model selection) |
| D | dummy token **without** the `sk-ant` prefix | works, identical to B |

Conclusions: the CLI needs *a* non-empty `CLAUDE_CODE_OAUTH_TOKEN` to start;
any placeholder keeps it in subscription-OAuth mode; all API traffic follows
`ANTHROPIC_BASE_URL` (nothing bypasses the proxy); the placeholder needs no
particular format (so no collision with the `subscription-guard` CI grep).

## Shipped architecture

**`claude-proxy`** (chart `templates/claude-proxy{,-config}.yaml`,
`claudeProxy.*` values): an nginx (`nginxinc/nginx-unprivileged`, pinned)
Deployment + hardcoded Service `agent-platform-claude-proxy:8000`. The
`claude-credentials` secret is mounted as a **volume** and read **per request**
by an njs handler (`js_set $claude_auth`; njs ships in the official image), so
a token rotation via the Secrets UI propagates through the kubelet's volume
sync (~1 min) with **no restart** — an env var would have cached the old token
until the pod died (the old per-Job mount re-read the secret each launch; this
preserves that property). Every request **replaces** the inbound
`Authorization` header with `Bearer <real token>` before forwarding to
`https://api.anthropic.com` (TLS verified — `proxy_ssl_verify on`, which is
not nginx's default; upstream re-resolved via cluster DNS with `valid=300s`;
SSE unbuffered). Hardened like the other pods: non-root uid 101, read-only
rootfs, no capabilities, no SA token, seccomp RuntimeDefault.

**Runner wiring**: with `claudeProxy.enabled` (default on) the backend env
gains `AP_CLAUDE_PROXY_URL`; `joblauncher.build_job` then omits the
`claude-credentials` volume/mount entirely and passes the URL through;
`runner._install_credentials` returns
`ANTHROPIC_BASE_URL=<proxy>` + a **placeholder** `CLAUDE_CODE_OAUTH_TOKEN`
(spike test A: the CLI refuses to start with none). The secret-access audit
stops recording `claude-credentials` for proxied runs — `secrets_granted` is
now `[]` for a plain agent pod, and that's true, not cosmetic.

**NetworkPolicy**: runner → claude-proxy:8000 ingress allowed; claude-proxy
added to the egress-443 allowlist. Runner keeps open 443 egress (client-side
WebFetch + self-edit git need it) — the point was never to close runner
egress, it was to make sure there's no token behind it.

**Rollback**: `claudeProxy.enabled=false` restores the legacy direct mount
(joblauncher/runner keep both paths; covered by tests either way).

## Verified live (2026-07-30)

- Runner pods post-upgrade: volumes = agents/home/workspace/tmp only, no
  `claude-credentials`, `AP_CLAUDE_PROXY_URL` set; `secrets_granted: []`.
- run-summarizer + health-monitor (MCP tools, streaming) **succeeded** through
  the proxy; proxy access log shows their `/v1/messages` 200s.
- pai run retrieving live web content (server-side web tools) through the
  proxy succeeded.
- The njs per-request read verified live (helm rev 21): a run succeeds with
  the client holding only the placeholder — possible only if njs injected the
  real token from the volume.

## Residual

- The proxy is a single point of failure for all runs (like api/kafka/postgres
  on this single-node cluster); a failed call fails the run, which retries via
  the normal queue path.
- An agent can still *use* the proxy to spend subscription quota — but it is a
  claude process; it could always do that. What it can't do anymore is read or
  exfiltrate the credential itself.
- In-cluster hop is plain HTTP (like every other in-namespace service on this
  single-node LAN deploy); the upstream hop is verified TLS.

## AS BUILT addendum (2026-09-14, design 22)

The proxy gained a second job that follows from the first: since it is the one
pod every response passes through, it now also **reads** Anthropic's
`anthropic-ratelimit-unified-*` headers off each upstream response and posts
them to the API's `POST /api/internal/quota`, authenticated with
`X-AP-Internal-Secret` — a second secret mounted as a volume at
`/secrets/internal` and read per request, exactly like the token, so rotating
it needs no restart. The capture never touches the response it observed: the
filter writes into an njs shared dict and a `js_periodic` tick does the
posting, because issuing the request from the filter itself blanks the
client's reply on njs 1.0.0. See `docs/design/22-quota-usage-bars.md`.

Two lines of NetworkPolicy follow: `allow-api` admits `claude-proxy` on 8000
(the report), and `allow-claude-proxy` admits `api` (the deliberate refresh
probe, which the API makes through this proxy so the token stays here). Both
directions are in-namespace and nothing else changed — the proxy still has no
service account and no other egress.

The forwarding target is now a value, `claudeProxy.upstream`
(`https://api.anthropic.com`), and `proxy_ssl_*` follows its **scheme** rather
than a switch of its own, with `Host` and `proxy_ssl_name` derived from the
same URL. There is therefore no combination of values that ships the token
over https without verifying the certificate; the only thing that moves the
upstream is the docker integration test, to a plain-HTTP fake.
