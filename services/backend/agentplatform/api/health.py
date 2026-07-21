import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from agentplatform.api.auth import READ_ROLES, require_role
from agentplatform.db import Run, RunState
from agentplatform.events import ALL_TOPICS, TOPIC_RUN_REQUESTS

log = logging.getLogger("health")

router = APIRouter()


async def _dispatcher_lag(bootstrap: str) -> int | None:
    """Best-effort consumer lag for the dispatcher group on run.requests:
    end offsets minus the group's committed offsets. Returns None if it can't
    be determined (fuller lag metrics land with observability in M05)."""
    from aiokafka import AIOKafkaConsumer, TopicPartition
    from aiokafka.admin import AIOKafkaAdminClient

    consumer = AIOKafkaConsumer(bootstrap_servers=bootstrap, enable_auto_commit=False)
    await consumer.start()
    try:
        parts = consumer.partitions_for_topic(TOPIC_RUN_REQUESTS)
        if not parts:
            return None
        tps = [TopicPartition(TOPIC_RUN_REQUESTS, p) for p in parts]
        end = await consumer.end_offsets(tps)
    finally:
        await consumer.stop()

    admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap)
    await admin.start()
    try:
        committed = await admin.list_consumer_group_offsets("dispatcher")
    finally:
        await admin.close()

    total = 0
    for tp in tps:
        meta = committed.get(tp)
        offset = meta.offset if meta and meta.offset >= 0 else 0
        total += max(0, end.get(tp, 0) - offset)
    return total


@router.get("/api/health/kafka", dependencies=[Depends(require_role(*READ_ROLES))])
async def kafka_health(request: Request):
    """Broker liveness + expected-topic presence, a best-effort dispatcher lag,
    and a DB-derived run backlog (queued/active/dlq). Powers the dashboard's
    Kafka health panel; deeper metrics wait for the M05 observability pass."""
    settings = request.app.state.settings
    bootstrap = settings.kafka_bootstrap
    result: dict = {"reachable": False, "topics": [], "missing_topics": [],
                    "lag": None, "error": None}

    async with request.app.state.session_factory() as s:
        queued = (await s.execute(select(func.count()).select_from(Run)
                  .where(Run.state == RunState.QUEUED))).scalar_one()
        active = (await s.execute(select(func.count()).select_from(Run)
                  .where(Run.state.in_([RunState.DISPATCHED, RunState.RUNNING])))).scalar_one()
        dlq = (await s.execute(select(func.count()).select_from(Run)
               .where(Run.state == RunState.DLQ))).scalar_one()
    result["backlog"] = {"queued": queued, "active": active, "dlq": dlq}

    try:
        from aiokafka.admin import AIOKafkaAdminClient
        admin = AIOKafkaAdminClient(bootstrap_servers=bootstrap, request_timeout_ms=5000)
        await admin.start()
        try:
            topics = await admin.list_topics()
            result["reachable"] = True
            result["topics"] = sorted(topics)
            result["missing_topics"] = [t for t in ALL_TOPICS if t not in topics]
            try:
                result["lag"] = await _dispatcher_lag(bootstrap)
            except Exception as e:
                log.warning("dispatcher lag probe failed: %s", e)
        finally:
            await admin.close()
    except Exception as e:
        result["error"] = str(e)

    return result
