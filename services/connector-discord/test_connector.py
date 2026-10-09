"""Transport boundary tests: account observations, claims and ambiguous effects."""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def module(monkeypatch):
    stub = types.ModuleType("discord")
    stub.Intents = types.SimpleNamespace(default=lambda: types.SimpleNamespace(message_content=False))
    class Client:
        def __init__(self, **kwargs):
            self.user = types.SimpleNamespace(id=99, bot=True)
            self.guilds = []
        def event(self, fn):
            return fn
        def is_ready(self):
            return True
    stub.Client = Client
    stub.DMChannel = type("DMChannel", (), {})
    stub.TextChannel = type("TextChannel", (), {})
    stub.Thread = type("Thread", (), {})
    stub.Forbidden = type("Forbidden", (Exception,), {})
    stub.NotFound = type("NotFound", (Exception,), {})
    stub.AllowedMentions = types.SimpleNamespace(none=lambda: "no-mentions")
    monkeypatch.setitem(sys.modules, "discord", stub)
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "test")
    spec = importlib.util.spec_from_file_location("discord_transport_test", Path(__file__).with_name("connector.py"))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def test_live_observation_addresses_bot_not_plain_text(module):
    async def run():
        c = module.DiscordConnector()
        c.ready, c.generation = True, 4
        c._api = AsyncMock(return_value={})
        c._permissions = lambda channel: {"can_read": True, "can_history": True, "can_send": True}
        message = types.SimpleNamespace(id=6, channel=types.SimpleNamespace(id=5),
            author=types.SimpleNamespace(id=7, bot=False), mentions=[], clean_content="@pai hi", webhook_id=None)
        await c.on_message(message)
        assert c._api.call_args.args[2]["addressed"] is False
        assert c._api.call_args.args[2]["ownership_generation"] == 4
        message.mentions = [c.client.user]
        await c.on_message(message)
        assert c._api.call_args.args[2]["addressed"] is True
        other_bot = types.SimpleNamespace(id=100, bot=True, name="Olu", display_name="Olu")
        message.mentions = [c.client.user, other_bot]
        message.content = "<@99> then <@100>"
        await c.on_message(message)
        assert c._api.call_args.args[2]["co_mentioned"] == ["Olu"]
        assert c._api.call_args.args[2]["mentioned_bot_ids"] == ["99", "100"]
        assert c._api.call_args.args[2]["author_bot"] is False
    asyncio.run(run())


def test_history_permission_required(module):
    async def run():
        c = module.DiscordConnector()
        c.ready, c.generation = True, 1
        c._api = AsyncMock()
        c._permissions = lambda channel: {"can_read": True, "can_history": False, "can_send": True}
        await c.on_message(types.SimpleNamespace(id=6, channel=types.SimpleNamespace(id=5),
            author=types.SimpleNamespace(id=7, bot=False), mentions=[c.client.user], clean_content="hi", webhook_id=None))
        c._api.assert_not_called()
    asyncio.run(run())


def test_accepted_send_records_receipt_without_gateway_echo(module):
    async def run():
        c = module.DiscordConnector()
        channel = types.SimpleNamespace(id=123, send=AsyncMock(return_value=types.SimpleNamespace(id=456)))
        c._channel_by_id = AsyncMock(return_value=channel)
        c.client.fetch_channel = AsyncMock(return_value=channel)
        c._permissions = lambda ch: {"can_read": True, "can_history": True, "can_send": True}
        c._api = AsyncMock(side_effect=[{"state": "claimed", "claim_token": "token", "external_ref": "123", "chunks": ["hello"]}, {}, {}])
        await c._deliver({"id": "request"})
        assert c._api.call_args.args == ("POST", "/deliveries/request/receipt", {"claim_token": "token", "index": 0, "provider_message_id": "456"})
        channel.send.assert_awaited_once_with("hello", allowed_mentions="no-mentions")
    asyncio.run(run())


