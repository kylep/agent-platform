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
