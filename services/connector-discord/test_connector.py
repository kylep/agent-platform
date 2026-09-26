"""The Discord bridge (docs/design/19 T10): which Discord messages become
platform messages, and how a platform message gets back out.

`discord` and `aiokafka` are stubbed rather than installed, so this runs under
the backend's venv with no gateway, no broker and no network:

    cd services/backend && .venv/bin/python -m pytest -q ../connector-discord

What is worth pinning here is exactly the seam: the envelope the platform
receives, the loop guards on both sides of it, and the fact that each agent
speaks under its own name in a bound channel. Tests are synchronous and drive
the coroutines with `asyncio.run`, so the file needs no asyncio plugin
configuration of its own."""
import asyncio
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


def _stub_discord():
    """Stand in for discord.py. Only the handful of names connector.py touches:
    the Client it drives, the two classes it does `isinstance`/annotation work
    with, `utils.get` and the AllowedMentions it sends with."""
    discord = types.ModuleType("discord")

    class Intents:
        def __init__(self):
            self.message_content = False

        @staticmethod
        def default():
            return Intents()

    class Client:
        def __init__(self, intents=None):
            self.intents = intents
            self.user = None
            self.guilds = []
            self.events = []
            self._channels = {}

        def event(self, fn):
            self.events.append(fn)
            return fn

        def get_channel(self, channel_id):
            return self._channels.get(channel_id)

        async def fetch_channel(self, channel_id):
            return self._channels.get(channel_id)

    class Message:
        pass

    class Thread:
        pass

    class ChannelType:
        text = 0

    class HTTPException(Exception):
        pass

    class NotFound(HTTPException):
        """What Discord raises for a webhook that has been deleted — the one
        failure the bridge has to recover from rather than log."""

    class AllowedMentions:
        def __init__(self, none=False):
            self.none_ = none

        @classmethod
        def none(cls):
            return cls(none=True)

    def get(iterable, **attrs):
        for item in iterable or []:
            if all(getattr(item, k, None) == v for k, v in attrs.items()):
                return item
        return None

    utils = types.ModuleType("discord.utils")
    utils.get = get
    discord.Intents, discord.Client = Intents, Client
    discord.Message, discord.Thread = Message, Thread
    discord.ChannelType = ChannelType
    discord.AllowedMentions, discord.utils = AllowedMentions, utils
    discord.HTTPException, discord.NotFound = HTTPException, NotFound
    sys.modules.update({"discord": discord, "discord.utils": utils})
    return discord


def _stub_aiokafka():
    aiokafka = types.ModuleType("aiokafka")

    class _Client:
        def __init__(self, *a, **kw):
            self.args, self.kwargs = a, kw

    aiokafka.AIOKafkaConsumer = type("AIOKafkaConsumer", (_Client,), {})
    aiokafka.AIOKafkaProducer = type("AIOKafkaProducer", (_Client,), {})
    sys.modules["aiokafka"] = aiokafka