def test_timeout_is_unknown_never_retries_send(module):
    async def run():
        c = module.DiscordConnector()
        channel = types.SimpleNamespace(id=123, send=AsyncMock(side_effect=TimeoutError()))
        c._channel_by_id = AsyncMock(return_value=channel)
        c.client.fetch_channel = AsyncMock(return_value=channel)
        c._permissions = lambda ch: {"can_read": True, "can_history": True, "can_send": True}
        c._api = AsyncMock(side_effect=[{"state": "claimed", "claim_token": "token", "external_ref": "123", "chunks": ["hello"]}, {}, {}])
        await c._deliver({"id": "request"})
        channel.send.assert_awaited_once()
        assert c._api.call_args.args[2]["outcome"] == "unknown"
    asyncio.run(run())


def test_failed_inventory_stays_unavailable(module):
    async def run():
        c = module.DiscordConnector()
        c.ready = True
        c._api = AsyncMock(side_effect=TimeoutError())
        await c.refresh()
        assert not c.ready
    asyncio.run(run())


def test_permission_scan_uses_rest_not_stale_gateway_cache(module):
    async def run():
        c = module.DiscordConnector()
        fresh_guild = types.SimpleNamespace(id=1, name="Family")
        channel = module.discord.TextChannel()
        channel.id, channel.guild, channel.name = 2, fresh_guild, "room"
        member = object()
        channel.permissions_for = lambda who: types.SimpleNamespace(view_channel=False, read_message_history=False, send_messages=False) if who is member else None
        fresh_guild.fetch_member = AsyncMock(return_value=member)
        fresh_guild.fetch_channels = AsyncMock(return_value=[channel])
        fresh_guild.active_threads = AsyncMock(return_value=[])
        c.client.fetch_guild = AsyncMock(return_value=fresh_guild)
        async def guilds(**kwargs):
            yield types.SimpleNamespace(id=1)
        c.client.fetch_guilds = guilds
        # Gateway still claims access; successful REST snapshot must revoke it.
        c.client.guilds = [types.SimpleNamespace(id=1, text_channels=["stale-readable-channel"], threads=[])]
        c._api = AsyncMock(side_effect=[{"active": True, "ownership_generation": 2, "permission_sequence": 8}, {}])
        await c.refresh()
        assert c.ready
        snapshot = c._api.call_args.args[2]
        assert snapshot["endpoints"][0]["display_name"] == "Family / room"
        assert snapshot["endpoints"][0]["can_read"] is False
        assert snapshot["endpoints"][0]["can_history"] is False
        c.client.fetch_guild.assert_awaited_once_with(1)
        fresh_guild.fetch_member.assert_awaited_once_with(99)
    asyncio.run(run())


def test_private_thread_requires_own_rest_membership(module):
    c = module.DiscordConnector()
    guild = types.SimpleNamespace(id=1)
    member = object()
    c._verified_members[1] = member
    parent = types.SimpleNamespace(id=2, guild=guild, permissions_for=lambda me: types.SimpleNamespace(
        view_channel=True, read_message_history=True, send_messages_in_threads=True, manage_threads=False))
    thread = module.discord.Thread()
    thread.id, thread.parent_id, thread.guild = 3, 2, guild
    thread.locked, thread.archived, thread.me = False, False, None
    thread.is_private = lambda: True
    thread.get_member = lambda user_id: None
    c._verified_channels = {2: parent, 3: thread}
    assert not c._permissions(thread)["can_read"]
    thread.get_member = lambda user_id: object() if user_id == 99 else None
    assert c._permissions(thread)["can_read"]


def test_a_reply_ping_reports_the_pinged_bot_without_a_written_mention(module):
    DiscordConnector = module.DiscordConnector
    pinged = types.SimpleNamespace(id=99, bot=True)
    human = types.SimpleNamespace(id=7, bot=False)
    message = types.SimpleNamespace(content="sounds good", mentions=[pinged, human])
    assert DiscordConnector._mentioned_bot_ids(message) == ["99"]
    written = types.SimpleNamespace(content="<@100> and you", mentions=[pinged, types.SimpleNamespace(id=100, bot=True)])
    assert DiscordConnector._mentioned_bot_ids(written) == ["100", "99"]
    assert DiscordConnector._mentioned_bot_ids(types.SimpleNamespace(content="hi", mentions=[])) == []
