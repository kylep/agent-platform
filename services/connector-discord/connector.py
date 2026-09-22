"""Discord connector: a thin, long-lived bridge between Discord and the
platform's conversation bus. It holds nothing stateful of its own — the
platform owns the rooms, the history and the routing.

Two shapes of room, one bus:

* **Threads** (the original flow). A mention of the bot, or a message in a
  thread it is active in, opens a thread and becomes a `conversation.message`
  on `conversation.inbound` with `external_ref=<thread id>`. The platform
  answers it as a DM turn and the reply comes back on `conversation.outbound`,
  posted by the bot itself.
* **Bound channels** (docs/design/19). A Discord text channel bound to a Relay
  channel is the SAME room: every human message in it is published inbound, so
  a plain-text `@news` routes exactly like an in-app mention, and every message
  written in the Relay channel is mirrored back out through a webhook whose
  `username` is the speaker — the humans see `news`, `pai` and `health-monitor`
  as distinct voices rather than one bot reading everyone's lines.

Which channels are bound comes from the platform (`GET /api/relay/bindings`),
polled, not from this process's environment: a binding is a row an operator
edits in the UI and it has to reach the bridge without a redeploy. Without
`AP_API_TOKEN` the connector cannot ask, so channel mirroring is simply off and
the thread flow runs alone.

Activates only when DISCORD_BOT_TOKEN is set (the deployment is gated off by
default). The envelope format matches agentplatform.events so the platform's
conversation-ingest consumer can unwrap it.
"""
import asyncio
import json
import logging
import os
import re
from pathlib import Path
import uuid
from datetime import datetime, timezone

import aiohttp
import discord
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

log = logging.getLogger("connector-discord")

TOPIC_IN = "conversation.inbound"
TOPIC_OUT = "conversation.outbound"
TOPIC_CHANNEL_POST = "discord.channel.post"
SCHEMA_VERSION = 1

# The bindings the platform says this bridge owns, re-read on a timer: a room
# bound (or unbound) in the UI takes effect within a minute, with no restart.
BINDINGS_PATH = "/api/relay/bindings"
BINDINGS_REFRESH_SECONDS = 60
# One webhook per bound channel. A bot can only ever post as itself, so a
# webhook is the only way each agent gets its own name in the member list; the
# fixed name is how this connector finds the one it made last time instead of
# creating a new one per restart.
WEBHOOK_NAME = "relay"
# Discord rejects a webhook username containing either of its own names, and
# caps one at 80 characters. An agent called `discord-watcher` would otherwise
# fail EVERY send with a 400 that the consume loop's catch-all swallows, so the
# room would just go quiet with no idea why.
RESERVED_IN_USERNAME = re.compile(r"clyde|discord", re.IGNORECASE)
USERNAME_LIMIT = 80


def _chunks(text: str, limit: int = 1990):
    """Split text into <=limit pieces, preferring newline boundaries (Discord
    caps a message at 2000 chars).

    A line longer than the limit is CARRIED ON rather than cut: a long URL, a
    base64 blob or a wrapped-off code line is exactly the kind of text a reader
    needs whole, and silently dropping its tail is a bug nobody sees until they
    follow a truncated link."""
    out, cur = [], ""
    for line in (text or "").split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            if cur:
                out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        out.append(cur)
    return out or [""]


def _envelope(type_: str, key: str, data: dict) -> bytes:
    return json.dumps({
        "type": type_, "schema_version": SCHEMA_VERSION, "id": uuid.uuid4().hex,
        "ts": datetime.now(timezone.utc).isoformat(), "key": key,
        "source": "connector:discord", "data": data,
    }).encode()


def _unwrap(raw: bytes) -> dict:
    v = json.loads(raw)
    return v.get("data", v) if isinstance(v, dict) else v


def _speaker(author: str) -> str:
    """The name a message is posted under in a bound channel. `agent:news` is
    `news` — that is the whole point of the webhook — a human posts under their
    principal, and anything the platform said in its own name (`system:relay`,
    a pause notice, a run that died) is Relay speaking.

    Sanitised to what Discord will actually accept, because the alternative is
    a send that fails: the reserved words come out, the result is trimmed to the
    80-character cap, and a name that sanitising empties falls back to Relay
    rather than to a 400."""
    kind, _, name = (author or "").partition(":")
    name = name if kind in ("agent", "user") else ""
    return RESERVED_IN_USERNAME.sub("", name).strip()[:USERNAME_LIMIT] or "Relay"


