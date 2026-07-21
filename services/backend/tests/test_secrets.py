def _by(rows):
    return {r["name"]: r for r in rows}


async def test_secret_lifecycle(admin_client, secret_store):
    rows = _by((await admin_client.get("/api/secrets")).json())
    cc = rows["claude-credentials"]
    assert cc["status"] == "missing" and cc["required"] is True and cc["hint"]
    # connector-declared secret is surfaced with a format hint + suggested key
    assert "discord-bot" in rows and rows["discord-bot"]["required"] is False
    assert rows["discord-bot"]["key"] == "token" and rows["discord-bot"]["hint"]
    # skill secret whose key must be the env var name it binds to
    assert rows["github-token"]["key"] == "GITHUB_TOKEN"
    r = await admin_client.put("/api/secrets/claude-credentials",
                               json={"data": {"credentials.json": "{\"tok\":1}"}})
    assert r.status_code == 200
    assert await secret_store.get("claude-credentials") == {"credentials.json": "{\"tok\":1}"}
    rows = _by((await admin_client.get("/api/secrets")).json())
    assert rows["claude-credentials"]["status"] == "unprobed"


async def test_can_add_arbitrary_secret_via_api(admin_client, secret_store):
    r = await admin_client.put("/api/secrets/discord-bot", json={"data": {"token": "abc123"}})
    assert r.status_code == 200
    assert await secret_store.get("discord-bot") == {"token": "abc123"}

async def test_setup_state_includes_secrets(client):
    assert client is not None
    r = await client.get("/api/setup-state")
    assert r.json()["secrets"][0]["name"] == "claude-credentials"


async def test_out_of_band_secret_reports_unprobed(admin_client, secret_store):
    # Secret created directly in the store (kubectl path), no API PUT / meta row.
    await secret_store.set("claude-credentials", {"credentials.json": "{}"})
    r = await admin_client.get("/api/secrets")
    assert r.json()[0]["status"] == "unprobed"
