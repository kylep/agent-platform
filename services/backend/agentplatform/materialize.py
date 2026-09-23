"""The single place a Run row is created and handed to the dispatcher.

Both the synchronous command path (`POST /api/runs`) and the event-sourced
ingress (the `run.inbound` consumer) funnel through `materialize_run`, so run
creation has exactly one implementation. Idempotent on `run_id`, so a
redelivered inbound event is a no-op."""
import asyncio
import logging

from agentplatform.db import Conversation, Project, Run, Team
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
    stub of docs/design/13 D), parent_run_id, depth, conversation_id,
    trigger_message_id, ticket_id.
    Returns the run id.

    Postgres-first: the row is committed before the publish, and a failed/slow
    publish is swallowed — the run is `queued` and the dispatcher's queued-run
    sweep drains it once Kafka is reachable (see Dispatcher.sweep_queued)."""
    run_id = spec["run_id"]
    async with session_factory() as s:
        if await s.get(Run, run_id) is None:
            parent = await s.get(Run, spec["parent_run_id"]) if spec.get("parent_run_id") else None
            room = await s.get(Conversation, spec["conversation_id"]) if spec.get("conversation_id") else None
            team_id = spec.get("team_id") or (room.team_id if room else None) or (parent.team_id if parent else None)
            project_id = spec.get("project_id") or (room.project_id if room else None) or (parent.project_id if parent else None)
            context = []
            if team_id:
                team = await s.get(Team, team_id)
                if team:
                    context.append(f"Team: {team.name} ({team.slug}). {team.description}".strip())
            if project_id:
                project = await s.get(Project, project_id)
                if project:
                    context.append(f"Project: {project.name} ({project.slug}). {project.description}".strip())
                    context.append(
                        "To recall earlier Project conversations, use Relay search with "
                        f"project={project.slug!r} and specific terms, then read relevant "
                        "rooms or threads. Search respects room visibility. If Relay is "
                        "unavailable, continue with current context and say so.")
            prompt = spec["prompt"]
            if context:
                prompt = "<work-context>\n" + "\n".join(context) + "\n</work-context>\n\n" + prompt
            s.add(Run(
                id=run_id, agent=spec["agent"], prompt=prompt,
                trigger=spec["trigger"], requested_by=spec["requested_by"],
                initiated_by=spec.get("initiated_by") or "admin",
                parent_run_id=spec.get("parent_run_id"), depth=spec.get("depth", 0),
                conversation_id=spec.get("conversation_id"),
                team_id=team_id, project_id=project_id,
                user_message=spec.get("user_message"),
                trigger_message_id=spec.get("trigger_message_id"),
                ticket_id=spec.get("ticket_id"),
                requested_model=spec.get("model") or "",
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
