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
- **Migrate the git write credential off the personal `gh` token:** M2
  shipped using a personal `gh` OAuth token (broad scope, tied to Kyle's
  account) as the `github-token` secret to unblock the self-hosting loop.
  Replace it with the **PericakAI GitHub App** (repo-scoped installation
  tokens): store App ID + private key + install id, mint installation
  tokens on demand. Private key already exists at
  `~/gh/multi/secrets/pericakai.private-key.pem`.
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
