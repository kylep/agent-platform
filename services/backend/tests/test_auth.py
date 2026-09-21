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


# --- the `qa` principal (docs/design/25) -------------------------------------
# `POST /api/login` takes an optional `principal` (default `admin`): the QA's
# browser logs in as the `qa` row, a reader that renders every page and is
# refused by every write route.

async def _seed_qa(sf, password: str = "qa-pw-123456", role: str = "reader"):
    from agentplatform.api.auth import ph
    from agentplatform.db import Principal
    async with sf() as s:
        s.add(Principal(name="qa", role=role, password_hash=ph.hash(password)))
        await s.commit()


async def test_login_as_qa_sets_a_reader_cookie(client, sf):
    await client.post("/api/setup", json={"password": "pw12345678"})
    await _seed_qa(sf)
    assert (await client.post("/api/login", json={"principal": "qa", "password": "wrong"})).status_code == 401
    r = await client.post("/api/login", json={"principal": "qa", "password": "qa-pw-123456"})
    assert r.status_code == 200 and "ap_session" in r.cookies
    who = (await client.get("/api/whoami")).json()
    assert (who["principal"], who["role"]) == ("qa", "reader")
    # A reader reads the console's data and is refused by every write route.
    assert (await client.get("/api/agents")).status_code == 200
    assert (await client.put("/api/agents/x", json={"prompt": "# x"})).status_code == 403
    assert (await client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})).status_code == 403


async def test_login_unknown_or_passwordless_principal_is_401(client, sf):
    from agentplatform.db import Principal
    await client.post("/api/setup", json={"password": "pw12345678"})
    assert (await client.post("/api/login", json={"principal": "nobody", "password": "x"})).status_code == 401
    # A row with no password (an API-key-only principal) is not a login.
    async with sf() as s:
        s.add(Principal(name="ghost", role="reader", password_hash=None))
        await s.commit()
    assert (await client.post("/api/login", json={"principal": "ghost", "password": ""})).status_code == 401
    assert (await client.post("/api/login", json={"principal": "ghost", "password": "x"})).status_code == 401


async def test_login_without_principal_is_still_the_admin(client, sf):
    await client.post("/api/setup", json={"password": "pw12345678"})
    await _seed_qa(sf, password="pw12345678")
    assert (await client.post("/api/login", json={"password": "pw12345678"})).status_code == 200
    who = (await client.get("/api/whoami")).json()
    assert (who["principal"], who["role"]) == ("admin", "admin")
    # The admin's password does not open the qa row, and vice versa.
    await client.post("/api/logout")
    assert (await client.post("/api/login", json={"principal": "admin", "password": "qa-pw-123456"})).status_code == 401


async def test_login_principal_is_bounded(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    # A principal name is a short lowercase slug: an oversize or spaced name
    # is refused at the schema, before any row is looked up.
    for bad in ("a" * 65, "Bad Name", "", "Admin", "-lead"):
        r = await client.post("/api/login", json={"principal": bad, "password": "pw12345678"})
        assert r.status_code == 422, bad
