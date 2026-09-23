from agentplatform.apikeys import TOKEN_PREFIX, generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey


def test_token_helpers():
    t = generate_token()
    assert t.startswith(TOKEN_PREFIX) and len(t) > 20
    assert token_prefix(t) == t[:11]
    assert hash_token(t) == hash_token(t) and hash_token(t) != hash_token(generate_token())


async def _mint(client, name, role, agent=None):
    r = await client.post("/api/api-keys", json={"name": name, "role": role, "agent": agent})
    return r


async def test_mint_returns_token_once_and_lists_without_it(admin_client):
    r = await _mint(admin_client, "ci", "operator")
    assert r.status_code == 201
    body = r.json()
    assert body["token"].startswith(TOKEN_PREFIX)
    assert body["prefix"] == body["token"][:11] and body["role"] == "operator"

    listing = (await admin_client.get("/api/api-keys")).json()
    assert len(listing) == 1
    assert "token" not in listing[0]
    assert listing[0]["prefix"] == body["prefix"] and listing[0]["revoked_at"] is None


async def test_invalid_role_rejected(admin_client):
    for role in ("wizard", "dev", "tools", "relay", "coder", "annotator"):
        assert (await _mint(admin_client, "bad", role)).status_code == 422


async def test_human_api_key_roles_are_accepted(admin_client):
    for role in ("reader", "operator", "admin"):
        assert (await _mint(admin_client, role, role)).status_code == 201


async def test_bearer_key_authenticates_and_role_is_enforced(admin_client):
    admin_token = (await _mint(admin_client, "root", "admin")).json()["token"]
    reader_token = (await _mint(admin_client, "ro", "reader")).json()["token"]

    # Drop the interactive session so only the bearer header authenticates.
    admin_client.cookies.clear()
    assert (await admin_client.get("/api/runs")).status_code == 401

    ok = await admin_client.get("/api/runs", headers={"Authorization": f"Bearer {admin_token}"})
    assert ok.status_code == 200

    # /api/api-keys is admin-only; a reader key is authenticated but forbidden.
    forbidden = await admin_client.get("/api/api-keys",
                                       headers={"Authorization": f"Bearer {reader_token}"})
    assert forbidden.status_code == 403


async def test_revoked_key_stops_working(admin_client):
    minted = (await _mint(admin_client, "temp", "admin")).json()
    token, key_id = minted["token"], minted["id"]
    admin_client.cookies.clear()
    hdr = {"Authorization": f"Bearer {token}"}
    assert (await admin_client.get("/api/runs", headers=hdr)).status_code == 200

    # Revoke needs admin; re-auth with the (still-valid) admin key to revoke itself.
    assert (await admin_client.delete(f"/api/api-keys/{key_id}", headers=hdr)).status_code == 200
    assert (await admin_client.get("/api/runs", headers=hdr)).status_code == 401


async def test_mint_ignores_agent_scope(admin_client):
    # the removed `agent` field must not become an enforced/echoed scope
    r = await admin_client.post("/api/api-keys", json={"name": "x", "role": "reader", "agent": "hello-world"})
    assert r.status_code == 201 and r.json()["agent"] is None


async def test_key_role_changes_take_effect_without_rotation(admin_client):
    admin_token = (await _mint(admin_client, "admin", "admin")).json()["token"]
    reader = (await _mint(admin_client, "reader", "reader")).json()
    key_id, reader_token = reader["id"], reader["token"]
    admin_client.cookies.clear()
    admin_hdr = {"Authorization": f"Bearer {admin_token}"}
    reader_hdr = {"Authorization": f"Bearer {reader_token}"}

    assert (await admin_client.get("/api/api-keys", headers=reader_hdr)).status_code == 403
    changed = await admin_client.patch(f"/api/api-keys/{key_id}", json={"role": "admin"}, headers=admin_hdr)
    assert changed.status_code == 200 and changed.json()["role"] == "admin"
    assert (await admin_client.get("/api/api-keys", headers=reader_hdr)).status_code == 200

    changed = await admin_client.patch(f"/api/api-keys/{key_id}", json={"role": "reader"}, headers=admin_hdr)
    assert changed.status_code == 200
    assert (await admin_client.get("/api/api-keys", headers=reader_hdr)).status_code == 403
    assert (await admin_client.patch(f"/api/api-keys/{key_id}", json={"role": "annotator"}, headers=admin_hdr)).status_code == 422

    assert (await admin_client.delete(f"/api/api-keys/{key_id}", headers=admin_hdr)).status_code == 200
    assert (await admin_client.patch(f"/api/api-keys/{key_id}", json={"role": "operator"}, headers=admin_hdr)).status_code == 409


async def test_platform_managed_keys_are_not_edited_as_user_keys(admin_client, sf):
    assert (await _mint(admin_client, "app:fake", "operator")).status_code == 422
    token = generate_token()
    async with sf() as s:
        key = ApiKey(name="app:running", role="annotator",
                     key_hash=hash_token(token), prefix=token_prefix(token))
        s.add(key)
        await s.commit()
        key_id = key.id

    rows = (await admin_client.get("/api/api-keys")).json()
    assert rows[0]["managed"] is True and rows[0]["role"] == "annotator"
    assert (await admin_client.patch(f"/api/api-keys/{key_id}", json={"role": "operator"})).status_code == 409
