"""Sender-constrained run tokens (docs/design/13 C).

The dispatcher signs a short-lived JWT per SA-identity run carrying WHAT the
run may do (its grant set, frozen at launch) and WHO it is for
(initiated_by), bound to WHICH workload may use it (`cnf` = the pod's
ServiceAccount identity, upgraded to a SPIFFE SVID when design/13 B lands).

The projected SA token proves the workload; this JWT proves the run. The API
requires both and checks they agree — so a leaked run JWT is useless without
the pod, a leaked SA token grants nothing beyond what this JWT froze, and a
mid-run manifest edit cannot widen a live run.

Keys: an ES256 pair in the `run-jwt-key` k8s secret; the dispatcher
generates it on first use, everything else only ever reads the public half.
"""
from __future__ import annotations

import logging
import time

import jwt

log = logging.getLogger("runjwt")

SECRET_NAME = "run-jwt-key"
ISSUER = "ap-dispatcher"
AUDIENCE = "agent-platform"
ALGORITHM = "ES256"
# Grace beyond the run timeout — the watcher, not token expiry, ends runs.
EXP_SLACK_SECONDS = 600


def generate_keypair() -> dict[str, str]:
    """A fresh ES256 keypair as PEM strings (the secret's key/values)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    private = ec.generate_private_key(ec.SECP256R1())
    priv_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return {"private_key": priv_pem, "public_key": pub_pem}


def mint(private_key_pem: str, *, run_id: str, agent: str, initiated_by: str,
         tools: list[str], sa_name: str, timeout_seconds: int) -> str:
    now = int(time.time())
    return jwt.encode({
        "iss": ISSUER, "aud": AUDIENCE,
        "iat": now, "exp": now + timeout_seconds + EXP_SLACK_SECONDS,
        "run_id": run_id, "agent": agent, "initiated_by": initiated_by,
        "tools": tools,
        "cnf": {"sa": sa_name},
    }, private_key_pem, algorithm=ALGORITHM)


def verify(public_key_pem: str, token: str, *, expected_sa: str) -> dict | None:
    """Decode + verify a run JWT and require its cnf to match the workload
    identity that presented it. Returns the claims, or None."""
    try:
        claims = jwt.decode(token, public_key_pem, algorithms=[ALGORITHM],
                            audience=AUDIENCE, issuer=ISSUER)
    except jwt.InvalidTokenError as e:
        log.debug("run jwt rejected: %s", e)
        return None
    if (claims.get("cnf") or {}).get("sa") != expected_sa:
        log.warning("run jwt cnf mismatch: %s presented by %s",
                    (claims.get("cnf") or {}).get("sa"), expected_sa)
        return None
    return claims
