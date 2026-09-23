from sqlalchemy import select

from agentplatform.db import Conversation, Project, RelayParticipant, Team


async def test_team_owns_closed_relay_group_and_tracks_agent_roster(
        admin_client, sf, seed_agent):
    await seed_agent("alice")
    await seed_agent("bob")
    r = await admin_client.post("/api/teams", json={
        "slug": "rpg-dev-test", "name": "RPG dev/test", "agents": ["alice", "bob"]})
    assert r.status_code == 201, r.text
    team = r.json()
    assert team["agents"] == ["alice", "bob"]
    async with sf() as s:
        room = await s.get(Conversation, team["relay_channel_id"])
        assert room.kind == "group" and room.team_id == team["id"]
        people = set((await s.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == room.id))).scalars())
        assert people == {"user:admin", "agent:alice", "agent:bob"}
    r = await admin_client.patch("/api/teams/rpg-dev-test", json={"agents": ["bob"]})
    assert r.status_code == 200, r.text
    async with sf() as s:
        people = set((await s.execute(select(RelayParticipant.participant).where(
            RelayParticipant.channel_id == team["relay_channel_id"]))).scalars())
        assert people == {"user:admin", "agent:bob"}
    assert (await admin_client.post("/api/teams", json={
        "slug": "rpg-dev-test", "name": "Again"})).status_code == 409


async def test_project_membership_is_independent_of_team(admin_client, sf, seed_agent):
    await seed_agent("alice")
    await seed_agent("bob")
    team = (await admin_client.post("/api/teams", json={
        "slug": "platform-engineering", "name": "Platform engineering",
        "agents": ["alice"]})).json()
    r = await admin_client.post("/api/projects", json={
        "slug": "marathon", "name": "Marathon preparation",
        "team_slug": "platform-engineering", "agents": ["bob"]})
    assert r.status_code == 201, r.text
    project = r.json()
    assert project["team_slug"] == "platform-engineering"
    assert project["agents"] == ["bob"]
    async with sf() as s:
        row = await s.get(Project, project["id"])
        assert row.team_id == team["id"]
    r = await admin_client.patch("/api/projects/marathon", json={
        "team_slug": None, "agents": ["alice", "bob"], "archived": True})
    assert r.status_code == 200, r.text
    assert r.json()["team_slug"] is None and r.json()["archived"] is True
    assert r.json()["agents"] == ["alice", "bob"]


async def test_scopes_reject_unknown_agents_and_invalid_slugs(admin_client):
    assert (await admin_client.post("/api/teams", json={
        "slug": "Bad Slug", "name": "Bad"})).status_code == 422
    assert (await admin_client.post("/api/projects", json={
        "slug": "valid", "name": "Valid", "agents": ["missing"]})).status_code == 422
