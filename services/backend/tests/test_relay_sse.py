"""Relay's live feed (docs/design/19 T6): the SSE endpoint and the in-process
fan-out behind it.

The fan-out is deliberately double-fed — the API publishes locally the moment a
post commits, AND the same message comes back off `relay.messages` — so these
tests are mostly about the two never colliding: one frame per message, no
matter which arrival won, and a room that stays live when Kafka is not.

httpx's ASGITransport buffers a response to completion, and an event stream
never completes, so the streaming tests drive the app over raw ASGI instead of
`admin_client.stream(...)`. Everything that answers before the stream opens (a
403, say) is still an ordinary client call."""
import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from agentplatform.api import relay as relay_api
from agentplatform.db import Conversation, RelayParticipant, Run, RunState
from agentplatform.events import (TOPIC_RELAY_MESSAGES, TOPIC_RUN_EVENTS,
                                  make_envelope)
from agentplatform import relay_feed
from agentplatform.relay_feed import OVERFLOW, RelayFeed

from .test_relay_api import _agent_token, _channel_id, _seed, token_client  # noqa: F401


@asynccontextmanager
async def sse(client, path, *, headers=None):
    """Open an event stream over raw ASGI and hand back a frame reader."""
    app = client._transport.app
    chunks: asyncio.Queue = asyncio.Queue()
    started = asyncio.Event()
    response = {}

    async def receive():
        # The client never disconnects; the test cancels the task instead.
        await asyncio.Event().wait()

    async def send(message):
        if message["type"] == "http.response.start":
            response["status"] = message["status"]
            response["headers"] = {k.decode(): v.decode() for k, v in message["headers"]}
            started.set()
        elif message["type"] == "http.response.body":
            chunks.put_nowait(message.get("body", b""))

    raw = dict(client.cookies)
    wire = [(b"cookie", "; ".join(f"{k}={v}" for k, v in raw.items()).encode())] if raw else []
    wire += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    url, _, query = path.partition("?")
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
             "method": "GET", "path": url, "raw_path": url.encode(),
             "query_string": query.encode(), "headers": wire, "scheme": "http",
             "server": ("t", 80), "client": ("127.0.0.1", 123), "root_path": ""}
    task = asyncio.create_task(app(scope, receive, send))

    class Reader:
        buffer = ""

        async def frame(self, timeout=2.0):
            """The next complete SSE frame, blank-line terminated."""
            while "\n\n" not in self.buffer:
                self.buffer += (await asyncio.wait_for(chunks.get(), timeout)).decode()
            frame, _, self.buffer = self.buffer.partition("\n\n")
            return frame

        async def event(self, timeout=2.0):
            """The next frame that is not a heartbeat comment, as (event, data).
            The deadline spans the whole wait, heartbeats included: a stream
            that only ever beats must fail this call, not park the suite."""
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout
            while True:
                frame = await self.frame(max(0.01, deadline - loop.time()))
                if frame.startswith(":"):
                    continue
                fields = dict(line.split(": ", 1) for line in frame.splitlines())
                return fields.get("event"), json.loads(fields["data"]), fields

    try:
        await asyncio.wait_for(started.wait(), 2.0)
        yield response, Reader()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


async def test_stream_carries_a_message_posted_after_connect(admin_client, sf, producer):
    cid = await _channel_id(sf, "general")
    async with sse(admin_client, f"/api/relay/channels/{cid}/events") as (resp, stream):
        assert resp["status"] == 200
        assert resp["headers"]["content-type"].startswith("text/event-stream")
        posted = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                          json={"body": "live"})).json()
        event, data, fields = await stream.event()
        assert (event, data["id"], data["body"]) == ("message", posted["id"], "live")
        # The frame carries the message id as its SSE id, which is what a
        # reconnecting browser sends back as Last-Event-ID.
        assert fields["id"] == posted["id"]
        assert data["channel_kind"] == "channel"


