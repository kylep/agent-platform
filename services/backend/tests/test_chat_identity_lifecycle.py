"""Discord lifecycle effects and failure recovery; no real provider traffic."""
from unittest.mock import Mock

import httpx
import pytest
from kubernetes.client.exceptions import ApiException
from sqlalchemy import select

from agentplatform.db import AgentDef, AgentVersion, ChatIdentity, RelayBinding, init_db


def apps(admin_client, **methods):
    client = Mock(**methods)
    admin_client._transport.app.state.k8s_apps_v1 = client
    return client


async def test_edit_retains_routes_and_restarts_only_its_connector(admin_client, sf, secret_store):
    await secret_store.set("discord-bot", {"token": "old", "other": "preserved"})
    api = apps(admin_client)
    response = await admin_client.patch("/api/chat-identities/discord-default", json={
        "display_name": "New name", "token": "new-token"})
    assert response.status_code == 200
    assert "new-token" not in response.text
    assert await secret_store.get("discord-bot") == {"token": "new-token", "other": "preserved"}
    assert api.patch_namespaced_deployment.call_args.args[:2] == ("ap-connector-discord", "agent-platform")
    response = await admin_client.patch("/api/chat-identities/discord-default", json={"display_name": "Renamed"})
    assert response.status_code == 200
    assert api.patch_namespaced_deployment.call_count == 1
    assert (await admin_client.patch("/api/chat-identities/discord-default", json={"display_name": "  "})).status_code == 422


@pytest.mark.parametrize("status,flags,active", [(200, 1 << 19, True), (200, 0, False), (401, 0, False), (429, 0, True)])
async def test_verify_checks_token_intent_and_does_not_disable_on_rate_limit(
        admin_client, sf, secret_store, monkeypatch, status, flags, active):
    await secret_store.set("discord-bot", {"token": "test-token"})
    apps(admin_client, read_namespaced_deployment=Mock(side_effect=ApiException(status=404)))
    # Patch just the provider method after preserving the test client's get.
    real_get = httpx.AsyncClient.get
    async def get(self, url, **kwargs):
        if url.startswith("https://discord.com/"):
            assert kwargs["headers"]["Authorization"] == "Bot test-token"
            return httpx.Response(status, json={"flags": flags, "bot": {"username": "Example"}}, request=httpx.Request("GET", url))
        return await real_get(self, url, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, "get", get)
    response = await admin_client.post("/api/chat-identities/discord-default/verify")
    assert response.status_code == 200
    assert "test-token" not in response.text
    assert response.json()["checks"][-1]["detail"] == "Bot process has not been deployed yet."
    assert (await admin_client.get("/api/chat-identities/discord-default/transport")).json()["active"] is active


async def test_delete_detaches_agents_preserves_history_and_never_reseeds_default(
        admin_client, sf, secret_store, seed_agent, agent_store):
    await secret_store.set("discord-bot", {"token": "old"})
    await seed_agent("sender", discord_identity_id="discord-default")
    await agent_store.reload()
    async with sf() as session:
        session.add(RelayBinding(channel_id="history", connector="discord", identity_id="discord-default", external_ref="123"))
        await session.commit()
    api = apps(admin_client)
    result = await admin_client.delete("/api/chat-identities/discord-default")
    assert result.status_code == 200
    assert await secret_store.get("discord-bot") == {}
    assert api.patch_namespaced_deployment_scale.call_args.args[2] == {"spec": {"replicas": 0}}
    assert (await admin_client.get("/api/chat-identities")).json() == []
    assert (await admin_client.get("/api/chat-identities/discord-default/transport")).json()["active"] is False
    await init_db(sf.kw["bind"])
    async with sf() as session:
        assert (await session.get(ChatIdentity, "discord-default")).status == "deleted"
        assert (await session.get(AgentDef, "sender")).discord_identity_id is None
        version = (await session.execute(select(AgentVersion).where(AgentVersion.agent == "sender"))).scalar_one()
        assert version.snapshot["discord_identity_id"] is None
        binding = (await session.execute(select(RelayBinding).where(RelayBinding.external_ref == "123"))).scalar_one()
        assert binding.identity_id == "discord-default" and binding.status == "disabled"
    assert (await admin_client.patch("/api/chat-identities/discord-default", json={"display_name": "Resurrect"})).status_code == 404


async def test_delete_failed_secret_cleanup_is_visible_and_retryable(admin_client, sf, secret_store, monkeypatch):
    await secret_store.set("discord-bot", {"token": "old"})
    apps(admin_client)
    original = secret_store.set
    async def fail(name, data):
        raise RuntimeError("private diagnostics")
    monkeypatch.setattr(secret_store, "set", fail)
    response = await admin_client.delete("/api/chat-identities/discord-default")
    assert response.status_code == 503 and "private diagnostics" not in response.text
    assert (await admin_client.get("/api/chat-identities")).json()[0]["status"] == "deleting"
    assert (await admin_client.get("/api/chat-identities/discord-default/transport")).json()["active"] is False
    monkeypatch.setattr(secret_store, "set", original)
    assert (await admin_client.delete("/api/chat-identities/discord-default")).status_code == 200


async def test_delete_does_not_touch_an_unrelated_secret(admin_client, sf, secret_store):
    await secret_store.set("github-app", {"token": "keep"})
    async with sf() as session:
        row = await session.get(ChatIdentity, "discord-default")
        row.secret_refs = {"bot_token": {"secret": "github-app", "key": "token"}}
        await session.commit()
    assert (await admin_client.delete("/api/chat-identities/discord-default")).status_code == 409
    assert await secret_store.get("github-app") == {"token": "keep"}


async def test_lifecycle_actions_require_admin(token_client):
    path = "/api/chat-identities/discord-default"
    assert (await token_client.patch(path, json={"display_name": "Bot"})).status_code == 401
    assert (await token_client.post(path + "/verify")).status_code == 401
    assert (await token_client.delete(path)).status_code == 401


async def test_invalid_token_input_is_never_reflected(admin_client):
    secret = "private-token-" * 400
    response = await admin_client.patch("/api/chat-identities/discord-default", json={"display_name": "Bot", "token": secret})
    assert response.status_code == 422
    assert "private-token" not in response.text