def _load():
    _stub_aiokafka()
    discord = _stub_discord()
    spec = importlib.util.spec_from_file_location("connector", HERE / "connector.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, discord


connector, discord = _load()


# --- the doubles the bridge talks to -----------------------------------------


class Producer:
    """Records what would have been produced, as (topic, key, envelope)."""

    def __init__(self):
        self.sent = []

    async def send_and_wait(self, topic, key=None, value=None):
        self.sent.append((topic, key.decode() if key else None, json.loads(value)))


class Webhook:
    def __init__(self, name="relay", *, deleted=False):
        self.name = name
        self.posts = []
        # Deleted in Discord behind the bridge's back: the cached handle 404s
        # on every send, and the channel no longer lists it.
        self.deleted = deleted

    async def send(self, content, username=None, allowed_mentions=None):
        if self.deleted:
            raise discord.NotFound("unknown webhook")
        self.posts.append((content, username, allowed_mentions))


class Channel:
    """A Discord text channel: what it holds in webhooks, and what the bot
    itself posted into it."""

    def __init__(self, channel_id, hooks=(), name=""):
        self.id = channel_id
        self.name = name
        self.type = discord.ChannelType.text
        self.hooks = list(hooks)
        self.sent = []
        self.created = []

    async def webhooks(self):
        # Discord does not list a webhook that has been deleted, which is what
        # makes get-or-create the right recovery from a 404.
        return [h for h in self.hooks if not h.deleted]

    async def create_webhook(self, name):
        hook = Webhook(name)
        self.hooks.append(hook)
        self.created.append(name)
        return hook

    async def send(self, text):
        self.sent.append(text)


class Thread(discord.Thread):
    def __init__(self, thread_id):
        self.id = thread_id
        self.sent = []

    async def send(self, text):
        self.sent.append(text)


class Author:
    def __init__(self, author_id, name="kyle", bot=False):
        self.id = author_id
        self.name = name
        self.display_name = name.title()
        self.bot = bot


class Message:
    def __init__(self, channel, author, content, *, webhook_id=None, mentions=()):
        self.channel, self.author = channel, author
        self.clean_content = content
        self.id = 999
        self.webhook_id = webhook_id
        self.mentions = list(mentions)


@pytest.fixture
def bridge(monkeypatch):
    """A connector with a bound channel (id 100), a thread (id 200) and no
    network anywhere: the producer and the Discord client are doubles."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t0ken")
    monkeypatch.setenv("CONNECTOR_AGENT", "pai")
    monkeypatch.setenv("AP_API_TOKEN", "ap_key")
    c = connector.DiscordConnector()
    async def identity_active(*, fresh=False):
        return True
    c._identity_active = identity_active
    c.producer = Producer()
    c.client.user = Author(1, name="relay-bot", bot=True)
    c._set_bindings([{"channel_id": "cafe", "external_ref": "100", "config": {}}])
    c.client._channels = {100: Channel(100), 200: Thread(200)}
    return c


def run(coro):
    return asyncio.run(coro)


# --- inbound -----------------------------------------------------------------


def test_a_message_in_a_bound_channel_is_published_inbound(bridge):
    channel = bridge.client._channels[100]
    run(bridge.on_message(Message(channel, Author(55, "kyle"), "morning @news")))
    (topic, key, envelope), = bridge.producer.sent
    assert (topic, key) == (connector.TOPIC_IN, "100")
    assert envelope["type"] == "conversation.message"
    assert envelope["data"] == {
        "connector": "discord", "external_ref": "100",
        "identity_id": "discord-default",
        "external_kind": "channel", "external_title": "",
        "external_url": "", "external_message_id": "999",
        # The id, not the name: `discord:<id>` is the platform's participant.
        "external_user": "55", "display_name": "Kyle",
        # Verbatim: `@news` is the mention the platform's router parses.
        "text": "morning @news", "agent": "pai"}


def test_bots_webhooks_and_our_own_messages_are_ignored(bridge):
    channel = bridge.client._channels[100]
    for message in (Message(channel, bridge.client.user, "my own words"),
                    Message(channel, Author(7, "other-bot", bot=True), "beep"),
                    Message(channel, Author(55), "relayed", webhook_id=42)):
        run(bridge.on_message(message))
    assert bridge.producer.sent == []


def test_an_unbound_channel_still_needs_a_mention(bridge):
    """The thread flow is untouched: a message in a channel nobody bound is
    only interesting if it addresses the bot."""
    quiet = Channel(300)
    run(bridge.on_message(Message(quiet, Author(55), "chatting among ourselves")))
    assert bridge.producer.sent == []

    thread = bridge.client._channels[200]
    bridge._active_threads.add(200)
    run(bridge.on_message(Message(thread, Author(55), "and to you, bot")))
    (topic, key, envelope), = bridge.producer.sent
    assert (topic, key) == (connector.TOPIC_IN, "200")
    assert envelope["data"]["external_ref"] == "200"


def test_a_known_thread_survives_a_connector_restart(bridge):
    bridge._active_threads = {200, 300}
    bridge._set_bindings([{"channel_id": "chat", "external_ref": "200",
                           "external_kind": "thread", "config": {}}])
    assert bridge._active_threads == {300}
    thread = bridge.client._channels[200]
    run(bridge.on_message(Message(thread, Author(55), "still here")))
    assert bridge.producer.sent[0][2]["data"]["external_message_id"] == "999"
    assert bridge.producer.sent[0][2]["data"]["external_user"] == "55"


# --- outbound ----------------------------------------------------------------


def _outbound(**kw):
    return {"connector": "discord", "external_ref": "100", "author": "agent:news",
            "kind": "text", "text": "the wire is quiet", **kw}


def test_a_bound_channel_is_posted_through_a_webhook_as_the_agent(bridge):
    run(bridge._deliver_outbound(_outbound()))
    channel = bridge.client._channels[100]
    hook = channel.hooks[0]
    assert channel.created == [connector.WEBHOOK_NAME]
    (text, username, mentions), = hook.posts
    assert (text, username) == ("the wire is quiet", "news")
    # Relayed text is written elsewhere, by agents and by people in another
    # room: it must not be able to ping anyone here.
    assert mentions.none_ is True
    assert channel.sent == []       # never as the bot itself


@pytest.mark.parametrize("author,expected", [("agent:news", "news"),
                                             ("user:admin", "admin"),
                                             ("system:relay", "Relay"),
                                             ("", "Relay")])
def test_the_speaker_is_the_participant_behind_the_message(author, expected):
    assert connector._speaker(author) == expected


def test_the_webhook_is_reused_not_recreated(bridge):
    existing = Webhook(connector.WEBHOOK_NAME)
    bridge.client._channels[100] = Channel(100, hooks=[existing, Webhook("other")])
    run(bridge._deliver_outbound(_outbound()))
    run(bridge._deliver_outbound(_outbound(text="still quiet")))
    assert bridge.client._channels[100].created == []
    assert [p[0] for p in existing.posts] == ["the wire is quiet", "still quiet"]


def test_a_long_message_is_chunked(bridge):
    run(bridge._deliver_outbound(_outbound(text="\n".join(["x" * 1000] * 3))))
    # Three 1000-char lines cannot share a 1990-char message, so each gets one.
    assert [len(p[0]) for p in bridge.client._channels[100].hooks[0].posts] == [1000] * 3


def test_a_thread_reply_still_goes_out_as_the_bot(bridge):
    """A DM thread has one speaker on each side; the webhook is what a channel
    full of agents needs, and a thread is not that."""
    run(bridge._deliver_outbound(_outbound(external_ref="200")))
    assert bridge.client._channels[200].sent == ["the wire is quiet"]


def test_a_long_thread_reply_is_not_truncated(bridge):
    text = "\n".join(["x" * 1000] * 3)
    run(bridge._deliver_outbound(_outbound(external_ref="200", external_kind="thread",
                                           text=text)))
    assert [len(part) for part in bridge.client._channels[200].sent] == [1000] * 3


def test_the_platform_can_forward_between_two_discord_endpoints(bridge):
    """Source-binding suppression belongs to the platform. If it emits a
    Discord-authored message for this endpoint, this is the *other* Discord
    endpoint and the connector must deliver it."""
    run(bridge._deliver_outbound(_outbound(author="discord:55")))
    assert bridge.client._channels[100].hooks[0].posts[0][0] == "the wire is quiet"


def test_another_connectors_message_is_not_ours(bridge):
    run(bridge._deliver_outbound(_outbound(connector="slack")))
    run(bridge._deliver_outbound(_outbound(identity_id="discord-other")))
    run(bridge._deliver_outbound(_outbound(external_ref="")))
    assert bridge.client._channels[100].hooks == []


def test_binding_inventory_excludes_another_chat_identity(bridge):
    bridge._set_bindings([
        {"channel_id": "ours", "external_ref": "100", "identity_id": "discord-default"},
        {"channel_id": "theirs", "external_ref": "200", "identity_id": "discord-other"},
    ])
    assert set(bridge.bound) == {100}


def test_second_identity_never_claims_legacy_default_routes_or_messages(
        bridge, monkeypatch):
    monkeypatch.setenv("AP_CHAT_IDENTITY", "discord-second")
    second = connector.DiscordConnector()
    second._identity_active = bridge._identity_active
    second.client._channels = {100: Channel(100), 200: Channel(200)}
    second._set_bindings([
        {"channel_id": "legacy", "external_ref": "100"},
        {"channel_id": "other", "external_ref": "200",
         "identity_id": "discord-second"},
    ])
    assert set(second.bound) == {200}
    run(second._deliver_outbound(_outbound(external_ref="100", identity_id=None)))
    run(second._deliver_channel_post({"channel_id": "100", "text": "legacy"}))
    assert second.client._channels[100].sent == []


def test_each_identity_consumes_the_full_outbound_stream(bridge, monkeypatch):
    groups = []

    class EmptyConsumer:
        def __init__(self, *args, group_id, **kwargs):
            groups.append(group_id)

        async def start(self):
            pass

        async def stop(self):
            pass

        def __aiter__(self):
            async def empty():
                if False:
                    yield None
            return empty()

    async def ready():
        pass

    monkeypatch.setattr(connector, "AIOKafkaConsumer", EmptyConsumer)
    bridge.client.wait_until_ready = ready
    run(bridge.consume_outbound())
    monkeypatch.setenv("AP_CHAT_IDENTITY", "discord-second")
    second = connector.DiscordConnector()
    second.client.wait_until_ready = ready
    run(second.consume_outbound())
    assert groups == ["connector-discord-discord-default",
                      "connector-discord-discord-second"]


# --- bindings ----------------------------------------------------------------


def test_the_bindings_refresh_parses_the_api_response(bridge, monkeypatch):
    rows = [{"channel_id": "aa", "external_ref": "100", "config": {"guild": "g"}},
            {"channel_id": "bb", "external_ref": "101", "config": {}},
            {"channel_id": "chat", "external_ref": "200",
             "external_kind": "thread", "config": {}},
            # A thread ref from the legacy flow, or somebody else's idea of a
            # channel: not a snowflake, so not ours to mirror.
            {"channel_id": "cc", "external_ref": "thread-9", "config": {}}]

    async def _fetch():
        return rows

    monkeypatch.setattr(bridge, "_fetch_bindings", _fetch)
    run(bridge.refresh_bindings())
    assert sorted(bridge.bound) == [100, 101]
    assert sorted(bridge.threads) == [200]
    assert bridge.bound[100]["config"] == {"guild": "g"}


def test_a_failed_refresh_keeps_the_last_good_map(bridge, monkeypatch):
    async def _boom():
        raise RuntimeError("the api is restarting")

    monkeypatch.setattr(bridge, "_fetch_bindings", _boom)
    run(bridge.refresh_bindings())
    assert sorted(bridge.bound) == [100]


def test_disabled_identity_stops_inbound_outbound_and_clears_routes(bridge, monkeypatch):
    async def inactive(*, fresh=False):
        return False
    monkeypatch.setattr(bridge, "_identity_active", inactive)
    run(bridge.on_message(Message(bridge.client._channels[100],
                                  Author(55, "kyle"), "hello")))
    run(bridge._deliver_outbound(_outbound()))
    run(bridge._deliver_channel_post({"channel_id": "100", "text": "hello"}))
    run(bridge.refresh_bindings())
    assert bridge.producer.sent == []
    assert bridge.client._channels[100].sent == []
    assert bridge.bound == {} and bridge.threads == {}


def test_unbinding_a_channel_drops_its_cached_webhook(bridge):
    run(bridge._deliver_outbound(_outbound()))
    assert 100 in bridge._webhooks
    bridge._set_bindings([])
    assert bridge._webhooks == {} and bridge.bound == {}


def test_without_an_api_token_nothing_is_mirrored(monkeypatch):
    """The bridge degrades to the thread flow rather than guessing: bindings
    are the platform's answer, and unauthenticated there is no answer."""
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t0ken")
    monkeypatch.delenv("AP_API_TOKEN", raising=False)
    monkeypatch.delenv("AP_API_TOKEN_FILE", raising=False)
    c = connector.DiscordConnector()
    assert c._api_bearer() == "" and c.bound == {}


def test_projected_api_identity_is_read_from_disk_on_each_request(monkeypatch, tmp_path):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "t0ken")
    monkeypatch.delenv("AP_API_TOKEN", raising=False)
    token = tmp_path / "token"
    token.write_text("first\n")
    monkeypatch.setenv("AP_API_TOKEN_FILE", str(token))
    c = connector.DiscordConnector()
    assert c._api_bearer() == "first"
    token.write_text("rotated\n")
    assert c._api_bearer() == "rotated"


