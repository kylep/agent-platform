"""Kafka event bus: a versioned message envelope, a hardened producer, and a
shared consume loop that dead-letters processing failures.

Every message on the wire is an `Envelope` (JSON): a typed, versioned wrapper
around a domain payload (`data`). Producers wrap; consumers `unwrap`. Legacy
un-enveloped messages are tolerated during rollout."""
import json
import logging
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaProducer
from pydantic import BaseModel

log = logging.getLogger("events")

# Run lifecycle
TOPIC_RUN_INBOUND = "run.inbound"          # event-sourced run-creation commands
TOPIC_RUN_REQUESTS = "run.requests"        # materialized runs → dispatcher
TOPIC_RUN_EVENTS = "run.events"            # run state transitions
TOPIC_RUN_TRANSCRIPT = "run.transcript"    # per-frame runner output
TOPIC_RUN_DLQ = "run.dlq"                  # run-launch failures (DLQ UI)
# Conversations
TOPIC_CONVERSATION_INBOUND = "conversation.inbound"    # connector → platform
TOPIC_CONVERSATION_OUTBOUND = "conversation.outbound"  # platform → connector
# Infra
TOPIC_DEAD_LETTER = "dead.letter"          # consumer processing failures

ALL_TOPICS = [
    TOPIC_RUN_INBOUND, TOPIC_RUN_REQUESTS, TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT,
    TOPIC_RUN_DLQ, TOPIC_CONVERSATION_INBOUND, TOPIC_CONVERSATION_OUTBOUND,
    TOPIC_DEAD_LETTER,
]

SCHEMA_VERSION = 1


class Envelope(BaseModel):
    type: str
    schema_version: int = SCHEMA_VERSION
    id: str
    ts: str
    key: str
    source: str
    data: dict


def make_envelope(*, type: str, key: str, data: dict, source: str) -> dict:
    return Envelope(
        type=type, id=uuid.uuid4().hex,
        ts=datetime.now(timezone.utc).isoformat(),
        key=key, source=source, data=data,
    ).model_dump()


def unwrap(raw: bytes | dict) -> tuple[dict, dict]:
    """Return (envelope, data) from a wire message. Tolerates legacy
    un-enveloped payloads (no `type`/`data` keys) by synthesizing an envelope so
    a mid-rollout topic doesn't crash consumers."""
    value = raw if isinstance(raw, dict) else json.loads(raw)
    if isinstance(value, dict) and "data" in value and "type" in value and "schema_version" in value:
        return value, value["data"]
    # legacy / foreign message
    return {"type": "legacy", "schema_version": 0, "id": "", "ts": "",
            "key": "", "source": "legacy", "data": value}, value


class Producer:
    """Idempotent, acks=all, lz4-compressed producer. Wraps every payload in an
    envelope so the type/source/version travel with the message."""

    def __init__(self, bootstrap: str = "localhost:9092", source: str = "backend"):
        self._bootstrap = bootstrap
        self._source = source
        self._p: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._p = AIOKafkaProducer(
            bootstrap_servers=self._bootstrap,
            enable_idempotence=True,
            acks="all",
            compression_type="lz4",
        )
        await self._p.start()

    async def stop(self) -> None:
        if self._p:
            await self._p.stop()

    async def publish(self, topic: str, key: str, data: dict, *,
                      type: str, source: str | None = None) -> None:
        assert self._p, "producer not started"
        env = make_envelope(type=type, key=key, data=data, source=source or self._source)
        await self._p.send_and_wait(topic, json.dumps(env).encode(), key=key.encode())


class FakeProducer(Producer):
    """Test double. `published` keeps the logical (topic, key, data) tuples so
    existing assertions hold; `envelopes` keeps the full wrapped form."""

    def __init__(self, source: str = "test"):
        self._source = source
        self.published: list[tuple[str, str, dict]] = []
        self.envelopes: list[dict] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def publish(self, topic: str, key: str, data: dict, *,
                      type: str, source: str | None = None) -> None:
        self.published.append((topic, key, data))
        self.envelopes.append(make_envelope(type=type, key=key, data=data,
                                            source=source or self._source))


async def dead_letter(producer: Producer, source_topic: str, key: str,
                      raw: object, error: str) -> None:
    """Route a message that failed processing to the dead-letter topic."""
    try:
        await producer.publish(TOPIC_DEAD_LETTER, key or "unknown",
                               {"source_topic": source_topic, "key": key,
                                "raw": raw if isinstance(raw, (dict, str, int, float, bool, type(None))) else str(raw),
                                "error": error},
                               type="dead.letter")
    except Exception:
        log.exception("failed to dead-letter message from %s", source_topic)


async def consume_forever(consumer, producer: Producer, on_message, *,
                          source_topic_of=lambda msg: msg.topic) -> None:
    """Shared at-least-once consume loop: unwrap, dispatch, and on handler failure
    dead-letter the message (instead of silently dropping it) before committing.
    `on_message(msg, data)` is awaited; offsets advance only after the message is
    either handled or dead-lettered."""
    async for msg in consumer:
        key = msg.key.decode() if msg.key else ""
        try:
            _, data = unwrap(msg.value)
        except Exception as e:
            await dead_letter(producer, source_topic_of(msg), key, None, f"unwrap: {e}")
            await consumer.commit()
            continue
        try:
            await on_message(msg, data)
        except Exception as e:
            log.exception("handler failed on %s", source_topic_of(msg))
            await dead_letter(producer, source_topic_of(msg), key, data, str(e))
        await consumer.commit()
