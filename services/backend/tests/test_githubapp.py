import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from agentplatform.githubapp import GitHubApp


@pytest.fixture
def keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = key.private_bytes(serialization.Encoding.PEM,
                             serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()
    pub = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv, pub


def test_app_jwt_is_valid_rs256(keypair):
    priv, pub = keypair
    app = GitHubApp("12345", "999", priv)
    token = app.app_jwt(now=1_000_000)
    claims = jwt.decode(token, pub, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == "12345"
    assert claims["iat"] == 1_000_000 - 60 and claims["exp"] == 1_000_000 + 540


def test_installation_token_caches_and_refreshes(keypair):
    priv, _ = keypair
    app = GitHubApp("1", "2", priv)
    calls = []
    def fake_mint(now):
        calls.append(now)
        return f"ghs_token_{len(calls)}"

    t1 = app.installation_token(now=1000, mint=fake_mint)
    t2 = app.installation_token(now=1500, mint=fake_mint)   # within TTL → cached
    assert t1 == t2 == "ghs_token_1" and len(calls) == 1

    # Well past expiry → re-mint.
    t3 = app.installation_token(now=1000 + 3300, mint=fake_mint)
    assert t3 == "ghs_token_2" and len(calls) == 2


def test_ids_are_stripped(keypair):
    priv, _ = keypair
    app = GitHubApp(" 12345\n", "  999 ", priv)
    assert app.app_id == "12345" and app.install_id == "999"
