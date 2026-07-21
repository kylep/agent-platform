import pytest
from agentplatform.events import FakeProducer, TOPIC_RUN_REQUESTS, make_envelope, unwrap


@pytest.mark.asyncio
async def test_fake_records():
    p = FakeProducer()
    await p.start()
    await p.publish(TOPIC_RUN_REQUESTS, "abc", {"type": "run"}, type="run.request")
    assert p.published == [(TOPIC_RUN_REQUESTS, "abc", {"type": "run"})]
    env = p.envelopes[0]
    assert env["type"] == "run.request" and env["key"] == "abc" and env["data"] == {"type": "run"}
    await p.stop()


def test_envelope_roundtrip_and_legacy_tolerance():
    env = make_envelope(type="x.y", key="k", data={"a": 1}, source="test")
    got_env, data = unwrap(env)
    assert data == {"a": 1} and got_env["type"] == "x.y" and got_env["schema_version"] == 1
    # legacy un-enveloped message: treated as raw data
    legacy_env, legacy_data = unwrap({"type": "state", "run_id": "r"})
    assert legacy_data == {"type": "state", "run_id": "r"} and legacy_env["source"] == "legacy"
