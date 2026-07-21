from agentplatform.db import Conversation, Run, RunState


async def test_integrations_missing_then_configured(admin_client, secret_store):
    by = {r["name"]: r for r in (await admin_client.get("/api/integrations")).json()}
    assert by["Discord"]["configured"] is False and by["Discord"]["status"] == "missing"
    assert by["Discord"]["secrets"] == ["discord-bot"]
    await secret_store.set("discord-bot", {"token": "x"})
    by = {r["name"]: r for r in (await admin_client.get("/api/integrations")).json()}
    assert by["Discord"]["configured"] is True and by["Discord"]["status"] == "configured"


async def test_integration_working_with_recent_activity(admin_client, secret_store, sf):
    await secret_store.set("discord-bot", {"token": "x"})
    async with sf() as s:
        conv = Conversation(connector="discord", agent="echo")
        s.add(conv); await s.flush()
        s.add(Run(agent="echo", trigger="conversation", requested_by="c", prompt="p",
                  conversation_id=conv.id, state=RunState.SUCCEEDED))
        await s.commit()
    by = {r["name"]: r for r in (await admin_client.get("/api/integrations")).json()}
    assert by["Discord"]["status"] == "working"


async def test_integrations_admin_only(client):
    assert (await client.get("/api/integrations")).status_code == 401
