import pytest
from agentplatform.events import FakeProducer, TOPIC_RUN_REQUESTS


@pytest.mark.asyncio
async def test_fake_records():
    p = FakeProducer()
    await p.start()
    await p.publish(TOPIC_RUN_REQUESTS, "abc", {"type": "run"})
    assert p.published == [(TOPIC_RUN_REQUESTS, "abc", {"type": "run"})]
    await p.stop()
