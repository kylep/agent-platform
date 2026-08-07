"""The single place a Run row is created and handed to the dispatcher.

Both the synchronous command path (`POST /api/runs`) and the event-sourced
ingress (the `run.inbound` consumer) funnel through `materialize_run`, so run
creation has exactly one implementation. Idempotent on `run_id`, so a
redelivered inbound event is a no-op."""
import asyncio
import logging

from agentplatform.db import Run
from agentplatform.events import TOPIC_RUN_REQUESTS

log = logging.getLogger("materialize")

# Fail the run.requests publish fast so POST /api/runs doesn't hang on a down
# broker (aiokafka's send otherwise blocks ~40s → an nginx 504). The row is
# already committed, so a timed-out publish just falls to the sweep.
PUBLISH_TIMEOUT_SECONDS = 5.0


async def materialize_run(session_factory, producer, spec: dict,
                          publish_timeout: float = PUBLISH_TIMEOUT_SECONDS) -> str:
    """Create the Run (idempotent on spec['run_id']) and publish run.requests.
    `spec` keys: run_id, agent, prompt, trigger, requested_by, and optional
    initiated_by (root principal, defaults to "admin" — the single-operator
    stub of docs/design/13 D), parent_run_id, depth, conversation_id.
    Returns the run id.

    Postgres-first: the row is committed before the publish, and a failed/slow
    publish is swallowed — the run is `queued` and the dispatcher's queued-run
    sweep drains it once Kafka is reachable (see Dispatcher.sweep_queued)."""
    run_id = spec["run_id"]
    async with session_factory() as s:
        if await s.get(Run, run_id) is None:
            s.add(Run(
                id=run_id, agent=spec["agent"], prompt=spec["prompt"],
                trigger=spec["trigger"], requested_by=spec["requested_by"],
                initiated_by=spec.get("initiated_by") or "admin",
                parent_run_id=spec.get("parent_run_id"), depth=spec.get("depth", 0),
                conversation_id=spec.get("conversation_id"),
                user_message=spec.get("user_message"),
            ))
            await s.commit()
    try:
        await asyncio.wait_for(
            producer.publish(TOPIC_RUN_REQUESTS, run_id,
                             {"type": "run", "run_id": run_id}, type="run.request"),
            timeout=publish_timeout)
    except (Exception, asyncio.TimeoutError):
        log.warning("publish failed/timed out for run %s; sweep will drain it", run_id)
    return run_id
