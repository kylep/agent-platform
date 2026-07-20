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

## Done when

Scans run clean in CI, a restore drill succeeds from backup, and the
platform is reachable from outside the LAN without widening the blast
radius.
