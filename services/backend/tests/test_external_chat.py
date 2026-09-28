"""Authorization, canonical observations and ambiguous delivery contracts."""
from datetime import timedelta

import pytest
from sqlalchemy import select, func

from agentplatform import external_chat as chat
from agentplatform.db import AgentDef, ChatIdentity, RelayMessage, Run, utcnow


async def setup(sf):
    async with sf() as s:
        s.add_all([AgentDef(name="persona-a", agent_type="persona", enabled=True),
                   AgentDef(name="persona-b", agent_type="persona", enabled=True),
                   AgentDef(name="worker-a", agent_type="worker", enabled=True)])
        s.add_all([ChatIdentity(id="discord-a", connector="discord", display_name="A", owner_agent="persona-a", status="active"),
                   ChatIdentity(id="discord-b", connector="discord", display_name="B", owner_agent="persona-b", status="active")])
        await s.commit()
    for account in ("discord-a", "discord-b"):
        async with sf() as s:
            await chat.snapshot(s, account, 0, 1, [{"external_ref": "123", "can_read": True, "can_history": True, "can_send": True}])
            await s.commit()


@pytest.mark.asyncio
async def test_shared_canonical_message_preserves_addressed_observations(sf):
    await setup(sf)
    data = {"external_ref": "123", "provider_message_id": "456", "author_id": "human", "text": "hello", "addressed": False}
    async with sf() as s:
        ep, first, a = await chat.observe(s, "discord-a", 0, data)
        _, second, b = await chat.observe(s, "discord-b", 0, {**data, "addressed": True})
        _, replay, same = await chat.observe(s, "discord-b", 0, {**data, "addressed": True})
        assert first.id == second.id == replay.id
        assert a.id != b.id == same.id and b.addressed and not a.addressed
        assert await chat.can_read_channel(s, "persona-a", ep.channel_id)
        assert not await chat.can_read_channel(s, "worker-a", ep.channel_id)
        await s.commit()


@pytest.mark.asyncio
async def test_no_history_capability_cannot_inherit_shared_archive(sf):
    await setup(sf)
    async with sf() as s:
        await chat.snapshot(s, "discord-b", 0, 2, [{"external_ref": "123", "can_read": True, "can_history": False, "can_send": True}])
        ep = (await s.execute(select(chat.ExternalEndpoint))).scalar_one()
        assert not await chat.can_read_channel(s, "persona-b", ep.channel_id)
        assert await chat.can_read_channel(s, "persona-a", ep.channel_id)
        with pytest.raises(chat.ExternalChatError):
            await chat.observe(s, "discord-b", 0, {"external_ref": "123", "provider_message_id": "456", "author_id": "h", "text": "hi"})
        await s.commit()


async def queued(sf, text="hello"):
    await setup(sf)
    async with sf() as s:
        run = Run(id="f" * 32, agent="persona-a", prompt="test", trigger="api", requested_by="test", state="running", authorization_generation=0)
        s.add(run)
        await s.flush()
        row = await chat.queue_send(s, agent="persona-a", run_id=run.id, identity_id="discord-a", external_ref="123", text=text)
        await s.commit()
        return row.id


@pytest.mark.asyncio
async def test_claim_once_receipt_without_echo_mirrors_once(sf):
    request_id = await queued(sf)
    async with sf() as s:
        row, _ = await chat.claim_delivery(s, "discord-a", request_id)
        token = row.claim_token
        await s.commit()
    async with sf() as s:
        with pytest.raises(chat.ExternalChatError):
            await chat.claim_delivery(s, "discord-a", request_id)
        row, msg = await chat.receipt(s, "discord-a", request_id, token, 0, "sent-123")
        _, replay = await chat.receipt(s, "discord-a", request_id, token, 0, "sent-123")
        assert msg.id == replay.id and row.state == "accepted"
        assert await s.scalar(select(func.count()).select_from(RelayMessage).where(RelayMessage.run_id == "f" * 32)) == 1
        await s.commit()


@pytest.mark.asyncio
async def test_partial_delivery_is_unknown_and_not_reclaimed(sf):
    request_id = await queued(sf, "x" * 2000)
    async with sf() as s:
        row, _ = await chat.claim_delivery(s, "discord-a", request_id)
        await chat.receipt(s, "discord-a", request_id, row.claim_token, 0, "sent-first")
        row, _ = await chat.receipt(s, "discord-a", request_id, row.claim_token, None, outcome="failed")
        assert row.state == "unknown"
        with pytest.raises(chat.ExternalChatError):
            await chat.claim_delivery(s, "discord-a", request_id)
        await s.commit()


