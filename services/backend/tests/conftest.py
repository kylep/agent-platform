import pytest, httpx
from agentplatform.agents import AgentStore
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory, init_db
from agentplatform.events import FakeProducer
from agentplatform.secrets import InMemorySecretStore
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
def secret_store():
    return InMemorySecretStore()

@pytest.fixture
def tmp_agents(tmp_path):
    d = tmp_path / "hello-world"
    d.mkdir(parents=True)
    (d / "agent.md").write_text("# hello-world\nYou are hello-world.")
    (d / "manifest.yaml").write_text("description: test\n")
    return tmp_path

@pytest.fixture
def agent_store(tmp_agents):
    return AgentStore(tmp_agents)

@pytest.fixture
async def client(sf, producer, secret_store, agent_store):
    app = create_app(Settings(agents_root=str(agent_store.root)), sf, producer,
                      secret_store=secret_store, agent_store=agent_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c

@pytest.fixture
async def admin_client(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    await client.post("/api/login", json={"password": "pw12345678"})
    return client