async def test_a_reaction_reaches_the_stream(admin_client, sf):
    cid = await _channel_id(sf, "general")
    m = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                 json={"body": "nice"})).json()
    async with sse(admin_client, f"/api/relay/channels/{cid}/events") as (_, stream):
        await admin_client.post(f"/api/relay/messages/{m['id']}/reactions",
                                json={"emoji": "🎉"})
        event, data, _ = await stream.event()
    assert event == "reaction"
    assert data == {"message_id": m["id"], "emoji": "🎉", "count": 1,
                    "participant": "user:admin"}


async def test_after_replays_what_the_client_missed(admin_client, sf):
    cid = await _channel_id(sf, "general")
    first = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                     json={"body": "one"})).json()
    second = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                      json={"body": "two"})).json()
    async with sse(admin_client,
                   f"/api/relay/channels/{cid}/events?after={first['id']}") as (_, stream):
        _, data, _ = await stream.event()
        assert data["id"] == second["id"]
    # The header is the browser's own way of saying the same thing.
    async with sse(admin_client, f"/api/relay/channels/{cid}/events",
                   headers={"Last-Event-ID": first["id"]}) as (_, stream):
        _, data, _ = await stream.event()
        assert data["id"] == second["id"]


async def test_heartbeat_keeps_the_connection_open(admin_client, sf, monkeypatch):
    """Nothing is happening in the room — the stream must still say something,
    or every proxy between here and the browser will close it."""
    monkeypatch.setattr(relay_api, "HEARTBEAT_SECONDS", 0.05)
    cid = await _channel_id(sf, "general")
    async with sse(admin_client, f"/api/relay/channels/{cid}/events") as (_, stream):
        assert (await stream.frame()) == ": heartbeat"


async def test_a_non_member_agent_is_refused_before_the_stream_opens(
        client, token_client, sf, seed_agent, agent_store):
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    async with sf() as s:
        conv = Conversation(connector="web", kind="group", topic="", title="private")
        s.add(conv)
        await s.flush()
        s.add(RelayParticipant(channel_id=conv.id, participant="user:admin"))
        await s.commit()
        cid = conv.id
    r = await token_client.get(f"/api/relay/channels/{cid}/events", headers=headers)
    assert r.status_code == 403


async def test_feed_delivers_once_however_the_message_arrives():
    """The local publish and the Kafka echo are the same message. The queue
    must see it once, or every UI would render every message twice."""
    feed = RelayFeed()
    q = feed.subscribe("c1")
    assert feed.publish("c1", "message", {"id": "m1", "body": "hi"}) is True
    assert feed.publish("c1", "message", {"id": "m1", "body": "hi"}) is False
    assert q.qsize() == 1
    # A different message still gets through, and so does a non-message event
    # (a reaction carries no id of its own to dedupe on).
    feed.publish("c1", "message", {"id": "m2", "body": "again"})
    feed.publish("c1", "reaction", {"message_id": "m1", "emoji": "👍"})
    assert q.qsize() == 3
    feed.unsubscribe("c1", q)
    feed.publish("c1", "message", {"id": "m3"})
    assert q.qsize() == 3


async def test_feed_fans_out_to_every_subscriber_of_that_channel_only():
    feed = RelayFeed()
    a, b, other = feed.subscribe("c1"), feed.subscribe("c1"), feed.subscribe("c2")
    feed.publish("c1", "message", {"id": "m1"})
    assert (a.qsize(), b.qsize(), other.qsize()) == (1, 1, 0)


class StubConsumer:
    """The Kafka consumer `consume_forever` expects: an async iterator of
    messages that commits offsets. It ends, which is what lets `run()` return."""

    def __init__(self, messages):
        self.messages = messages
        self.commits = 0

    async def __aiter__(self):
        for m in self.messages:
            yield m

    async def commit(self):
        self.commits += 1


