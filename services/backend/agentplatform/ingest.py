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


class ToolAuditIngestor:
    """Consumes the broker's tool-audit events (docs/design/13 E) into the
    append-only tool_audit table. The broker stays credential-free — Kafka is
    the trust boundary (netpol: in-namespace producers only)."""

    def __init__(self, settings, session_factory, producer):
        self.settings = settings
        self.sf = session_factory
        self.producer = producer

    async def _record(self, data: dict) -> None:
        from agentplatform.db import ToolAudit
        async with self.sf() as s:
            s.add(ToolAudit(
                agent=str(data.get("agent") or "unknown")[:128],
                run_id=(data.get("run_id") or None),
                initiated_by=(data.get("initiated_by") or None),
                tool=str(data.get("tool") or "unknown")[:64],
                args_digest=str(data.get("args_digest") or "")[:64],
                decision=str(data.get("decision") or "unknown")[:64],
                latency_ms=int(data.get("latency_ms") or 0),
                result_bytes=int(data.get("result_bytes") or 0),
            ))
            await s.commit()

    async def run_forever(self) -> None:
        from agentplatform.events import TOPIC_TOOL_AUDIT
        consumer = AIOKafkaConsumer(
            TOPIC_TOOL_AUDIT, bootstrap_servers=self.settings.kafka_bootstrap,
            group_id="tool-audit", enable_auto_commit=False,
        )
        await consumer.start()
        try:
            await consume_forever(
                consumer, self.producer,
                lambda msg, data: self._record(data),
            )
        finally:
            await consumer.stop()
