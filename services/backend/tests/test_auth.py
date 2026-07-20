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


async def test_change_password_flow(admin_client):
    # wrong current password is rejected
    r = await admin_client.post("/api/change-password",
                                json={"old_password": "wrong", "new_password": "newpw12345"})
    assert r.status_code == 403
    # too-short new password is rejected
    r = await admin_client.post("/api/change-password",
                                json={"old_password": "pw12345678", "new_password": "short"})
    assert r.status_code == 422
    # valid rotation succeeds
    r = await admin_client.post("/api/change-password",
                                json={"old_password": "pw12345678", "new_password": "newpw12345"})
    assert r.status_code == 200
    # old password no longer logs in; new one does
    await admin_client.post("/api/logout")
    assert (await admin_client.post("/api/login", json={"password": "pw12345678"})).status_code == 401
    assert (await admin_client.post("/api/login", json={"password": "newpw12345"})).status_code == 200


async def test_change_password_requires_auth(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    # not logged in
    r = await client.post("/api/change-password",
                          json={"old_password": "pw12345678", "new_password": "newpw12345"})
    assert r.status_code == 401


async def test_setup_state_hides_secrets_when_unauthenticated_post_setup(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    # not logged in, admin exists → secrets must not be disclosed
    body = (await client.get("/api/setup-state")).json()
    assert body["needs_admin"] is False and body["secrets"] == []
    # authenticated → secrets visible again (for the gate)
    await client.post("/api/login", json={"password": "pw12345678"})
    assert len((await client.get("/api/setup-state")).json()["secrets"]) >= 1
