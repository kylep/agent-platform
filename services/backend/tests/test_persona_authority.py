"""Cross-boundary design-34 ownership, revocation and archive regression tests."""
from sqlalchemy import delete, func, select

from agentplatform.db import (AgentDef, AgentVersion, ChatIdentity, Conversation,
    RelayBinding, RelayMessage, RelaySession, Run, RunState, SchemaMark)
from agentplatform.authority import migrate_authority
from agentplatform.relay_store import enabled_agents
from agentplatform.relay import mentionable_in
from .test_relay_api import _agent_token


async def test_accounts_only_accept_enabled_persona_owners(admin_client, sf, seed_agent, agent_store):
    await seed_agent("worker", agent_type="worker")
    await seed_agent("sleeping", agent_type="persona", enabled=False)
    await seed_agent("companion", agent_type="persona")
    await agent_store.reload()
    for name in ("worker", "sleeping", "missing"):
        response = await admin_client.patch("/api/chat-identities/discord-default", json={
            "display_name": "Persona account", "owner_agent": name})
        assert response.status_code == 422, response.text
    response = await admin_client.patch("/api/chat-identities/discord-default", json={
        "display_name": "Persona account", "owner_agent": "companion"})
    assert response.status_code == 200, response.text
    async with sf() as s:
        account = await s.get(ChatIdentity, "discord-default")
        owner = await s.get(AgentDef, "companion")
        assert account.owner_agent == owner.name
        assert owner.discord_identity_id == account.id
        assert "mcp__platform__discord" in owner.platform_tools
    # An editorial type conversion cannot keep external authority.
    definition = (await admin_client.get("/api/agents/companion")).json()
    changed = await admin_client.put("/api/agents/companion", json={**definition, "agent_type": "worker"})
    assert changed.status_code in (409, 422), changed.text


async def test_worker_account_assignment_and_dynamic_token_bindings_refused(admin_client, sf, seed_agent, agent_store):
    await seed_agent("worker", agent_type="worker")
    await agent_store.reload()
    async with sf() as s:
        s.add(ChatIdentity(id="discord-private", connector="discord", display_name="Private",
            secret_refs={"bot_token": {"secret": "unusual-provider-token", "key": "token"}}))
        await s.commit()
    definition = (await admin_client.get("/api/agents/worker")).json()
    response = await admin_client.put("/api/agents/worker", json={
        **definition, "discord_identity_id": "discord-private"})
    assert response.status_code == 422, response.text
    response = await admin_client.put("/api/agents/worker", json={
        **definition, "secrets": ["unusual-provider-token"]})
    assert response.status_code == 422, response.text
    assert "credentials" in response.text.lower() or "secret" in response.text.lower()


async def test_owner_change_revokes_existing_run_api_token(admin_client, token_client, sf, seed_agent, agent_store):
    await seed_agent("companion", agent_type="persona")
    await agent_store.reload()
    assigned = await admin_client.patch("/api/chat-identities/discord-default", json={
        "display_name": "Persona account", "owner_agent": "companion"})
    assert assigned.status_code == 200
    async with sf() as s:
        owner = await s.get(AgentDef, "companion")
        run = Run(agent=owner.name, state=RunState.RUNNING, trigger="manual",
            requested_by="admin", prompt="test", authorization_generation=owner.authorization_generation)
        s.add(run)
        await s.commit()
        run_id = run.id
    headers = await _agent_token(sf, "companion", run_id=run_id)
    assert (await token_client.get("/api/whoami", headers=headers)).status_code == 200
    removed = await admin_client.patch("/api/chat-identities/discord-default", json={
        "display_name": "Persona account", "owner_agent": None})
    assert removed.status_code == 200
    assert (await token_client.get("/api/whoami", headers=headers)).status_code == 401


async def test_authority_migration_preserves_history_versions_and_is_idempotent(sf, seed_agent):
    await seed_agent("pai", agent_type="persona")
    async with sf() as s:
        await s.execute(delete(SchemaMark).where(SchemaMark.name == "persona-external-authority-v1"))
        conv = Conversation(kind="channel", home="external", connector="discord", name="old-chat", title="Historical")
        s.add(conv)
        await s.flush()
        msg = RelayMessage(channel_id=conv.id, author="agent:health-monitor", body="Historical alert", kind="text")
        s.add(msg)
        s.add(RelaySession(channel_id=conv.id, agent="pai", session_blob=b"old-context"))
        s.add(RelayBinding(channel_id=conv.id, connector="discord", external_ref="old-endpoint", status="active"))
        await s.commit()
        channel_id, message_id = conv.id, msg.id
        connection = await s.connection()
        await connection.run_sync(migrate_authority)
        await s.commit()
        first_versions = await s.scalar(select(func.count()).select_from(AgentVersion))
        connection = await s.connection()
        await connection.run_sync(migrate_authority)
        await s.commit()
        assert await s.scalar(select(func.count()).select_from(AgentVersion)) == first_versions
        assert (await s.get(RelayMessage, message_id)).body == "Historical alert"
        assert (await s.get(RelaySession, (channel_id, "pai"))).session_blob == b"old-context"
        assert (await s.get(ChatIdentity, "discord-default")).owner_agent == "pai"
        binding = (await s.execute(select(RelayBinding).where(RelayBinding.external_ref == "old-endpoint"))).scalar_one()
        assert binding.status == "disabled" and binding.channel_id == channel_id
        changes = (await s.execute(select(AgentVersion).where(
            AgentVersion.agent == "pai", AgentVersion.changed_via == "persona-authority-migration"))).scalars().all()
        assert changes and changes[-1].snapshot["discord_identity_id"] == "discord-default"


async def test_external_mirror_is_read_only_and_observer_never_summonable(
        admin_client, token_client, sf, seed_agent, agent_store, producer):
    from agentplatform.config import Settings
    from agentplatform.relay_router import RelayRouter
    from .test_relay_router import _say
    from agentplatform.db import RelayInvocation

    await seed_agent("ordinary", platform_tools=["mcp__platform__relay"])
    await seed_agent("observer", external_observer=True, platform_tools=["mcp__platform__relay"])
    await agent_store.reload()
    async with sf() as s:
        conv = Conversation(kind="channel", home="external", connector="discord", open=True,
            name="external-chat", title="External chat", dispatch_mode="mentions")
        s.add(conv)
        await s.commit()
        cid = conv.id
        roster = await enabled_agents(s)
        assert "observer" not in mentionable_in(conv, roster, {"agent:observer"})
    ordinary = await _agent_token(sf, "ordinary")
    observer = await _agent_token(sf, "observer")
    assert (await token_client.get(f"/api/relay/channels/{cid}", headers=ordinary)).status_code == 403
    readable = await token_client.get(f"/api/relay/channels/{cid}", headers=observer)
    assert readable.status_code == 200, readable.text
    for client, headers in ((admin_client, {}), (token_client, observer)):
        response = await client.post(f"/api/relay/channels/{cid}/messages", headers=headers,
            json={"body": "This must never cross to the provider."})
        assert response.status_code == 403, response.text
    router = RelayRouter(Settings(), sf, producer, agent_store)
    await _say(router, sf, cid, "discord:123", "@observer inspect this")
    async with sf() as s:
        invoked = (await s.execute(select(RelayInvocation).where(
            RelayInvocation.channel_id == cid, RelayInvocation.decision == "invoked"))).scalars().all()
        assert not invoked
