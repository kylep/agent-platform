"""Ingest consumer: the async half of event-sourced run creation. Reads
`run.inbound` (produced by webhooks, the scheduler, and conversation ingest) and
materializes each into a run. Idempotent via materialize_run, so redelivery is
safe."""
import logging

from aiokafka import AIOKafkaConsumer

from agentplatform.events import TOPIC_RUN_INBOUND, consume_forever
from agentplatform.materialize import materialize_run

log = logging.getLogger("ingest")


class Ingestor:
    def __init__(self, settings, session_factory, producer):
        self.settings = settings
        self.sf = session_factory
        self.producer = producer

    async def run_forever(self) -> None:
        consumer = AIOKafkaConsumer(
            TOPIC_RUN_INBOUND, bootstrap_servers=self.settings.kafka_bootstrap,
            group_id="ingest", enable_auto_commit=False,
        )
        await consumer.start()
        try:
            await consume_forever(
                consumer, self.producer,
                lambda msg, data: materialize_run(self.sf, self.producer, data),
            )
        finally:
            await consumer.stop()