@pytest.mark.asyncio
async def test_refresh_cannot_resurrect_expired_run(sf):
    request_id = await queued(sf)
    async with sf() as s:
        account = await s.get(ChatIdentity, "discord-a")
        account.access_expires_at = utcnow() - timedelta(seconds=1)
        await s.commit()
    async with sf() as s:
        await chat.snapshot(s, "discord-a", 0, 2, [{"external_ref": "123", "can_read": True, "can_history": True, "can_send": True}])
        row, ep = await chat.claim_delivery(s, "discord-a", request_id)
        assert row.state == "denied" and ep is None
        assert (await s.get(AgentDef, "persona-a")).authorization_generation == 1
        await s.commit()


@pytest.mark.asyncio
async def test_wrong_owner_worker_and_stale_snapshot_refused(sf):
    await setup(sf)
    async with sf() as s:
        with pytest.raises(chat.ExternalChatError):
            await chat.owned_identity(s, "discord-a", "persona-b")
        with pytest.raises(chat.ExternalChatError):
            await chat.owned_identity(s, "discord-a", "worker-a")
        with pytest.raises(chat.ExternalChatError):
            await chat.snapshot(s, "discord-a", 0, 1, [])
        assert await s.scalar(select(func.count()).select_from(chat.ExternalAccess)) == 2


@pytest.mark.asyncio
async def test_tool_response_suppresses_final_but_unrelated_send_does_not(sf):
    await setup(sf)
    async with sf() as s:
        ep, msg, obs = await chat.observe(s, "discord-a", 0, {"external_ref": "123", "provider_message_id": "456", "author_id": "human", "text": "hello", "addressed": True})
        run = Run(id="e" * 32, agent="persona-a", prompt="test", trigger="relay", requested_by="test", state="running",
            conversation_id=ep.channel_id, trigger_message_id=msg.id, external_observation_id=obs.id, authorization_generation=0)
        s.add(run)
        await s.flush()
        unrelated = await chat.queue_send(s, agent=run.agent, run_id=run.id, identity_id="discord-a", external_ref="123", text="unrelated")
        response = await chat.queue_send(s, agent=run.agent, run_id=run.id, identity_id="discord-a", external_ref="123", text="answer", answer_to=msg.id)
        final = await chat.queue_final(s, run, "automatic answer")
        assert final.id == response.id != unrelated.id
        response.state = "unknown"
        assert (await chat.queue_final(s, run, "automatic answer")).id == response.id
        response.state = "failed"
        final = await chat.queue_final(s, run, "automatic answer")
        assert final.id not in (unrelated.id, response.id)
        await s.commit()


@pytest.mark.asyncio
async def test_connector_api_rejects_admin_and_derives_account(sf, admin_client, monkeypatch):
    await setup(sf)
    assert (await admin_client.get("/api/external-chat/connector/state")).status_code == 403
    from agentplatform.api import external_chat as api
    async def auth(request):
        return ("connector-discord:discord-a", "connector")
    monkeypatch.setattr(api, "authenticate", auth)
    state = await admin_client.get("/api/external-chat/connector/state")
    assert state.status_code == 200 and state.json()["identity_id"] == "discord-a"
    body = {"ownership_generation": 0, "external_ref": "123", "provider_message_id": "456", "author_id": "h", "text": "hello", "addressed": True, "identity_id": "discord-b"}
    response = await admin_client.post("/api/external-chat/connector/observe", json=body)
    assert response.status_code == 200
    async with sf() as s:
        obs = await s.get(chat.ExternalObservation, response.json()["observation_id"])
        assert obs.identity_id == "discord-a"


@pytest.mark.asyncio
async def test_expired_claim_poll_is_unknown_not_retryable(sf, admin_client, monkeypatch):
    request_id = await queued(sf)
    async with sf() as s:
        row, _ = await chat.claim_delivery(s, "discord-a", request_id)
        row.claimed_at = utcnow() - timedelta(seconds=chat.CLAIM_SECONDS + 1)
        await s.commit()
    from agentplatform.api import external_chat as api
    async def auth(request):
        return ("connector-discord:discord-a", "connector")
    monkeypatch.setattr(api, "authenticate", auth)
    assert (await admin_client.get("/api/external-chat/connector/deliveries")).json() == []
    async with sf() as s:
        assert (await s.get(chat.ExternalDelivery, request_id)).state == "unknown"
