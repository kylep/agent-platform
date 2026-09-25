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
    r = await admin_client.post("/api/notify", json={"channel_id": "not-an-id", "text": "status"})
    assert r.status_code == 422
