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

Done (safe, no-helm slices):
- [x] **Runner pod securityContext** — runner Jobs run `runAsNonRoot`,
      `allowPrivilegeEscalation=false`, drop ALL capabilities, seccomp
      `RuntimeDefault` (set in `joblauncher.build_job`, no helm needed). Verified
      live: runs still succeed with the tightened context.
- [x] **Supply-chain scanning in CI** — report-only `security-scan` job: Trivy
      fs (vuln/secret/misconfig) + Semgrep (python/typescript/secrets).

Blocked on Kyle (decisions or risky helm ops — noted for a supervised pass):
- [ ] **Network policy** (default-deny + egress allowlists) — chart change +
      helm upgrade; can sever pod↔kafka/postgres/k8s-API connectivity, so needs
      a supervised rollout.
- [ ] **Platform-service securityContext** (api/dispatcher/recorder/web) — chart
      change; `helm upgrade` on this cluster can wedge and would revert the
      imperative env drift (AP_SKILLS_ROOT etc.) — reconcile with Kyle present.
- [ ] **Secret rotation + audit log** — rotation is largely supported (set
      overwrites + `updated_at`); the per-run secret-access audit log is the
      real remaining feature.
- [ ] **External exposure** (Cloudflare tunnel + rate limits) — needs Kyle's
      Cloudflare account/decisions.
- [ ] **Backup/DR** (postgres backups off-box + restore drill) — needs Kyle to
      pick the backup target/credentials.

## Done when

Scans run clean in CI, a restore drill succeeds from backup, and the
platform is reachable from outside the LAN without widening the blast
radius.
