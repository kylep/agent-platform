from agentplatform.db import Run
from sqlalchemy import select


async def _mint(client, role):
    r = await client.post("/api/api-keys", json={"name": f"wh-{role}", "role": role, "agent": None})
    return r.json()["token"]


async def test_webhook_requires_auth(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    assert (await client.post("/api/webhooks/hello-world", json={"x": 1})).status_code == 401


async def test_webhook_with_operator_key_creates_run(admin_client, sf):
    token = await _mint(admin_client, "operator")
    admin_client.cookies.clear()  # only the bearer key authenticates now
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"event": "push", "ref": "main"})
    assert r.status_code == 202
    async with sf() as s:
        run = (await s.execute(select(Run))).scalars().one()
    assert run.agent == "hello-world" and run.trigger == "webhook"
    assert "push" in run.prompt and "webhook" in run.prompt.lower()


async def test_webhook_reader_key_forbidden(admin_client):
    token = await _mint(admin_client, "reader")
    admin_client.cookies.clear()
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"}, json={})
    assert r.status_code == 403   # reader can't trigger runs


async def test_webhook_unknown_agent_404(admin_client):
    r = await admin_client.post("/api/webhooks/ghost", json={})
    assert r.status_code == 404
