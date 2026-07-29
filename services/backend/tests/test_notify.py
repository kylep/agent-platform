async def test_notify_publishes_channel_post_and_defangs(admin_client, producer):
    r = await admin_client.post("/api/notify", json={"channel": "alerts", "text": "down! @everyone"})
    assert r.status_code == 200
    posts = [d for t, _, d in producer.published if t == "discord.channel.post"]
    assert len(posts) == 1
    assert posts[0]["channel"] == "alerts"
    assert "@everyone" not in posts[0]["text"] and "down!" in posts[0]["text"]
