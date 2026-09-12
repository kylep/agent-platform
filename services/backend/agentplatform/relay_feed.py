"""The live fan-out behind the platform's SSE endpoints (docs/design/19 T6,
docs/design/20 T4).

One object per API process. Every open event stream holds a queue; a message
reaches those queues twice over — once from the API pod that wrote it, the
moment the post commits, and once off `relay.messages`, which is what carries a
message written by ANOTHER pod, the recorder or a bridge. Both paths go through
`publish`, and the id dedupe there is what makes the double feed safe: the
local hand-off keeps the room live when Kafka is down or slow, and the Kafka
echo is dropped as the duplicate it is.

Queues are bounded and publishing never blocks or raises: a browser that has
stopped reading costs its own stream — it loses its oldest frame and is handed
an `overflow` marker telling it to resync — never the post that produced them.
Presence is derived here too, from `run.events` — a run with a channel entering
RUNNING is an agent thinking in that room, and anything terminal is it going
quiet — so nothing has to be stored and nothing leaks when a pod dies.

`TopicFeed` is the mechanism; `RelayFeed` is Relay's use of it. Tickets
(docs/design/20) is the second: the same bounded queues, the same overflow
marker and the same "publishing never raises" promise, over `tickets.events` —
a board that has fallen behind wants telling in exactly the way a room does."""
import asyncio
import logging
from collections import OrderedDict

from agentplatform.db import ACTIVE_STATES, Run, RunState
from agentplatform.events import (TOPIC_RELAY_MESSAGES, TOPIC_RUN_EVENTS,
                                  consume_forever)

log = logging.getLogger("relay_feed")

# Per-stream backlog. A browser reads a frame in microseconds; 200 unread means
# the socket is gone and has not been noticed yet.
QUEUE_SIZE = 200
# How many message ids the dedupe remembers. The two arrivals of one message are
# milliseconds apart, so this only has to outlive a burst, not a session.
SEEN_SIZE = 512
THINKING, IDLE = "thinking", "idle"
# The event a stream gets instead of the frames it was too slow to take. It
# says "you have missed something, resync" — which a client can act on, where a
# silently dropped message is a room that is quietly wrong from then on.
OVERFLOW = "overflow"
# Anything not still in flight is the agent going quiet. Derived from the one
# definition of "in flight" so a new run state cannot leave a dot lit forever.
TERMINAL_STATES = tuple(s for s in RunState if s not in ACTIVE_STATES)


class TopicFeed:
    """Per-key fan-out over ONE Kafka topic.

    `topic` is what the feed consumes and `event` the SSE event name a record
    becomes; `frame_of` turns a consumed payload into the `(stream key, data)`
    its subscribers receive, or None for a record this feed has nothing to say
    about. The stream key is whatever the endpoint subscribes by — a channel id
    for Relay, one constant for the ticket board, which is a single stream for
    the whole platform.

    `dedupe` is for a feed whose events arrive TWICE: Relay publishes locally
    the moment a post commits and then meets the same message again off Kafka,
    and the id dedupe is what makes that double feed safe. A feed fed from one
    direction leaves it off — a ticket's id is on every one of its events, so
    deduping there would deliver a ticket's first change and drop the rest.

    `session_factory` is only needed by a subclass that has to look something
    up (Relay's presence: a run event names a run, not a room); it is assigned
    after construction on the API's lifespan path, exactly as the agent store's
    is."""

    def __init__(self, topic: str, *, event: str, frame_of, dedupe: bool = False,
                 session_factory=None):
        self.topic, self.event, self.frame_of = topic, event, frame_of
        self.dedupe = dedupe
        self.session_factory = session_factory
        self._subs: dict[str, set[asyncio.Queue]] = {}
        self._seen: OrderedDict[str, None] = OrderedDict()

    def subscriber_count(self, channel_id: str) -> int:
        """How many streams are watching this room. Nothing depends on it in
        production — it is how a test proves a stream let go of its queue."""
        return len(self._subs.get(channel_id, ()))

    def subscribe(self, channel_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
        self._subs.setdefault(channel_id, set()).add(q)
        return q

    def unsubscribe(self, channel_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(channel_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            # A room with nobody watching keeps no entry: the dict is as long as
            # the number of open streams, not the number of channels ever seen.
            del self._subs[channel_id]

    def publish(self, channel_id: str, event: str, data: dict) -> bool:
        """Hand one event to this channel's streams. Returns False if it was a
        message this feed has already delivered — the caller's two paths racing,
        not an error. Synchronous on purpose: `put_nowait` cannot block, so
        posting a message never waits on a reader."""
        if self.dedupe and event == self.event and not self._fresh(data.get("id")):
            return False
        for q in list(self._subs.get(channel_id, ())):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                # Drop the OLDEST frame to make room for the marker: a reader
                # this far behind needs to be told to resync, and the newest
                # events are the ones it would want if it catches up. Publishing
                # must never raise into the caller — the message is committed,
                # and one slow browser cannot be allowed to fail a post.
                log.warning("%s stream backlog full on %s; signalling overflow",
                            self.topic, channel_id)
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    q.put_nowait((OVERFLOW, {}))
                except asyncio.QueueFull:
                    pass
        return True

    def _fresh(self, message_id) -> bool:
        if not message_id:
            return True
        if message_id in self._seen:
            return False
        self._seen[message_id] = None
        while len(self._seen) > SEEN_SIZE:
            self._seen.popitem(last=False)
        return True

    async def run(self, consumer, producer=None) -> None:
        """Consume this feed's topics forever. The shared loop dead-letters a
        handler failure, so `producer` is the API's own; without one a failure
        is logged and the offset still advances."""
        await consume_forever(consumer, producer, self._on_message)

    async def _on_message(self, msg, data: dict) -> None:
        if msg.topic != self.topic:
            return
        framed = self.frame_of(data)
        if framed is not None:
            self.publish(framed[0], self.event, framed[1])


def _relay_frame(data: dict):
    return (data["channel_id"], data) if data.get("channel_id") else None


class RelayFeed(TopicFeed):
    """Relay's rooms: `relay.messages` fanned out per channel, plus the presence
    a run event implies — the one thing here that is not a straight relay of
    what arrived, and the reason this feed holds a session factory."""

    def __init__(self, session_factory=None):
        super().__init__(TOPIC_RELAY_MESSAGES, event="message", frame_of=_relay_frame,
                         dedupe=True, session_factory=session_factory)

    async def _on_message(self, msg, data: dict) -> None:
        if msg.topic == TOPIC_RUN_EVENTS:
            await self._presence(data)
            return
        await super()._on_message(msg, data)

    async def _presence(self, data: dict) -> None:
        state, run_id = data.get("state"), data.get("run_id")
        if state == RunState.RUNNING:
            presence = THINKING
        elif state in TERMINAL_STATES:
            presence = IDLE
        else:
            # queued/dispatched: the agent has not started thinking yet, and a
            # dot that flickers on before there is anything to see is noise.
            return
        if self.session_factory is None or not run_id:
            return
        async with self.session_factory() as session:
            run = await session.get(Run, run_id)
            # A run this API never recorded (or one with no room) is not an
            # error: run.events carries every run on the platform, and only the
            # ones sitting in a channel have anywhere to show presence.
            if run is None or not run.conversation_id:
                return
            agent, channel_id = run.agent, run.conversation_id
        self.publish(channel_id, "presence",
                     {"agent": agent, "state": presence, "channel_id": channel_id})
