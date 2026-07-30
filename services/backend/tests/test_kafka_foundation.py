"""Kafka foundation: materialize_run idempotency + consume_forever dead-lettering."""
import json

from sqlalchemy import func, select

from agentplatform.db import Run
from agentplatform.events import (FakeProducer, TOPIC_DEAD_LETTER, TOPIC_RUN_REQUESTS,
                                  consume_forever, make_envelope)
from agentplatform.materialize import materialize_run


async def test_materialize_run_creates_and_is_idempotent(sf):
    producer = FakeProducer()
    spec = {"run_id": "r" * 32, "agent": "echo", "prompt": "hi",
            "trigger": "webhook", "requested_by": "op"}
    await materialize_run(sf, producer, spec)
    await materialize_run(sf, producer, spec)  # redelivery
    async with sf() as s:
        n = (await s.execute(select(func.count()).select_from(Run)
             .where(Run.id == "r" * 32))).scalar_one()
    assert n == 1  # created once despite two calls
    # both calls publish run.requests (dispatcher.handle is itself idempotent)
    reqs = [p for p in producer.published if p[0] == TOPIC_RUN_REQUESTS]
    assert len(reqs) == 2 and reqs[0][1] == "r" * 32


async def test_materialize_run_survives_a_hanging_publish(sf):
    """Kafka-down: the row must be committed as `queued` and the call must
    return fast even though the broker publish hangs — the sweep drains it.
    Regression for the POST /api/runs 504 during a Kafka outage."""
    import asyncio

    class HangingProducer(FakeProducer):
        async def publish(self, *a, **k):
            await asyncio.sleep(3600)   # broker down: send never completes

    spec = {"run_id": "q" * 32, "agent": "echo", "prompt": "hi",
            "trigger": "manual", "requested_by": "op"}
    # A short timeout stands in for the production default (5s).
    rid = await asyncio.wait_for(
        materialize_run(sf, HangingProducer(), spec, publish_timeout=0.05),
        timeout=5)
    assert rid == "q" * 32
    async with sf() as s:                       # committed, queued, not lost
        run = await s.get(Run, "q" * 32)
        assert run is not None and run.state == "queued"


class _Msg:
    def __init__(self, topic, key, value):
        self.topic = topic
        self.key = key.encode()
        self.value = value


class _FakeConsumer:
    """Async-iterates a fixed list of messages, then stops. Records commits."""
    def __init__(self, msgs):
        self._msgs = msgs
        self.commits = 0

    def __aiter__(self):
        self._it = iter(self._msgs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def commit(self):
        self.commits += 1


async def test_consume_forever_dead_letters_handler_failure():
    producer = FakeProducer()
    good = json.dumps(make_envelope(type="t", key="k1", data={"ok": 1}, source="s")).encode()
    bad = json.dumps(make_envelope(type="t", key="k2", data={"boom": 1}, source="s")).encode()
    consumer = _FakeConsumer([_Msg("some.topic", "k1", good), _Msg("some.topic", "k2", bad)])
    seen = []

    async def on_message(msg, data):
        if "boom" in data:
            raise ValueError("kaboom")
        seen.append(data)

    await consume_forever(consumer, producer, on_message)
    assert seen == [{"ok": 1}]            # good one handled
    assert consumer.commits == 2          # both offsets advanced (no infinite loop)
    dlq = [p for p in producer.published if p[0] == TOPIC_DEAD_LETTER]
    assert len(dlq) == 1                  # the poison message was dead-lettered, not dropped
    assert dlq[0][2]["source_topic"] == "some.topic" and "kaboom" in dlq[0][2]["error"]