def test_a_deleted_webhook_is_replaced_and_the_message_still_lands(bridge):
    """The failure this guards is silent: a webhook deleted in Discord 404s
    forever, the handle is cached, and the consume loop swallows the error — so
    the room simply stops receiving messages."""
    dead = Webhook(connector.WEBHOOK_NAME, deleted=True)
    channel = Channel(100, hooks=[dead])
    bridge.client._channels[100] = channel
    run(bridge._deliver_outbound(_outbound()))
    # The dead handle was evicted and a new webhook made; the message landed on
    # the new one, exactly once.
    assert dead.posts == [] and channel.created == [connector.WEBHOOK_NAME]
    assert [p[0] for p in channel.hooks[-1].posts] == ["the wire is quiet"]
    assert bridge._webhooks[100] is channel.hooks[-1]


def test_channel_broadcast_rejects_ambiguous_names_and_wrong_identity(bridge):
    first, second = Channel(100, name="news"), Channel(101, name="news")
    bridge.client.guilds = [types.SimpleNamespace(text_channels=[first]),
                            types.SimpleNamespace(text_channels=[second])]
    bridge.client._channels.update({100: first, 101: second})
    run(bridge._deliver_channel_post({"channel": "news", "text": "hello"}))
    assert first.sent == second.sent == []
    run(bridge._deliver_channel_post({"channel_id": "101", "text": "hello",
                                      "identity_id": "other-bot"}))
    assert second.sent == []
    run(bridge._deliver_channel_post({"channel_id": "101", "text": "hello",
                                      "identity_id": "discord-default"}))
    assert second.sent == ["hello"]


