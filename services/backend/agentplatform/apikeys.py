"""Pure helpers for `ap_` API tokens. Tokens are high-entropy random
strings; only their SHA-256 hash and a short display prefix are persisted,
so the plaintext is shown to the caller exactly once at mint time."""
import hashlib
import secrets

TOKEN_PREFIX = "ap_"


def generate_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_prefix(token: str) -> str:
    """First 11 chars (`ap_` + 8) — enough to identify a key in listings
    without revealing it."""
    return token[:11]


async def revoke_run_keys(session, run_id: str) -> None:
    """Revoke any per-run API tokens minted for `run_id`. Called when a run
    reaches a terminal state so a finished run's (operator-scoped) token can no
    longer invoke agents. Mutates rows in the caller's session; caller commits."""
    from sqlalchemy import select
    from agentplatform.db import ApiKey, utcnow
    keys = (await session.execute(select(ApiKey).where(
        ApiKey.run_id == run_id, ApiKey.revoked_at.is_(None)))).scalars().all()
    for k in keys:
        k.revoked_at = utcnow()
