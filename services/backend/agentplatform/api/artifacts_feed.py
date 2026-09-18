"""The artifacts block, live (docs/design/23): one SSE stream off
`artifacts.events`, the wiki's feed shape — a single stream for the whole
platform, because an artifact is not in a room, and Kafka-fed only, because a
create is a change to the record rather than a line of conversation. What the
Studio's recent strip and the `[[artifact:]]` card draw from.

Its own module beside `api/artifacts.py` so the REST surface stays the store's
skin and this stays the feed's; both mount under `/api/artifacts`.
"""
import asyncio

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from agentplatform.api import relay as relay_api
from agentplatform.api.artifacts import require_artifacts_access
from agentplatform.api.relay import READ, Caller
from agentplatform.events import TOPIC_ARTIFACTS_EVENTS
from agentplatform.relay_feed import OVERFLOW, TopicFeed

router = APIRouter()

STREAM = "artifacts"


@router.get("/api/artifacts/events", response_class=StreamingResponse)
async def artifacts_stream(request: Request,
                           caller: Caller = Depends(require_artifacts_access(*READ))):
    """An `artifact` frame per `artifacts.event` — `{event: created | deleted |
    agent_image, artifact, agent}`, the envelope's data verbatim, metadata and
    never bytes — a heartbeat in between, and an `overflow` marker for a reader
    that fell too far behind for anything but a re-list to catch it up."""
    feed = request.app.state.artifacts_feed
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


def artifacts_feed(session_factory=None) -> TopicFeed:
    """The API's live artifact fan-out: the whole payload is the frame, since
    the strip wants the event name and who acted as much as the artifact."""
    return TopicFeed(TOPIC_ARTIFACTS_EVENTS, event="artifact",
                     frame_of=lambda data: ((STREAM, data) if _is_frame(data) else None),
                     session_factory=session_factory)


def _is_frame(data: dict) -> bool:
    """A record with an artifact, or an `agent_image` clear — the one record
    whose whole meaning is that there is no artifact any more."""
    return bool(data.get("artifact")) or data.get("event") == "agent_image"