def test_channel_broadcast_rejects_non_text_and_mixed_targets(bridge):
    channel = Channel(101, name="news")
    bridge.client._channels[101] = channel
    channel.type = 2
    run(bridge._deliver_channel_post({"channel_id": "101", "text": "hello"}))
    channel.type = discord.ChannelType.text
    run(bridge._deliver_channel_post({"channel": "news", "channel_id": "101",
                                      "text": "hello"}))
    assert channel.sent == []


def test_a_webhook_that_keeps_vanishing_is_given_up_on(bridge, monkeypatch):
    """Two failures is a room we cannot post to. Retrying forever would spend
    the rest of the queue on it."""
    made = []

    async def _always_dead(channel):
        hook = Webhook(connector.WEBHOOK_NAME, deleted=True)
        made.append(hook)
        return hook

    monkeypatch.setattr(bridge, "_webhook", _always_dead)
    run(bridge._deliver_outbound(_outbound(text="a\n" + "b" * 3000)))
    # Two attempts for the first chunk, then it stops — the remaining chunks are
    # not each retried twice over.
    assert len(made) == 2


def test_a_username_discord_would_reject_is_sanitised():
    """Discord 400s a webhook username containing either of its own names, and
    caps it at 80 characters."""
    assert connector._speaker("agent:discord-watcher") == "-watcher"
    assert connector._speaker("user:Clyde") == "Relay"     # nothing left of it
    assert connector._speaker("agent:" + "n" * 120) == "n" * 80


def test_a_long_line_is_carried_on_not_cut():
    """The old behaviour truncated an over-long line to the chunk limit, which
    loses the end of a URL with no trace."""
    body = "x" * 4500
    chunks = connector._chunks(body)
    assert "".join(chunks) == body
    assert all(len(c) <= 1990 for c in chunks)
    # Short lines still group, and a mixed body keeps its order.
    assert connector._chunks("one\ntwo") == ["one\ntwo"]
