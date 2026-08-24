# 16 — Per-webhook shared-secret auth

## Problem

Webhook ingress (`POST /api/webhooks/{path}`) requires a platform API key with
the operator role. External services (GitHub, IFTTT, a curl from anywhere)
can't reasonably hold a platform key, so webhooks are effectively
internal-only.

## Decision

Each declared webhook path gets an `auth` mode in the agent's entrypoints:

- `none` (default): exactly today's behavior — platform API key required.
  Nothing gets less secure by default.
- `secret`: the request is accepted when it carries the shared secret in the
  `X-AP-Webhook-Secret` header (constant-time compare). A valid platform key
  also still works.

## Secret storage — never in the change log

The secret VALUE does not live on the agent definition. `agent_versions`
snapshots the whole definition on every write; putting secrets (even hashed)
there would spray them across the change log and let rollback silently
resurrect rotated secrets. Instead:

- `webhook_secrets` table: (agent, path) pk, `secret_hash` (salted SHA-256,
  per-row random salt), timestamps. Deleted with the agent; replaced on
  rotation; never selected back out through any API.
- The definition's webhook entry carries only `{"path": ..., "auth":
  "none"|"secret"}` — the mode is versioned/rollbackable, the secret is not.
- Fail closed: `auth: "secret"` with no stored hash row rejects callers
  (503-style detail naming the misconfiguration) rather than falling open.
- Rollback restores the MODE only. Rolling back to `secret` with no live hash
  is the fail-closed case above until a new secret is set.
- A secret outlives no path. Deleting the agent, or any definition write that
  stops declaring a path, drops that path's row — otherwise re-declaring it
  later would silently re-arm a credential nobody can see, which is the same
  resurrection this whole section exists to prevent.

## API

- `PUT /api/agents/{name}/webhooks/{path}/secret` body `{"secret": ...}` —
  write-only, same authority as entrypoints edits (admin or `agents_edit`),
  min length 16. Sets/rotates; never readable back. `DELETE` removes it.
- `GET /api/agents/{name}` webhook entries gain `secret_set: bool` (derived,
  not stored on the def) so the UI can show state without the value.
- Ingress order: resolve path → owning agent (cross-agent uniqueness enforced
  at write since design-15's final wave) → auth (platform key passes as today;
  else `secret` mode compares the header) → enabled/quarantine checks as today.

  Auth runs BEFORE the state checks, and the path resolution that has to
  precede it is not allowed to leak: an unauthenticated caller gets the same
  401 whether or not the path exists, so the endpoint doesn't become a
  directory of declared webhooks. Only an already-authenticated caller sees
  404 / 409.

## UI

agents › {name} › config › webhooks › {path}: auth dropdown [None, Secret].
Choosing Secret reveals a password field with an eye toggle; saving stores the
secret (separate write-only call after the def save) and the row shows
"secret set · rotate". A tooltip on the row prints the exact requirement:
`X-AP-Webhook-Secret: <your secret>`. Beside the field, a Generate button fills
in a fresh 24-byte URL-safe random secret and unmasks it — a secret the operator
can't read is one they can't hand to the caller, and it is never shown again
after the save. The /agents listing gains a Webhook column — a checkmark for any
agent with ≥1 declared webhook, em-dash otherwise, in both the active and
system tables; its tooltip lists the declared `POST /api/webhooks/<path>` lines.

## Explicitly not now

Cert/mTLS-style caller auth (the dropdown leaves room); per-webhook rate
limits; exposing secret-setting through the `agents_edit` broker tool (UI/API
only until a need shows up).
