import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from agentplatform.config import get_settings
from agentplatform.db import init_db, make_engine, make_session_factory
from agentplatform.events import TOPIC_RUN_DLQ, TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT
from agentplatform.recorder import Recorder

log = logging.getLogger("recorder_main")


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    engine = make_engine(settings.db_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    recorder = Recorder(session_factory)

    consumer = AIOKafkaConsumer(
        TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT, TOPIC_RUN_DLQ,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id="recorder", enable_auto_commit=False,
    )
    await consumer.start()
    try:
        async for msg in consumer:
            try:
                await recorder.handle(msg.topic, msg.key.decode(), json.loads(msg.value))
            except Exception:
                log.exception("handle failed")
            await consumer.commit()
    finally:
        await consumer.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
