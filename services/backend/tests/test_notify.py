from sqlalchemy import select

from agentplatform.apikeys import hash_token
from agentplatform.db import ApiKey, Conversation, RelayBinding


async def test_notify_publishes_channel_post_and_defangs(admin_client, producer):
    r = await admin_client.post("/api/notify", json={"channel": "alerts", "text": "down! @everyone"})
    assert r.status_code == 200
    posts = [d for t, _, d in producer.published if t == "discord.channel.post"]
    assert len(posts) == 1
    assert posts[0]["channel"] == "alerts"
    assert posts[0]["identity_id"] == "discord-default"
    assert "@everyone" not in posts[0]["text"] and "down!" in posts[0]["text"]


async def test_notify_accepts_exact_channel_id_and_rejects_ambiguous_target(admin_client, producer):
    target = "123456789012345678"
    r = await admin_client.post("/api/notify", json={"channel_id": target, "text": "status"})
    assert r.status_code == 200
    posts = [d for t, _, d in producer.published if t == "discord.channel.post"]
    assert posts[-1] == {"channel_id": target, "identity_id": "discord-default", "text": "status"}
    r = await admin_client.post("/api/notify", json={
        "channel": "alerts", "channel_id": target, "text": "status"})
    assert r.status_code == 422


async def test_disabled_chat_identity_blocks_notification(admin_client, producer):
    changed = await admin_client.patch("/api/chat-identities/discord-default/status",
                                       json={"status": "disabled"})
    assert changed.status_code == 200
    result = await admin_client.post("/api/notify", json={"channel": "alerts", "text": "hello"})
    assert result.status_code == 409
    assert not [d for t, _, d in producer.published if t == "discord.channel.post"]
    r = await admin_client.post("/api/notify", json={"channel_id": "not-an-id", "text": "status"})
    assert r.status_code == 422


async def test_second_identity_send_requires_admin_active_account_and_bound_id(
        admin_client, token_client, producer, sf):
    target = "123456789012345678"
    store = admin_client._transport.app.state.secret_store
    await store.set("second-bot", {"token": "fake-test-token"})
    created = await admin_client.post("/api/chat-identities", json={
        "id": "discord-second", "display_name": "Second bot",
        "secret_name": "second-bot"})
    assert created.status_code == 201
    body = {"identity_id": "discord-second", "channel_id": target, "text": "hello"}
    assert (await admin_client.post("/api/notify", json=body)).status_code == 409
    assert (await admin_client.patch("/api/chat-identities/discord-second/status",
                                     json={"status": "active"})).status_code == 200
    assert (await admin_client.post("/api/notify", json=body)).status_code == 404
    assert (await admin_client.post("/api/notify", json={
        "identity_id": "discord-second", "channel": "alerts", "text": "hello"
    })).status_code == 422
    async with sf() as session:
        session.add(RelayBinding(channel_id="a" * 32, connector="discord",
                                 identity_id="discord-second", external_ref=target,
                                 external_kind="channel", status="active"))
        await session.commit()
    operator_token = "ap_second_identity_operator"
    async with sf() as session:
        session.add(ApiKey(name="second-operator", role="operator",
                           key_hash=hash_token(operator_token), prefix=operator_token[:10]))
        await session.commit()
    assert (await token_client.post("/api/notify", json=body, headers={
        "Authorization": f"Bearer {operator_token}"})).status_code == 403
    posted = await admin_client.post("/api/notify", json=body)
    assert posted.status_code == 200, posted.text
    messages = [d for t, _, d in producer.published if t == "discord.channel.post"]
    assert messages == [{"channel_id": target, "identity_id": "discord-second",
                         "text": "hello", "requested_by": "user:admin"}]


async def test_agent_can_send_as_second_identity_only_to_a_room_it_can_join(
        admin_client, token_client, producer, sf, seed_agent, agent_store):
    target = "223456789012345678"
    await admin_client._transport.app.state.secret_store.set(
        "second-bot", {"token": "fake-test-token"})
    await admin_client.post("/api/chat-identities", json={
        "id": "discord-second", "display_name": "Second bot",
        "secret_name": "second-bot"})
    await admin_client.patch("/api/chat-identities/discord-second/status",
                             json={"status": "active"})
    await seed_agent("sender", description="Sends to Discord",
                     platform_tools=["mcp__platform__discord_chat"],
                     discord_identity_id="discord-second")
    await agent_store.reload()
    async with sf() as session:
        room = (await session.execute(select(Conversation).where(
            Conversation.name == "general"))).scalar_one()
        session.add(RelayBinding(channel_id=room.id, connector="discord",
                                 identity_id="discord-second", external_ref=target,
                                 external_kind="channel", status="active"))
        await session.commit()
    agent_token = "ap_second_identity_agent"
    async with sf() as session:
        session.add(ApiKey(name="sender-run", role="tools", agent="sender",
                           key_hash=hash_token(agent_token), prefix=agent_token[:10]))
        await session.commit()
    body = {"identity_id": "discord-second", "channel_id": target, "text": "hello"}
    assert (await token_client.post("/api/notify", json={
        "channel": "alerts", "text": "wrong bot"}, headers={
        "Authorization": f"Bearer {agent_token}"})).status_code == 403
    assert (await token_client.get(
        "/api/chat-identities/discord-default/transport", headers={
            "Authorization": f"Bearer {agent_token}"})).status_code == 403
    sent = await token_client.post("/api/notify", json=body, headers={
        "Authorization": f"Bearer {agent_token}"})
    assert sent.status_code == 200, sent.text
    assert [d for t, _, d in producer.published if t == "discord.channel.post"] == [
        {"identity_id": "discord-second", "channel_id": target,
         "text": "hello", "requested_by": "agent:sender"}]
    private_target = "323456789012345678"
    private = await admin_client.post("/api/relay/channels", json={
        "kind": "group", "participants": ["user:admin"]})
    assert private.status_code == 201, private.text
    async with sf() as session:
        session.add(RelayBinding(channel_id=private.json()["id"], connector="discord",
                                 identity_id="discord-second", external_ref=private_target,
                                 external_kind="channel", status="active"))
        await session.commit()
    denied = await token_client.post("/api/notify", json={
        **body, "channel_id": private_target}, headers={
        "Authorization": f"Bearer {agent_token}"})
    assert denied.status_code == 403
