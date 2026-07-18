import json
from aiokafka import AIOKafkaProducer

TOPIC_RUN_REQUESTS = "run.requests"
TOPIC_RUN_EVENTS = "run.events"
TOPIC_RUN_TRANSCRIPT = "run.transcript"
TOPIC_RUN_DLQ = "run.dlq"
ALL_TOPICS = [TOPIC_RUN_REQUESTS, TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT, TOPIC_RUN_DLQ]


class Producer:
    def __init__(self, bootstrap: str = "localhost:9092"):
        self._bootstrap = bootstrap
        self._p: AIOKafkaProducer | None = None

    async def start(self) -> None:
        self._p = AIOKafkaProducer(bootstrap_servers=self._bootstrap)
        await self._p.start()

    async def stop(self) -> None:
        if self._p:
            await self._p.stop()

    async def publish(self, topic: str, key: str, value: dict) -> None:
        assert self._p, "producer not started"
        await self._p.send_and_wait(
            topic, json.dumps(value).encode(), key=key.encode()
        )


class FakeProducer(Producer):
    def __init__(self):
        self.published: list[tuple[str, str, dict]] = []

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def publish(self, topic: str, key: str, value: dict) -> None:
        self.published.append((topic, key, value))
