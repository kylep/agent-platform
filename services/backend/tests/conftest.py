from pathlib import Path

import pytest, httpx
from agentplatform.agents import AgentStore

# The real repo secrets/ + skills/ + reports/ trees — tests run against the
# shipped declarations so the files themselves are under test.
REPO_ROOT = Path(__file__).resolve().parents[3]
REPO_SECRETS = REPO_ROOT / "secrets"
REPO_SKILLS = REPO_ROOT / "skills"
REPO_REPORTS = REPO_ROOT / "reports"
REPO_APPS = REPO_ROOT / "apps"
REPO_TOOLS = REPO_ROOT / "tools"
from agentplatform.config import Settings
from agentplatform.db import make_engine, make_session_factory, init_db
from agentplatform.events import FakeProducer
from agentplatform.secrets import InMemorySecretStore
from agentplatform.api.app import create_app

@pytest.fixture(autouse=True)
def _no_ambient_settings_env(monkeypatch):
    """This pod's own real environment sets AP_CLAUDE_PROXY_URL (it runs behind
    a claude-proxy) and AP_KAFKA_BOOTSTRAP (it points at a real broker). Left
    alone, `Settings()` picks both up in tests that build one with no override,
    flipping K8sJobLauncher onto the proxied-credentials branch and
    kafka_health onto a real (unreachable from here) broker — failures that
    have nothing to do with the code under test. A test that wants either
    non-default still sets it itself via monkeypatch or an explicit Settings()
    kwarg, which wins over this fixture."""
    monkeypatch.delenv("AP_CLAUDE_PROXY_URL", raising=False)
    monkeypatch.delenv("AP_KAFKA_BOOTSTRAP", raising=False)

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
def tmp_checkout(tmp_path):
    """Stands in for the synced git checkout (`/agents` in the cluster). What
    the platform still reads from it is capability-as-code — skills, secret
    declarations, tools, reports, apps — plus the docs the Help pages serve and
    the `.git` that /api/sync-status reports. Agent definitions are rows
    (docs/design/15) and the `agents/` directory is gone."""
    d = tmp_path / "checkout"
    d.mkdir()
    return d

@pytest.fixture
def seed_agent(sf):
    """Insert (or update) an agent definition row — the DB-first replacement
    for writing an `agents/<name>/` tree. Returns an async callable so a test
    can add an agent mid-test and reload the store."""
    from agentplatform.db import AgentDef

    async def _seed(name: str, **fields):
        async with sf() as s:
            row = await s.get(AgentDef, name) or AgentDef(name=name)
            fields.setdefault("prompt", f"# {name}\nYou are {name}.")
            for k, v in fields.items():
                setattr(row, k, v)
            s.add(row)
            await s.commit()
    return _seed

@pytest.fixture
async def agent_store(sf, seed_agent):
    """The shared store, pre-seeded with `hello-world` (the agent most suites
    assume exists). Seed more with `seed_agent` and `await store.reload()`."""
    await seed_agent("hello-world", description="test")
    store = AgentStore(sf)
    await store.reload()
    return store

@pytest.fixture
async def client(sf, producer, secret_store, agent_store, tmp_checkout):
    app = create_app(Settings(checkout_root=str(tmp_checkout),
                              secrets_root=str(REPO_SECRETS),
                              skills_root=str(REPO_SKILLS),
                              reports_root=str(REPO_REPORTS),
                              apps_root=str(REPO_APPS),
                              tools_root=str(REPO_TOOLS)), sf, producer,
                      secret_store=secret_store, agent_store=agent_store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        yield c

@pytest.fixture
async def admin_client(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    await client.post("/api/login", json={"password": "pw12345678"})
    return client

@pytest.fixture
async def token_client(client):
    """A second client over the same app carrying no session cookie:
    `authenticate` tries the cookie before the bearer, so a bearer token is
    only really under test on a request that has nothing else. `client` and
    `admin_client` share one cookie jar, so a test that logs in and then sends
    a bearer through `client` is testing the cookie."""
    async with httpx.AsyncClient(transport=client._transport, base_url="http://t") as c:
        yield c
