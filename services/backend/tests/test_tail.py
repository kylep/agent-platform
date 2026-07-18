import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from agentplatform.config import Settings
from agentplatform.api.app import create_app


def test_tail_requires_admin_session(producer, agent_store):
    async def fake_consumer():
        yield ("RUNID", {"type": "assistant", "seq": 1, "text": "hi"})

    app = create_app(
        Settings(agents_root=str(agent_store.root)),
        None,
        producer,
        agent_store=agent_store,
        consumer_factory=fake_consumer,
    )
    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with tc.websocket_connect("/api/runs/RUNID/tail") as ws:
                ws.receive_text()
        assert exc_info.value.code == 4401


def test_tail_replays_then_streams(producer, agent_store):
    async def fake_consumer():
        yield ("RUNID", {"type": "assistant", "seq": 1, "text": "hi"})
        yield ("RUNID", {"type": "lifecycle", "terminal": True, "state": "succeeded"})

    app = create_app(
        Settings(agents_root=str(agent_store.root)),
        None,
        producer,
        agent_store=agent_store,
        consumer_factory=fake_consumer,
    )
    with TestClient(app) as tc:
        tc.post("/api/setup", json={"password": "pw12345678"})
        tc.post("/api/login", json={"password": "pw12345678"})
        with tc.websocket_connect("/api/runs/RUNID/tail") as ws:
            assert ws.receive_json()["type"] == "assistant"
            assert ws.receive_json()["terminal"] is True
