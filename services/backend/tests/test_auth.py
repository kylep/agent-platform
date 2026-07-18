async def test_setup_flow(client):
    r = await client.get("/api/setup-state")
    assert r.json()["needs_admin"] is True
    assert (await client.post("/api/setup", json={"password": "pw12345678"})).status_code == 200
    assert (await client.post("/api/setup", json={"password": "x"})).status_code == 409
    assert (await client.get("/api/setup-state")).json()["needs_admin"] is False

async def test_login_required_and_works(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    assert (await client.get("/api/runs")).status_code == 401
    assert (await client.post("/api/login", json={"password": "wrong"})).status_code == 401
    assert (await client.post("/api/login", json={"password": "pw12345678"})).status_code == 200
