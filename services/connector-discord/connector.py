"""Discord account transport. The API owns permissions, routing and receipts.

No Relay mirroring, webhooks, default-agent fallback, or legacy Kafka effects.
Each send is an API-created request claimed once by this authenticated account.
"""
import asyncio
import logging
import os
import re
from pathlib import Path

import aiohttp
import discord

log = logging.getLogger("connector-discord")
BASE = "/api/external-chat/connector"
REFRESH_SECONDS = 60
POLL_SECONDS = 2


class DiscordConnector:
    def __init__(self):
        self.identity_id = os.environ.get("AP_CHAT_IDENTITY", "discord-default")
        self.token = os.environ["DISCORD_BOT_TOKEN"]
        self.api_url = os.environ.get("AP_API_URL", "http://agent-platform-api:8000").rstrip("/")
        self.api_token = os.environ.get("AP_API_TOKEN", "")
        self.api_token_file = os.environ.get("AP_API_TOKEN_FILE", "")
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.generation = None
        self.ready = False
        self._known_dm = {}
        self._verified_members = {}
        self._verified_channels = {}
        self._refresh_lock = asyncio.Lock()
        for event in (self.on_ready, self.on_disconnect, self.on_message,
                      self.on_guild_channel_update, self.on_guild_channel_delete,
                      self.on_guild_role_update, self.on_guild_role_delete,
                      self.on_thread_update, self.on_thread_delete,
                      self.on_thread_join, self.on_thread_remove,
                      self.on_guild_join, self.on_guild_remove, self.on_member_update):
            self.client.event(event)

    def _api_bearer(self):
        if self.api_token:
            return self.api_token
        try:
            return Path(self.api_token_file).read_text().strip() if self.api_token_file else ""
        except OSError:
            return ""

    async def _api(self, method, path, data=None):
        bearer = self._api_bearer()
        if not bearer:
            raise RuntimeError("connector API identity unavailable")
        async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {bearer}"},
                                         timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.request(method, self.api_url + BASE + path, json=data) as response:
                response.raise_for_status()
                return await response.json()

    def _permissions(self, channel):
        channel = self._verified_channels.get(channel.id, channel)
        if isinstance(channel, discord.DMChannel):
            # Only DMs received by this exact bot are added to known_dm.
            return {"can_read": True, "can_history": True, "can_send": True}
        guild = getattr(channel, "guild", None)
        me = self._verified_members.get(getattr(guild, "id", None))
        if me is None:
            return {"can_read": False, "can_history": False, "can_send": False}
        # Thread permissions inherit from a freshly fetched parent, but
        # private membership is checked separately on the thread itself.
        permission_channel = self._verified_channels.get(channel.parent_id) if isinstance(channel, discord.Thread) else channel
        if permission_channel is None:
            return {"can_read": False, "can_history": False, "can_send": False}
        permissions = permission_channel.permissions_for(me)
        readable = bool(permissions.view_channel)
        if isinstance(channel, discord.Thread) and channel.is_private():
            joined = channel.me is not None or channel.get_member(self.client.user.id) is not None
            readable = readable and (joined or permissions.manage_threads)
        writable = permissions.send_messages_in_threads if isinstance(channel, discord.Thread) else permissions.send_messages
        if isinstance(channel, discord.Thread) and (channel.locked or channel.archived):
            writable = False  # This transport does not implicitly unarchive.
        return {"can_read": readable, "can_history": readable and bool(permissions.read_message_history),
                "can_send": readable and bool(writable)}

    def _endpoint(self, channel):
        kind = "dm" if isinstance(channel, discord.DMChannel) else "thread" if isinstance(channel, discord.Thread) else "channel"
        name = str(getattr(channel, "name", "") or f"Discord {kind} {channel.id}")
        guild = getattr(channel, "guild", None)
        if guild is not None:
            name = f"{guild.name} / {name}"
        return {"external_ref": str(channel.id), "kind": kind,
                "display_name": name,
                **self._permissions(channel)}

    async def _fresh_guild(self, guild_id):
        # fetch_guild constructs an isolated Guild from REST, including its
        # current roles. fetch_member supplies current bot role membership;
        # fetch_channels supplies current permission overwrites. No gateway
        # cache (or private discord.py cache mutation) renews an access lease.
        guild = await self.client.fetch_guild(guild_id)
        member = await guild.fetch_member(self.client.user.id)
        channels = await guild.fetch_channels()
        threads = await guild.active_threads()
        self._verified_members[guild.id] = member
        for key, value in list(self._verified_channels.items()):
            if getattr(getattr(value, "guild", None), "id", None) == guild.id:
                del self._verified_channels[key]
        text_channels = [c for c in channels if isinstance(c, discord.TextChannel)]
        for channel in [*text_channels, *threads]:
            self._verified_channels[channel.id] = channel
        return [*text_channels, *threads]

    async def refresh(self):
        async with self._refresh_lock:
            try:
                state = await self._api("GET", "/state")
                if not state["active"] or not self.client.is_ready():
                    self.ready = False
                    return
                endpoints = {}
                async for summary in self.client.fetch_guilds(limit=None):
                    try:
                        channels = await self._fresh_guild(summary.id)
                    except (discord.Forbidden, discord.NotFound):
                        # A guild removed during enumeration contributes no
                        # permissions; all prior endpoints are revoked.
                        continue
                    for channel in channels:
                        endpoints[channel.id] = self._endpoint(channel)
                # DMs are not enumerable at the provider. The platform keeps
                # observed endpoint IDs so restart does not erase discovery.
                dm_refs = set(state.get("known_dm_refs", [])) | {str(k) for k in self._known_dm}
                for ref in dm_refs:
                    try:
                        channel = await self.client.fetch_channel(int(ref))
                    except (discord.Forbidden, discord.NotFound):
                        self._known_dm.pop(int(ref), None)
                        continue
                    if isinstance(channel, discord.DMChannel):
                        self._known_dm[channel.id] = channel
                        endpoints[channel.id] = self._endpoint(channel)
                await self._api("POST", "/snapshot", {
                    "ownership_generation": state["ownership_generation"],
                    "sequence": state["permission_sequence"] + 1,
                    "provider_user_id": str(self.client.user.id),
                    "endpoints": list(endpoints.values())})
                self.generation = state["ownership_generation"]
                self.ready = True
            except Exception:
                self.ready = False
                log.warning("permission inventory refresh failed; account unavailable", exc_info=True)

    async def on_ready(self):
        log.info("Discord account connected as %s", self.client.user)
        # Reconnect is an authority boundary even if disconnect reporting failed.
        await self._invalidate()
        await self.refresh()

    async def _invalidate(self):
        self.ready = False
        try:
            await self._api("POST", "/disconnect", {})
        except Exception:
            log.warning("could not publish immediate revocation; lease will expire", exc_info=True)

    async def on_disconnect(self):
        await self._invalidate()

    async def _permissions_changed(self):
        await self._invalidate()
        await self.refresh()

    async def on_guild_channel_update(self, before, after):
        await self._permissions_changed()

    async def on_guild_channel_delete(self, channel):
        await self._permissions_changed()

    async def on_guild_role_update(self, before, after):
        await self._permissions_changed()

    async def on_guild_role_delete(self, role):
        await self._permissions_changed()

    async def on_thread_update(self, before, after):
        await self._permissions_changed()

    async def on_thread_delete(self, thread):
        await self._permissions_changed()

    async def on_thread_join(self, thread):
        await self._permissions_changed()

    async def on_thread_remove(self, thread):
        await self._permissions_changed()

    async def on_guild_join(self, guild):
        await self._permissions_changed()

    async def on_guild_remove(self, guild):
        await self._permissions_changed()

    async def on_member_update(self, before, after):
        if self.client.user and after.id == self.client.user.id:
            await self._permissions_changed()

    async def on_message(self, message):
        if self.client.user is None or message.author.id == self.client.user.id:
            return
        if isinstance(message.channel, discord.DMChannel):
            if message.channel.id not in self._known_dm:
                self._known_dm[message.channel.id] = message.channel
                await self.refresh()
        if not self.ready:
            return
        permissions = self._permissions(message.channel)
        if not permissions["can_read"] or not permissions["can_history"]:
            return
        mentioned = self.client.user in message.mentions
        addressed = (not getattr(message.author, "bot", False) and not getattr(message, "webhook_id", None)) and (
            mentioned or isinstance(message.channel, discord.DMChannel) or (
            isinstance(message.channel, discord.Thread) and message.channel.owner_id == self.client.user.id))
        payload = {"ownership_generation": self.generation,
                   "external_ref": str(message.channel.id), "provider_message_id": str(message.id),
                   "author_id": str(message.author.id), "text": message.clean_content,
                   "addressed": addressed,
                   "author_bot": bool(getattr(message.author, "bot", False) or
                                      getattr(message, "webhook_id", None)),
                   "mentioned_bot_ids": [bot_id for bot_id in dict.fromkeys(
                       re.findall(r"<@!?(\d+)>", getattr(message, "content", "") or ""))
                       if any(str(user.id) == bot_id and getattr(user, "bot", False)
                              for user in message.mentions)][:16],
                   "co_mentioned": [str(getattr(user, "display_name", user.name))[:80]
                                    for user in message.mentions
                                    if getattr(user, "bot", False) and user.id != self.client.user.id][:8]}
        # API deduplicates both canonical message and account observation.
        for attempt in range(3):
            try:
                await self._api("POST", "/observe", payload)
                return
            except Exception:
                if attempt == 2:
                    log.exception("could not persist Discord observation %s", message.id)
                else:
                    await asyncio.sleep(1 + attempt)

    async def _channel_by_id(self, channel_id):
        return self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)

    async def _deliver(self, item):
        claimed = await self._api("POST", f"/deliveries/{item['id']}/claim", {})
        if claimed.get("state") != "claimed":
            return
        path = f"/deliveries/{item['id']}"
        token = claimed["claim_token"]
        attempted = False
        accepted = False
        try:
            channel = await self._channel_by_id(int(claimed["external_ref"]))
            for index, chunk in enumerate(claimed["chunks"]):
                await self._api("POST", path + "/authorize", {"claim_token": token})
                # REST-fetch the endpoint immediately before effect; current
                # gateway role/overwrite state supplies action permissions.
                channel = await self.client.fetch_channel(channel.id)
                if getattr(channel, "guild", None) is not None:
                    await self._fresh_guild(channel.guild.id)
                    if channel.id not in self._verified_channels:
                        raise PermissionError("endpoint is no longer accessible")
                permissions = self._permissions(channel)
                if not all(permissions.values()):
                    raise PermissionError("provider endpoint permission denied")
                attempted = True
                msg = await channel.send(chunk, allowed_mentions=discord.AllowedMentions.none())
                accepted = True
                await self._api("POST", path + "/receipt", {"claim_token": token,
                    "index": index, "provider_message_id": str(msg.id)})
                attempted = False
        except (discord.Forbidden, discord.NotFound, PermissionError):
            outcome = "unknown" if accepted else "failed"
            await self._api("POST", path + "/receipt", {"claim_token": token, "outcome": outcome})
        except Exception:
            # An API receipt failure after send is ambiguous as well. No retry
            # of the provider effect, even after this process restarts.
            outcome = "unknown" if attempted or accepted else "failed"
            try:
                await self._api("POST", path + "/receipt", {"claim_token": token, "outcome": outcome})
            except Exception:
                log.exception("delivery receipt unavailable; claimed attempt expires to unknown")

    async def deliveries_loop(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            if self.ready:
                try:
                    for item in await self._api("GET", "/deliveries"):
                        await self._deliver(item)
                except Exception:
                    log.warning("delivery poll failed", exc_info=True)
            await asyncio.sleep(POLL_SECONDS)

    async def inventory_loop(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            await asyncio.sleep(REFRESH_SECONDS)
            await self.refresh()

    async def run(self):
        async with self.client:
            tasks = [asyncio.create_task(self.deliveries_loop()), asyncio.create_task(self.inventory_loop())]
            try:
                await self.client.start(self.token)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(DiscordConnector().run())


if __name__ == "__main__":
    main()
