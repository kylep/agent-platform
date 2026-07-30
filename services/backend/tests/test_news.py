"""The news approval gate API: approve posts + records dedup, reject drops
without recording, decisions are one-shot, and RBAC is enforced."""
from sqlalchemy import select

from agentplatform.apikeys import generate_token, hash_token, token_prefix
from agentplatform.db import ApiKey, PendingNews, SharedNews

ITEMS = [{"url": "https://a.example/1", "headline": "H1", "section": "AI industry", "why": "x"},
         {"url": "https://b.example/2", "headline": "H2", "section": "Security", "why": "y"}]


async def _seed(sf, status="pending") -> str:
    async with sf() as s:
        p = PendingNews(channel="news", date="2026-07-30", post_text="**📰 News**\n• H1\n• H2",
                        items=ITEMS, status=status)
        s.add(p); await s.commit(); return p.id


async def _mint(sf, role) -> str:
    token = generate_token()
    async with sf() as s:
        s.add(ApiKey(name=f"k:{role}", role=role, key_hash=hash_token(token),
                     prefix=token_prefix(token)))
        await s.commit()
    return token


async def test_list_pending(admin_client, sf):
    await _seed(sf)
    r = await admin_client.get("/api/news/pending")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1 and body[0]["item_count"] == 2 and body[0]["status"] == "pending"


async def test_approve_posts_and_records(admin_client, sf, producer):
    nid = await _seed(sf)
    r = await admin_client.post(f"/api/news/pending/{nid}/approve")
    assert r.status_code == 200
    posts = [d for t, _, d in producer.published if t == "discord.channel.post"]
    assert len(posts) == 1 and posts[0]["channel"] == "news" and "H1" in posts[0]["text"]
    async with sf() as s:
        assert (await s.get(PendingNews, nid)).status == "approved"
        shared = {u for u in (await s.execute(select(SharedNews.url))).scalars()}
        assert shared == {"https://a.example/1", "https://b.example/2"}


async def test_reject_drops_without_recording(admin_client, sf, producer):
    """A reject posts nothing and records no dedup, so the stories can resurface."""
    nid = await _seed(sf)
    r = await admin_client.post(f"/api/news/pending/{nid}/reject")
    assert r.status_code == 200
    assert [d for t, _, d in producer.published if t == "discord.channel.post"] == []
    async with sf() as s:
        assert (await s.get(PendingNews, nid)).status == "rejected"
        assert (await s.execute(select(SharedNews))).scalars().all() == []


async def test_decision_is_one_shot(admin_client, sf):
    nid = await _seed(sf)
    assert (await admin_client.post(f"/api/news/pending/{nid}/approve")).status_code == 200
    # second decision (either way) is refused
    assert (await admin_client.post(f"/api/news/pending/{nid}/approve")).status_code == 409
    assert (await admin_client.post(f"/api/news/pending/{nid}/reject")).status_code == 409


async def test_unknown_id_404(admin_client):
    assert (await admin_client.post("/api/news/pending/nope/approve")).status_code == 404


async def test_reader_cannot_decide(client, sf):
    """A reader key may list but not approve/reject (ANNOTATE_ROLES)."""
    nid = await _seed(sf)
    tok = await _mint(sf, "reader")
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/api/news/pending", headers=h)).status_code == 200
    assert (await client.post(f"/api/news/pending/{nid}/approve", headers=h)).status_code == 403
