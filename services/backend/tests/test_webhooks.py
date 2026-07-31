from agentplatform.events import TOPIC_RUN_INBOUND


import pytest


@pytest.fixture(autouse=True)
def declare_webhook(tmp_agents):
    # Webhook paths must be declared in entrypoints.yaml (docs/design/10);
    # hello-world opts in with a path matching its name.
    (tmp_agents / "hello-world" / "entrypoints.yaml").write_text(
        "webhooks:\n  - path: hello-world\n")


async def _mint(client, role):
    r = await client.post("/api/api-keys", json={"name": f"wh-{role}", "role": role, "agent": None})
    return r.json()["token"]


async def test_webhook_requires_auth(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    assert (await client.post("/api/webhooks/hello-world", json={"x": 1})).status_code == 401


async def test_webhook_with_operator_key_emits_inbound(admin_client, producer):
    token = await _mint(admin_client, "operator")
    admin_client.cookies.clear()  # only the bearer key authenticates now
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"},
                                json={"event": "push", "ref": "main"})
    assert r.status_code == 202
    rid = r.json()["id"]
    # Event-sourced: produced to run.inbound (no synchronous Run row); the
    # ingest consumer materializes it.
    inbound = [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]
    assert len(inbound) == 1
    _, key, data = inbound[0]
    assert key == rid and data["agent"] == "hello-world" and data["trigger"] == "webhook"
    assert "push" in data["prompt"] and "webhook" in data["prompt"].lower()


async def test_webhook_reader_key_forbidden(admin_client):
    token = await _mint(admin_client, "reader")
    admin_client.cookies.clear()
    r = await admin_client.post("/api/webhooks/hello-world",
                                headers={"Authorization": f"Bearer {token}"}, json={})
    assert r.status_code == 403   # reader can't trigger runs


async def test_webhook_unknown_agent_404(admin_client):
    r = await admin_client.post("/api/webhooks/ghost", json={})
    assert r.status_code == 404


async def test_webhook_undeclared_path_404(admin_client, tmp_agents):
    # the agent exists, but stops declaring the path -> the endpoint vanishes
    (tmp_agents / "hello-world" / "entrypoints.yaml").write_text("webhooks: []\n")
    r = await admin_client.post("/api/webhooks/hello-world", json={})
    assert r.status_code == 404


async def test_webhook_decoupled_path_routes_to_declaring_agent(admin_client, tmp_agents, producer):
    from agentplatform.events import TOPIC_RUN_INBOUND
    (tmp_agents / "hello-world" / "entrypoints.yaml").write_text(
        "webhooks:\n  - path: newsflash\n")
    r = await admin_client.post("/api/webhooks/newsflash", json={"k": 1})
    assert r.status_code == 202
    inbound = [p for p in producer.published if p[0] == TOPIC_RUN_INBOUND]
    assert inbound[-1][2]["agent"] == "hello-world"
