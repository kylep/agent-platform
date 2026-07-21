# Milestone 06 — Hardening

The deferred cage-tightening: prove-it-works came first, now lock it
down.

## Scope

- **Pod security:** tight securityContext on all services and runners
  (non-root, no privilege escalation, dropped capabilities, seccomp),
  read-only root filesystems where possible.
- **Network policy:** default-deny with explicit egress allowlists;
  runner egress restricted to what its skills justify.
- **Secret rotation:** rotation workflows in the secrets UI, token
  steward finalized from 01's findings, audit log of secret access by
  run.
- **Git write credential → GitHub App for PR support:** M2 self-edit uses a
  repo-scoped **deploy key** (`github-deploy-key` secret, ssh, push-only) —
  limited and good for the tier-1 direct-commit path. Deploy keys can't use
  the REST API, so tier-2 **pull requests** (the freeform platform-coder
  flow) need a token. Wire the **PericakAI GitHub App** (installation tokens)
  for that: store App ID + private key + install id, mint tokens on demand.
  Private key already exists at `~/gh/multi/secrets/pericakai.private-key.pem`.
  (A personal `gh` token was used briefly during bring-up and removed; it
  appeared once in an api pod log before the deploy-key switch — rotate if
  paranoid, though that pod is gone.)
- **Supply chain:** image scanning in CI (trivy), pinned digests,
  semgrep on services.
- **Exposure:** authenticated path beyond the LAN (Cloudflare tunnel or
  equivalent), rate limits on public listeners.
- **Backup/DR:** scheduled postgres backups to the second SSD or
  off-box, documented restore drill.

## Adversarial test findings (2026-07-20)

An adversarial agent probed the live platform (RBAC matrix, token scoping,
self-hosting containment, input validation, webhook auth). Verdict: no
exploitable issues — RBAC allow-lists exact, no auth bypass/escalation, no
500s, tier classification fails closed. Fixed the hardening nits it found:

- Removed the unenforced `agent` scope from the API-key mint API (role is the
  only boundary; the column stays an internal owner label for system keys).
- `GET /api/setup-state` no longer discloses secret names/health to anonymous
  callers once setup is complete.
- `GET /api/runs?limit=` is now bounded (`ge=1, le=500`).

Still open (nit, no data exposure): an nginx path-normalization artifact makes
`GET /api/<encoded-slash traversal>` return the public SPA HTML (200) instead
of a JSON 404. Fix during a web-tier hardening pass.

## Progress (2026-07-20)

Done + verified live:
- [x] **Runner pod securityContext** — runner Jobs run `runAsNonRoot`,
      `allowPrivilegeEscalation=false`, drop ALL capabilities, seccomp
      `RuntimeDefault` (set in `joblauncher.build_job`). Runs still succeed.
- [x] **Platform-service securityContext + declarative reconverge** — api/
      dispatcher/recorder run non-root (numeric uid/gid 65534), drop ALL caps,
      no-priv-escalation, seccomp; web/nginx gets no-priv-escalation + seccomp
      (caps kept — nginx needs SETUID/SETGID/CHOWN). Applied via `helm upgrade`
      (release rev 4), ending the imperative `kubectl set env` drift — **helm is
      now the source of truth**; all config/infra changes go through the chart.
- [x] **Secret-access audit log** — `secret_access` table records the k8s
      secrets each run's pod is granted at launch; `GET /api/audit/secret-access`
      (admin, filterable) + `secrets_granted` on the run detail.
- [x] **Supply-chain scanning in CI** — report-only `security-scan` job: Trivy
      fs (pinned @SHA) + Semgrep (pinned), least-privilege `permissions`.

Blocked on Kyle (accounts/decisions or supervised rollout):
- [ ] **Network policy** (default-deny + egress allowlists) — can sever pod↔
      kafka/postgres/k8s-API; apply with Kyle present to test connectivity after.
- [ ] **External exposure** (Cloudflare tunnel + rate limits) — needs Kyle's
      Cloudflare account.
- [ ] **Backup/DR** (postgres backups off-box + restore drill) — needs Kyle to
      pick the backup target/credentials.

## Done when

Scans run clean in CI, a restore drill succeeds from backup, and the
platform is reachable from outside the LAN without widening the blast
radius.