def _msg(topic, key, type, data):
    return SimpleNamespace(topic=topic, key=key.encode(),
                           value=json.dumps(make_envelope(
                               type=type, key=key, data=data, source="test")).encode())


async def test_run_publishes_presence_for_a_run_in_a_channel(sf, producer):
    """Presence is derived from run state, not stored: the feed turns a run
    entering RUNNING in a channel into `x is thinking` and its end into idle."""
    async with sf() as s:
        s.add(Run(id="r1", agent="news", trigger="mention", requested_by="user:admin",
                  prompt="p", conversation_id="c1", state=RunState.RUNNING))
        await s.commit()
    feed = RelayFeed(sf)
    q = feed.subscribe("c1")
    consumer = StubConsumer([
        _msg(TOPIC_RUN_EVENTS, "r1", "run.state", {"run_id": "r1", "type": "state",
                                                   "state": "running", "detail": ""}),
        _msg(TOPIC_RUN_EVENTS, "r1", "run.state", {"run_id": "r1", "type": "state",
                                                   "state": "succeeded", "detail": ""}),
        _msg(TOPIC_RUN_EVENTS, "r2", "run.state", {"run_id": "r2", "type": "state",
                                                   "state": "running", "detail": ""}),
    ])
    await feed.run(consumer, producer)
    assert q.get_nowait() == ("presence", {"agent": "news", "state": "thinking",
                                           "channel_id": "c1"})
    assert q.get_nowait() == ("presence", {"agent": "news", "state": "idle",
                                           "channel_id": "c1"})
    # An unknown run is not an error — it is a run this API never recorded.
    assert q.empty() and consumer.commits == 3
    assert not [e for e in producer.envelopes if e["type"] == "dead.letter"]


async def test_run_fans_out_a_relay_message_from_kafka(sf, producer):
    feed = RelayFeed(sf)
    q = feed.subscribe("c1")
    payload = {"id": "m9", "channel_id": "c1", "body": "from another pod"}
    await feed.run(StubConsumer([_msg(TOPIC_RELAY_MESSAGES, "c1", "relay.message",
                                      payload)]), producer)
    assert q.get_nowait() == ("message", payload)


async def test_an_unknown_after_replays_nothing(admin_client, sf):
    """A cursor from another room (or a pruned message) is not a licence to
    push the newest page: the client already has whatever it is showing."""
    cid = await _channel_id(sf, "general")
    await admin_client.post(f"/api/relay/channels/{cid}/messages", json={"body": "old"})
    async with sse(admin_client, f"/api/relay/channels/{cid}/events?after=nope") as (_, s):
        fresh = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                         json={"body": "new"})).json()
        _, data, _ = await s.event()
        assert data["id"] == fresh["id"]          # the first frame, not the second


async def test_an_overflowing_stream_is_told_to_resync(admin_client, sf):
    """A reader too slow to keep up loses frames — it must not also lose the
    knowledge that it did."""
    cid = await _channel_id(sf, "general")
    feed = admin_client._transport.app.state.feed
    async with sse(admin_client, f"/api/relay/channels/{cid}/events") as (_, stream):
        posted = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                          json={"body": "seen"})).json()
        assert (await stream.event())[1]["id"] == posted["id"]
        feed.publish(cid, OVERFLOW, {})
        event, data, _ = await stream.event()
    # The client is told exactly how far its picture is trustworthy.
    assert (event, data) == ("overflow", {"after": posted["id"]})


async def test_a_full_queue_costs_the_slowest_frame_not_the_post():
    feed = RelayFeed()
    q = feed.subscribe("c1")
    for i in range(relay_feed.QUEUE_SIZE):
        feed.publish("c1", "message", {"id": f"m{i}"})
    assert q.full()
    feed.publish("c1", "message", {"id": "newest"})      # must not raise
    drained = [q.get_nowait() for _ in range(q.qsize())]
    assert drained[0] == ("message", {"id": "m1"})       # the oldest went
    assert drained[-1] == (OVERFLOW, {})
    assert len(drained) == relay_feed.QUEUE_SIZE