class DiscordConnector:
    def __init__(self):
        self.bootstrap = os.environ.get("AP_KAFKA_BOOTSTRAP", "ap-kafka:9092")
        self.agent = os.environ.get("CONNECTOR_AGENT", "echo")
        self.token = os.environ["DISCORD_BOT_TOKEN"]
        self.api_url = os.environ.get("AP_API_URL",
                                      "http://agent-platform-api:8000").rstrip("/")
        self.api_token = os.environ.get("AP_API_TOKEN", "")
        self.api_token_file = os.environ.get("AP_API_TOKEN_FILE", "")
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(intents=intents)
        self.producer: AIOKafkaProducer | None = None
        self._active_threads: set[int] = set()   # threads we've replied in
        # Discord channel id → its binding row. The map IS the answer to "is
        # this channel mirrored?", on both the inbound and the outbound side.
        self.bound: dict[int, dict] = {}
        # Assistant threads are endpoint bindings too, but are never channel
        # mirrors: they use bot replies rather than webhooks. Hydrating this at
        # startup is what makes an existing conversation survive a restart.
        self.threads: dict[int, dict] = {}
        self._webhooks: dict[int, object] = {}
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self):
        log.info("discord connector ready as %s", self.client.user)

    # --- bindings ------------------------------------------------------------

    def _api_bearer(self) -> str:
        if self.api_token:
            return self.api_token
        if not self.api_token_file:
            return ""
        try:
            return Path(self.api_token_file).read_text().strip()
        except OSError:
            log.warning("could not read AP_API_TOKEN_FILE", exc_info=True)
            return ""

    async def _fetch_bindings(self) -> list:
        headers = {"Authorization": f"Bearer {self._api_bearer()}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(self.api_url + BINDINGS_PATH,
                                   params={"connector": "discord"},
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                resp.raise_for_status()
                return await resp.json()

    def _set_bindings(self, rows) -> None:
        """Replace the map with what the platform just said. A ref that is not a
        Discord snowflake is skipped rather than guessed at: the thread flow's
        refs live in the same table, and an id this connector cannot resolve is
        a binding for somebody else's idea of a channel."""
        bound, threads = {}, {}
        for row in rows or []:
            ref = str((row or {}).get("external_ref") or "")
            if ref.isdigit():
                if (row or {}).get("external_kind") == "thread":
                    threads[int(ref)] = row
                else:
                    bound[int(ref)] = row
            else:
                log.warning("ignoring binding with a non-numeric external_ref %r", ref)
        if bound.keys() != self.bound.keys():
            log.info("mirroring %d discord channel(s): %s", len(bound), sorted(bound))
        self.bound = bound
        # Once a locally-created thread has appeared in the authoritative
        # endpoint list, stop remembering it independently. A later unbind
        # must make it inactive again rather than leave a stale process-local
        # exemption behind.
        self._active_threads.difference_update(set(self.threads) | set(threads))
        self.threads = threads
        # A channel we no longer mirror keeps no cached webhook: the next bind
        # of it should look the room up again rather than post through a handle
        # that may have been deleted in the meantime.
        for channel_id in [c for c in self._webhooks if c not in bound]:
            self._webhooks.pop(channel_id, None)

    async def refresh_bindings(self) -> None:
        """Re-read the bindings, keeping the last good map on failure: an API
        that is restarting must not silently stop a bridge that is working."""
        try:
            self._set_bindings(await self._fetch_bindings())
        except Exception:
            log.warning("could not refresh relay bindings", exc_info=True)

    async def bindings_loop(self) -> None:
        while True:
            await asyncio.sleep(BINDINGS_REFRESH_SECONDS)
            await self.refresh_bindings()

    # --- inbound -------------------------------------------------------------

    def _ignorable(self, message) -> bool:
        """Bots, webhooks and our own messages are not participants.

        Every message this bridge delivers to a bound channel arrives straight
        back through the gateway as a webhook message, so without this the
        connector would feed its own output into the platform — a loop that
        needs no agent's help to run forever."""
        return (self.client.user is None
                or message.author.id == self.client.user.id
                or getattr(message, "webhook_id", None) is not None
                or bool(getattr(message.author, "bot", False)))

    async def on_message(self, message: discord.Message):
        if self._ignorable(message):
            return
        if message.channel.id in self.bound:
            await self._publish_channel_message(message)
            return
        mentioned = self.client.user in message.mentions
        in_active_thread = (isinstance(message.channel, discord.Thread)
                            and (message.channel.id in self._active_threads
                                 or message.channel.id in self.threads))
        if not (mentioned or in_active_thread):
            return
        # Converse in a thread; create one off a channel mention so each
        # conversation maps to a stable external_ref (the thread id).
        if isinstance(message.channel, discord.Thread):
            thread = message.channel
        else:
            thread = await message.create_thread(name=f"chat-{message.id}")
        self._active_threads.add(thread.id)
        text = message.clean_content
        if self.client.user.name:
            text = text.replace(f"@{self.client.user.name}", "").strip()
        await self.producer.send_and_wait(TOPIC_IN, key=str(thread.id).encode(),
            value=_envelope("conversation.message", str(thread.id), {
                "connector": "discord", "external_ref": str(thread.id),
                "external_kind": "thread",
                "external_parent_ref": str(getattr(thread, "parent_id", "") or "") or None,
                "external_title": getattr(thread, "name", "") or f"Discord thread {thread.id}",
                "external_url": getattr(message, "jump_url", "") or "",
                "external_message_id": str(message.id),
                "external_user": str(message.author.id),
                "display_name": getattr(message.author, "display_name", message.author.name),
                "text": text, "agent": self.agent}))
        log.info("→ conversation.inbound thread=%s user=%s", thread.id, message.author.id)

    async def _publish_channel_message(self, message: discord.Message):
        """A message in a bound channel, published verbatim.

        The text is NOT rewritten: `@news` typed in Discord is the mention the
        platform's router parses, and clean_content has already resolved
        Discord's own `<@id>` markup to readable names. The user is identified
        by ID because that is what the platform's participant string is made of
        (`discord:<id>`) and a display name is a thing its owner can change."""
        ref = str(message.channel.id)
        await self.producer.send_and_wait(TOPIC_IN, key=ref.encode(),
            value=_envelope("conversation.message", ref, {
                "connector": "discord", "external_ref": ref,
                "external_kind": "channel",
                "external_title": getattr(message.channel, "name", "") or "",
                "external_url": getattr(message, "jump_url", "") or "",
                "external_message_id": str(message.id),
                "external_user": str(message.author.id),
                "display_name": getattr(message.author, "display_name", ""),
                "text": message.clean_content, "agent": self.agent}))
        log.info("→ conversation.inbound channel=%s user=%s", ref, message.author.id)

    # --- outbound ------------------------------------------------------------

    def _channel_by_name(self, name: str):
        """The first text channel named `name` across the bot's guilds."""
        for guild in self.client.guilds:
            ch = discord.utils.get(guild.text_channels, name=name)
            if ch is not None:
                return ch
        return None

    async def _channel_by_id(self, channel_id: int):
        return self.client.get_channel(channel_id) or await self.client.fetch_channel(channel_id)

    async def _webhook(self, channel):
        """The channel's `relay` webhook, made once and cached. Get-or-create
        rather than create: a restart must not leave a trail of webhooks behind
        it, and Discord caps how many a channel may have."""
        hook = self._webhooks.get(channel.id)
        if hook is None:
            hook = discord.utils.get(await channel.webhooks(), name=WEBHOOK_NAME)
            if hook is None:
                hook = await channel.create_webhook(name=WEBHOOK_NAME)
            self._webhooks[channel.id] = hook
        return hook

    async def _post_chunk(self, channel, text: str, username: str) -> bool:
        """Send one chunk through the channel's webhook, replacing the webhook
        once if Discord says it is gone.

        A webhook deleted in Discord is a 404 forever otherwise: the handle is
        cached, nothing evicts it, and every message to that room disappears
        into the consume loop's catch-all. So a NotFound drops the cached handle
        and the send is retried against a freshly made one — once. A second
        failure is a room we cannot post to, and saying so in the log beats
        retrying a broken channel for every message that follows."""
        for attempt in (1, 2):
            webhook = await self._webhook(channel)
            try:
                await webhook.send(text, username=username,
                                   allowed_mentions=discord.AllowedMentions.none())
                return True
            except discord.NotFound:
                self._webhooks.pop(channel.id, None)
                if attempt == 2:
                    log.warning("outbound: the relay webhook for channel %s keeps "
                                "vanishing — dropping this message", channel.id)
        return False

    async def _deliver_channel_message(self, data: dict):
        """Mirror one Relay message into its bound Discord channel, under the
        speaker's own name. Mentions are disabled on the way out: relayed text
        is written by agents and by people in another room, and neither is a
        licence to ping everyone here."""
        channel_id = int(data["external_ref"])
        channel = await self._channel_by_id(channel_id)
        if channel is None:
            log.warning("outbound: no bound channel %s the bot can see", channel_id)
            return
        username = _speaker(data.get("author") or "")
        for chunk in _chunks(data.get("text") or ""):
            if not await self._post_chunk(channel, chunk, username):
                return
        log.info("← posted as %s to channel=%s", username, channel_id)

    async def _deliver_thread_reply(self, data: dict):
        tid = int(data["external_ref"])
        channel = self.client.get_channel(tid) or await self.client.fetch_channel(tid)
        if channel is not None:
            for chunk in _chunks(data.get("text") or ""):
                await channel.send(chunk)
            log.info("← posted reply to thread=%s", tid)

    async def _deliver_outbound(self, data: dict):
        """One outbound message to whichever kind of room it names."""
        if data.get("connector") != "discord" or not data.get("external_ref"):
            return
        ref = str(data["external_ref"])
        if data.get("external_kind") == "channel" or (
                not data.get("external_kind") and ref.isdigit() and int(ref) in self.bound):
            await self._deliver_channel_message(data)
        else:
            await self._deliver_thread_reply(data)

    async def _deliver_channel_post(self, data: dict):
        """A platform broadcast (e.g. the news digest) to a named channel. The
        connector is the sole holder of the bot token; the text arrives already
        deduped + sanitized by the platform's news projector."""
        name, text = data.get("channel"), data.get("text")
        if not name or not text:
            return
        channel = self._channel_by_name(name)
        if channel is None:
            log.warning("channel.post: no channel named #%s the bot can see", name)
            return
        for chunk in _chunks(text):
            await channel.send(chunk)
        log.info("← posted %d message(s) to #%s", len(_chunks(text)), name)

    async def consume_outbound(self):
        await self.client.wait_until_ready()
        consumer = AIOKafkaConsumer(
            TOPIC_OUT, TOPIC_CHANNEL_POST, bootstrap_servers=self.bootstrap,
            group_id="connector-discord", auto_offset_reset="latest")
        await consumer.start()
        log.info("consuming conversation.outbound + discord.channel.post")
        try:
            async for msg in consumer:
                try:
                    data = _unwrap(msg.value)
                    if msg.topic == TOPIC_CHANNEL_POST:
                        await self._deliver_channel_post(data)
                    else:
                        await self._deliver_outbound(data)
                except Exception:
                    log.exception("failed to deliver outbound message")
        finally:
            await consumer.stop()

    async def run(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.bootstrap, enable_idempotence=True,
            acks="all", compression_type="gzip")
        await self.producer.start()
        if self._api_bearer():
            # Before the gateway connects: the map decides how the very first
            # message is handled, and a mirrored channel must not spend its
            # first minute being read as a bot mention.
            await self.refresh_bindings()
            asyncio.create_task(self.bindings_loop())
        else:
            log.warning("API identity is unavailable: binding recovery is off, only the "
                        "mention-the-bot thread flow runs")
        asyncio.create_task(self.consume_outbound())
        await self.client.start(self.token)


def main():
    logging.basicConfig(level=logging.INFO)
    asyncio.run(DiscordConnector().run())


if __name__ == "__main__":
    main()
