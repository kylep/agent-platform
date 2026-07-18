import pytest, httpx
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory, init_db
from agentplatform.events import FakeProducer
from agentplatform.api.app import create_app

@pytest.fixture
async def sf():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_session_factory(engine)
    await engine.dispose()

@pytest.fixture
def producer():
    return FakeProducer()

@pytest.fixture
async def client(sf, producer):
    app = create_app(Settings(), sf, producer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c

@pytest.fixture
async def admin_client(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    await client.post("/api/login", json={"password": "pw12345678"})
    return client