async def test_a_stream_ends_when_the_agent_stops_being_a_member(
        client, token_client, sf, seed_agent, agent_store, monkeypatch):
    """Membership can be taken away mid-stream. A socket that keeps delivering
    afterwards is the one way an agent reads a room it was thrown out of."""
    monkeypatch.setattr(relay_api, "HEARTBEAT_SECONDS", 0.05)
    await _seed(seed_agent, agent_store, "news")
    headers = await _agent_token(sf, "news")
    async with sf() as s:
        conv = Conversation(connector="web", kind="group", topic="", title="war room")
        s.add(conv)
        await s.flush()
        for p in ("user:admin", "agent:news"):
            s.add(RelayParticipant(channel_id=conv.id, participant=p))
        await s.commit()
        cid = conv.id
    async with sse(token_client, f"/api/relay/channels/{cid}/events",
                   headers=headers) as (resp, stream):
        assert resp["status"] == 200
        assert (await stream.frame()) == ": heartbeat"
        async with sf() as s:
            await s.delete(await s.get(RelayParticipant, (cid, "agent:news")))
            await s.commit()
        event, data, _ = await stream.event()
    assert event == "closed" and "member" in data["reason"]


async def test_a_stream_lets_go_of_its_queue(admin_client, sf):
    cid = await _channel_id(sf, "general")
    feed = admin_client._transport.app.state.feed
    async with sse(admin_client, f"/api/relay/channels/{cid}/events") as (_, stream):
        posted = (await admin_client.post(f"/api/relay/channels/{cid}/messages",
                                          json={"body": "hi"})).json()
        assert (await stream.event())[1]["id"] == posted["id"]
        assert feed.subscriber_count(cid) == 1
    # The client disconnecting closes the generator, whose finally unsubscribes.
    await asyncio.sleep(0)
    assert feed.subscriber_count(cid) == 0


async def test_a_stream_that_never_opens_leaves_no_queue_behind(admin_client, sf,
                                                                monkeypatch):
    """Between subscribe and the first frame there is DB work that can fail.
    The queue must not outlive a request that never became a stream."""
    cid = await _channel_id(sf, "general")
    feed = admin_client._transport.app.state.feed

    async def _boom(*a, **kw):
        raise RuntimeError("replay query failed")

    monkeypatch.setattr(relay_api, "_missed", _boom)
    with pytest.raises(RuntimeError):
        await admin_client.get(f"/api/relay/channels/{cid}/events?after=whatever")
    assert feed.subscriber_count(cid) == 0


# --- the feed's consumer is a seam, not a setting ----------------------------

class SleepyConsumer:
    def __init__(self):
        self.started = self.stopped = False

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True


async def test_no_factory_means_no_consumer(client):
    """`kafka_bootstrap` always has a value, so a feed keyed off that alone
    would have every lifespan-entering test dialling a broker that is not
    there. Production passes the factory; nothing else does."""
    app = client._transport.app
    ran = []

    async def _run(consumer, producer=None):
        ran.append(consumer)

    app.state.feed.run = _run
    assert app.state.feed_consumer_factory is None
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0.05)
    assert ran == []


async def test_the_lifespan_runs_the_feed_on_the_factorys_consumer(client):
    app = client._transport.app
    consumer = SleepyConsumer()
    running, cancelled = asyncio.Event(), asyncio.Event()

    async def _run(got, producer=None):
        assert got is consumer
        running.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    app.state.feed.run = _run
    app.state.feed_consumer_factory = lambda: consumer
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(running.wait(), 2)
        assert consumer.started
    # Cancelled at shutdown, and the consumer closed on the way out.
    await asyncio.sleep(0.05)
    assert cancelled.is_set() and consumer.stopped
