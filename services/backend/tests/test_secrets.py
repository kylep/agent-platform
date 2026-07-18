async def test_secret_lifecycle(admin_client, secret_store):
    r = await admin_client.get("/api/secrets")
    assert r.json() == [{"name": "claude-credentials", "status": "missing", "required": True}]
    r = await admin_client.put("/api/secrets/claude-credentials",
                               json={"data": {"credentials.json": "{\"tok\":1}"}})
    assert r.status_code == 200
    assert await secret_store.get("claude-credentials") == {"credentials.json": "{\"tok\":1}"}
    r = await admin_client.get("/api/secrets")
    assert r.json()[0]["status"] == "unprobed"

async def test_setup_state_includes_secrets(client):
    assert client is not None
    r = await client.get("/api/setup-state")
    assert r.json()["secrets"][0]["name"] == "claude-credentials"
