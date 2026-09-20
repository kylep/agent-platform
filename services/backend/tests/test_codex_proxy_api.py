import json

from agentplatform.db import SecretMeta


def _auth_json() -> str:
    return json.dumps({"auth_mode": "chatgpt", "tokens": {
        "access_token": "access", "refresh_token": "refresh", "account_id": "account"}})


async def test_internal_codex_auth_persists_rotated_credential(
        admin_client, secret_store, sf):
    secret = "s" * 32
    admin_client._transport.app.state.settings.internal_secret = secret
    response = await admin_client.post(
        "/api/internal/codex-auth",
        headers={"X-AP-Internal-Secret": secret},
        json={"auth_json": _auth_json()},
    )
    assert response.status_code == 200
    assert await secret_store.get("codex-credentials") == {"auth.json": _auth_json()}
    async with sf() as session:
        assert (await session.get(SecretMeta, "codex-credentials")).status == "valid"


async def test_internal_codex_auth_rejects_wrong_secret_and_bad_payload(
        admin_client, secret_store):
    secret = "s" * 32
    admin_client._transport.app.state.settings.internal_secret = secret
    denied = await admin_client.post(
        "/api/internal/codex-auth",
        headers={"X-AP-Internal-Secret": "wrong"},
        json={"auth_json": _auth_json()},
    )
    assert denied.status_code == 401
    malformed = await admin_client.post(
        "/api/internal/codex-auth",
        headers={"X-AP-Internal-Secret": secret},
        json={"auth_json": "{}"},
    )
    assert malformed.status_code == 422
    assert await secret_store.get("codex-credentials") is None
