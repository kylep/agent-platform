"""village.* Kafka fan-out (plan §11).

Postgres is the source of truth; Kafka is live fan-out and audit. So publishing
is fire-and-forget from the engine's point of view: sync methods put a message
on a bounded queue, one task sends them, a full queue or a failed send is
logged and dropped (frames first). Nothing here is awaited by the tick.
"""
import asyncio
import logging
from typing import Any

from agentplatform.events import (TOPIC_VILLAGE_COMMANDS, TOPIC_VILLAGE_EVENTS,
                                  TOPIC_VILLAGE_FRAMES, TOPIC_VILLAGE_PLANNER,
                                  TOPIC_VILLAGE_REFLECTIONS, TOPIC_VILLAGE_SESSION, Producer)

log = logging.getLogger("village.bus")

QUEUE_MAX = 5000
SOURCE = "village"


def event_payload(row: Any, epoch: str | None = None) -> dict[str, Any]:
    """A saved village_events row as the wire/SSE shape (public fields only)."""
    return {"seq": row.seq, "session_id": row.session_id, "epoch": epoch or row.epoch,
            "tick": row.tick, "kind": row.kind, "actor": row.actor,
            "subjects": row.subjects or [], "public_text": row.public_text,
            "data": row.data or {}}


class VillageBus:
    def __init__(self, producer: Producer):
        self.producer = producer
        self._q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self._task: asyncio.Task | None = None
        self.dropped = 0
        self.sent = 0

    # --- producers (sync, never block) -------------------------------------
    def _put(self, topic: str, key: str, data: dict[str, Any], type: str) -> None:
        try:
            self._q.put_nowait((topic, key, data, type))
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 100 == 1:
                log.warning("village bus full; %d messages dropped so far", self.dropped)

    def events(self, session_id: str, epoch: str, rows: list[Any]) -> None:
        for row in rows:
            self._put(TOPIC_VILLAGE_EVENTS, session_id, event_payload(row, epoch), "village.event")

    def frame(self, session_id: str, tick: int, frame: dict[str, Any]) -> None:
        """2 Hz compact positions + caption deltas. Never stored."""
        self._put(TOPIC_VILLAGE_FRAMES, session_id, {"session_id": session_id, "tick": tick,
                                                     **frame}, "village.frame")

    def session(self, day: str, data: dict[str, Any], type: str = "village.session") -> None:
        """Lifecycle: scheduled, gate decision, started, closing, closed, rest, skipped."""
        self._put(TOPIC_VILLAGE_SESSION, day, data, type)

    def command(self, persona: str | None, data: dict[str, Any]) -> None:
        self._put(TOPIC_VILLAGE_COMMANDS, persona or "system", data, "village.command")

    def planner(self, persona: str, data: dict[str, Any]) -> None:
        self._put(TOPIC_VILLAGE_PLANNER, persona, data, "village.planner")

    def reflection(self, persona: str, data: dict[str, Any]) -> None:
        self._put(TOPIC_VILLAGE_REFLECTIONS, persona, data, "village.reflection")

    # --- sender --------------------------------------------------------------
    async def drain(self) -> None:
        """Send everything queued right now (tests, shutdown)."""
        while not self._q.empty():
            await self._send(self._q.get_nowait())

    async def _send(self, item: tuple) -> None:
        topic, key, data, type = item
        try:
            await self.producer.publish(topic, key, data, type=type, source=SOURCE)
            self.sent += 1
        except Exception:
            log.exception("village bus: publish to %s failed", topic)

    async def run(self) -> None:
        while True:
            await self._send(await self._q.get())

    def start(self) -> None:
        self._task = asyncio.create_task(self.run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await self.drain()
