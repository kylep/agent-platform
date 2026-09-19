"""The Workbench, live (docs/design/24): one SSE stream off `workbench.events`,
the tickets shape — a single stream for the whole platform, Kafka-fed only,
because a publish is a change to the record and not a line of conversation.
What the Changes page draws its live rows from.

Admin-only, like `/api/pull-requests`: the page it feeds is the admin's.
"""
import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from agentplatform.api import relay as relay_api
from agentplatform.api.auth import require_admin
from agentplatform.events import TOPIC_WORKBENCH_EVENTS
from agentplatform.relay_feed import OVERFLOW, TopicFeed

router = APIRouter()

STREAM = "workbench"


@router.get("/api/workbench/events", response_class=StreamingResponse,
            dependencies=[Depends(require_admin)])
async def workbench_stream(request: Request):
    """A `workbench` frame per `workbench.event` — `{event: published |
    verify_failed | refused, agent, run_id, ticket_key, branch, pr, paths,
    tests_removed, verify, reason}`, the envelope's data verbatim — a
    heartbeat in between, and an `overflow` marker for a reader that fell too
    far behind for anything but a refetch to catch it up."""
    feed = request.app.state.workbench_feed
    queue = feed.subscribe(STREAM)

    async def stream():
        try:
            while True:
                try:
                    # Read per wait: a module global a test turns down.
                    event, data = await asyncio.wait_for(
                        queue.get(), relay_api.HEARTBEAT_SECONDS)
                except TimeoutError:
                    yield ": heartbeat\n\n"
                    continue
                if event == OVERFLOW:
                    yield relay_api._frame(OVERFLOW, {})
                    continue
                yield relay_api._frame(event, data)
        finally:
            feed.unsubscribe(STREAM, queue)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def workbench_feed(session_factory=None) -> TopicFeed:
    """The API's live publish fan-out: the whole payload is the frame."""
    return TopicFeed(TOPIC_WORKBENCH_EVENTS, event=STREAM,
                     frame_of=lambda data: ((STREAM, data) if data.get("event") else None),
                     session_factory=session_factory)
