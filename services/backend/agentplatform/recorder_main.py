import asyncio
import logging

from aiokafka import AIOKafkaConsumer

from agentplatform.config import get_settings
from agentplatform.db import init_db, make_engine, make_session_factory
from agentplatform.events import (Producer, TOPIC_RUN_DLQ, TOPIC_RUN_EVENTS,
                                  TOPIC_RUN_TRANSCRIPT, consume_forever)
from agentplatform.recorder import Recorder

log = logging.getLogger("recorder_main")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    engine = make_engine(settings.db_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    # A producer for the conversation-outbound projector and dead-lettering.
    producer = Producer(settings.kafka_bootstrap, source="recorder")
    await producer.start()
    recorder = Recorder(session_factory, producer)

    consumer = AIOKafkaConsumer(
        TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT, TOPIC_RUN_DLQ,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id="recorder", enable_auto_commit=False,
    )
    await consumer.start()
    try:
        await consume_forever(consumer, producer,
                              lambda msg, data: recorder.handle(msg.topic, msg.key.decode() if msg.key else "", data))
    finally:
        await consumer.stop()
        await producer.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
