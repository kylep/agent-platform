"""Shared secrets for webhook ingress (docs/design/16).

The storage rule this module exists to enforce: a webhook secret is NEVER part
of an agent definition. The definition carries the auth MODE — versioned,
rollbackable, safe to snapshot — and the value lives in `webhook_secrets`,
write-only, keyed by (agent, path). Everything that reads or writes one goes
through here so there is a single place to check that claim against.

Salted SHA-256 rather than a password KDF: this guards a machine-to-machine
header on a hot ingress path, and every comparison happens per inbound request.
Be precise about what that buys, though. The server enforces a LENGTH floor
(`MIN_SECRET_LENGTH`), and length is not entropy — nothing stops someone typing
sixteen guessable characters, and a fast hash makes a guessable secret cheap to
crack offline from a stolen table. What actually makes these strong is
GENERATING them rather than choosing them, which is a UI affordance, not a
server guarantee; if webhook secrets ever become something people routinely
invent by hand, this should become a KDF. The per-row salt is what stops a
stolen table from being a rainbow lookup or from revealing that two paths share
one secret.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets as _secrets

from sqlalchemy import delete, select

from agentplatform.db import WebhookSecret

# The header an external caller presents. Named in the design doc, echoed by
# the UI's tooltip, and compared here — one spelling, one place.
WEBHOOK_SECRET_HEADER = "X-AP-Webhook-Secret"

# Bounds, not a strength claim (see the module docstring). The floor is set
# where online guessing stops being the attack; the ceiling exists because an
# unbounded body would be hashed on every inbound request, which is a free
# CPU-burn primitive on an endpoint reachable without a platform key.
MIN_SECRET_LENGTH = 16
MAX_SECRET_LENGTH = 512


def new_salt() -> str:
    return _secrets.token_hex(16)


def hash_secret(secret: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{secret}".encode()).hexdigest()


def verify_secret(secret: str, salt: str, expected_hash: str) -> bool:
    """Constant-time comparison — a timing oracle on the digest would let a
    caller walk the hash out one byte at a time."""
    return hmac.compare_digest(hash_secret(secret, salt), expected_hash)


async def set_secret(session, agent: str, path: str, secret: str) -> None:
    """Set or ROTATE the secret for one path. Replaces in place (new salt and
    all): a rotation must leave exactly one live secret, never two."""
    row = await session.get(WebhookSecret, (agent, path))
    if row is None:
        row = WebhookSecret(agent=agent, path=path)
        session.add(row)
    row.salt = new_salt()
    row.secret_hash = hash_secret(secret, row.salt)


async def clear_secret(session, agent: str, path: str) -> bool:
    """Remove one path's secret. True if there was one to remove."""
    row = await session.get(WebhookSecret, (agent, path))
    if row is None:
        return False
    await session.delete(row)
    return True


async def clear_agent_secrets(session, agent: str) -> None:
    """Drop every secret an agent holds — called when the agent is deleted.
    Explicit rather than a database cascade because the platform runs on both
    sqlite (tests, where foreign keys are off unless a PRAGMA turns them on)
    and postgres, and a cleanup that only happens on one of them is not a
    cleanup."""
    await session.execute(delete(WebhookSecret).where(WebhookSecret.agent == agent))


async def prune_undeclared(session, agent: str, declared: set[str]) -> None:
    """Drop secrets for paths the agent no longer declares.

    A secret outlives no path. Without this, removing a webhook entry would
    leave its credential behind, and re-declaring that path later would
    silently re-arm a secret nobody can see or audit — the same
    resurrection-by-the-back-door that keeps secrets out of `agent_versions` in
    the first place (docs/design/16). Fail closed instead: a re-declared path
    starts with no secret, and `secret` mode there rejects callers until
    someone sets one.
    """
    stmt = delete(WebhookSecret).where(WebhookSecret.agent == agent)
    if declared:
        stmt = stmt.where(WebhookSecret.path.notin_(declared))
    await session.execute(stmt)


async def verify(session, agent: str, path: str, presented: str) -> bool | None:
    """Does `presented` match the stored secret for (agent, path)?

    Three-valued on purpose: True/False are the authentication answer, and None
    means NO SECRET IS SET — a `secret`-mode path with no live hash. That is a
    misconfiguration (a rollback restored the mode, or an edit stopped
    half-way), and the caller must fail closed on it rather than treat it as a
    mismatch, so the operator sees the cause instead of debugging a 401.
    """
    row = await session.get(WebhookSecret, (agent, path))
    if row is None or not row.secret_hash:
        return None
    return verify_secret(presented, row.salt, row.secret_hash)


async def paths_with_secrets(session, agent: str) -> set[str]:
    """Which of an agent's paths currently HAVE a secret — the `secret_set`
    flag the API derives on read. The value itself never leaves this module."""
    rows = (await session.execute(
        select(WebhookSecret.path).where(WebhookSecret.agent == agent))).scalars()
    return set(rows)


async def secrets_by_agent(session) -> dict[str, set[str]]:
    """The same flag for every agent in one query, for the listing endpoint."""
    out: dict[str, set[str]] = {}
    rows = (await session.execute(
        select(WebhookSecret.agent, WebhookSecret.path))).all()
    for agent, path in rows:
        out.setdefault(agent, set()).add(path)
    return out
