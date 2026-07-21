"""The single place a Run row is created and handed to the dispatcher.

Both the synchronous command path (`POST /api/runs`) and the event-sourced
ingress (the `run.inbound` consumer) funnel through `materialize_run`, so run
creation has exactly one implementation. Idempotent on `run_id`, so a
redelivered inbound event is a no-op."""
import logging

from agentplatform.db import Run
from agentplatform.events import TOPIC_RUN_REQUESTS

log = logging.getLogger("materialize")


async def materialize_run(session_factory, producer, spec: dict) -> str:
    """Create the Run (idempotent on spec['run_id']) and publish run.requests.
    `spec` keys: run_id, agent, prompt, trigger, requested_by, and optional
    parent_run_id, depth, conversation_id. Returns the run id."""
    run_id = spec["run_id"]
    async with session_factory() as s:
        if await s.get(Run, run_id) is None:
            s.add(Run(
                id=run_id, agent=spec["agent"], prompt=spec["prompt"],
                trigger=spec["trigger"], requested_by=spec["requested_by"],
                parent_run_id=spec.get("parent_run_id"), depth=spec.get("depth", 0),
                conversation_id=spec.get("conversation_id"),
                user_message=spec.get("user_message"),
            ))
            await s.commit()
    try:
        await producer.publish(TOPIC_RUN_REQUESTS, run_id,
                               {"type": "run", "run_id": run_id}, type="run.request")
    except Exception:
        # Row is committed; the dispatcher's queued-run sweep drains it once
        # Kafka is reachable again.
        log.warning("publish failed for run %s; sweep will drain it", run_id)
    return run_id
