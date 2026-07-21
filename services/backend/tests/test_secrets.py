def _by(rows):
    return {r["name"]: r for r in rows}


async def test_secret_lifecycle(admin_client, secret_store):
    rows = _by((await admin_client.get("/api/secrets")).json())
    cc = rows["claude-credentials"]
    assert cc["status"] == "missing" and cc["required"] is True and cc["hint"]
    # connector-declared secret is surfaced with a format hint + suggested key
    assert "discord-bot" in rows and rows["discord-bot"]["required"] is False
    assert rows["discord-bot"]["key"] == "token" and rows["discord-bot"]["hint"]
    r = await admin_client.put("/api/secrets/claude-credentials",
                               json={"data": {"credentials.json": "{\"tok\":1}"}})
    assert r.status_code == 200
    assert await secret_store.get("claude-credentials") == {"credentials.json": "{\"tok\":1}"}
    rows = _by((await admin_client.get("/api/secrets")).json())
    assert rows["claude-credentials"]["status"] == "unprobed"


async def test_verify_secret(admin_client, secret_store, monkeypatch):
    import agentplatform.api.secrets as secrets_api
    # not set → 404
    assert (await admin_client.post("/api/secrets/discord-bot/verify")).status_code == 404
    await secret_store.set("discord-bot", {"token": "abc"})
    # a non-probeable secret → 422
    await secret_store.set("claude-credentials", {"token": "x"})
    assert (await admin_client.post("/api/secrets/claude-credentials/verify")).status_code == 422
    # stub the HTTP check → valid, then invalid
    monkeypatch.setattr(secrets_api, "_http_ok", lambda url, headers: True)
    r = await admin_client.post("/api/secrets/discord-bot/verify")
    assert r.status_code == 200 and r.json()["status"] == "valid"
    monkeypatch.setattr(secrets_api, "_http_ok", lambda url, headers: False)
    r = await admin_client.post("/api/secrets/discord-bot/verify")
    assert r.json()["status"] == "invalid"


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
