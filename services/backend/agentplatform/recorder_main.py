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

    # A producer for conversation-outbound, result_topic feeds, and dead-lettering.
    producer = Producer(settings.kafka_bootstrap, source="recorder")
    await producer.start()
    from agentplatform.agents import AgentStore
    agent_store = AgentStore(session_factory)
    # Prime it: the recorder only ever reads (result_topic), so without a first
    # load its TTL refresh would land one frame too late.
    await agent_store.reload()
    recorder = Recorder(session_factory, producer, agent_store=agent_store)

    consumer = AIOKafkaConsumer(
        TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT, TOPIC_RUN_DLQ,
        bootstrap_servers=settings.kafka_bootstrap,
        group_id="recorder", enable_auto_commit=False,
    )
    await consumer.start()
    sweep = None
    if settings.reply_reconcile_seconds > 0:
        sweep = asyncio.create_task(_reply_sweep(recorder, settings.reply_reconcile_seconds))
    try:
        await consume_forever(consumer, producer,
                              lambda msg, data: recorder.handle(msg.topic, msg.key.decode() if msg.key else "", data))
    finally:
        if sweep is not None:
            sweep.cancel()
        await consumer.stop()
        await producer.stop()
        await engine.dispose()


async def _reply_sweep(recorder: Recorder, grace: int) -> None:
    """Backstop for conversation replies whose `result` frame never arrived.
    Never lets an exception kill the loop — it is a safety net, not a
    dependency of the consumers."""
    while True:
        await asyncio.sleep(grace)
        try:
            sent = await recorder.reconcile_replies(grace)
            if sent:
                log.warning("reply sweep published %d late conversation repl(ies)", sent)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reply sweep failed; retrying next tick")


if __name__ == "__main__":
    asyncio.run(main())
