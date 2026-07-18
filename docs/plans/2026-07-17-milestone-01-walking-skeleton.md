# Milestone 01 — Walking Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `helm install` brings up the whole platform; a hello-world agent runs end-to-end from a UI click with a live-streaming transcript.

**Architecture:** One Python package (`agentplatform`) provides three entrypoints — api (FastAPI), dispatcher (Kafka consumer that creates k8s Jobs), recorder (Kafka consumer that persists to postgres) — sharing models, config, and Kafka helpers. A separate runner image wraps the claude CLI and relays stream-json to Kafka. A React SPA (served by nginx, proxying `/api`) is the UI. Postgres is written first for every run; Kafka carries commands and events.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 (async) + asyncpg (aiosqlite in tests), aiokafka, kubernetes (python client), argon2-cffi, itsdangerous, pytest + pytest-asyncio + httpx; Vite + React 18 + TypeScript + react-router; Helm with Bitnami postgresql + kafka (KRaft) dependencies; claude CLI pinned 2.1.214.

## Global Constraints

- **Subscription auth only.** No `ANTHROPIC_API_KEY`, no `sk-ant-` anywhere. CI greps and fails.
- Claude CLI pinned to `2.1.214` in the runner image.
- Python `>=3.12`; all backend code type-hinted; async throughout the api.
- Kafka topics: `run.requests`, `run.events`, `run.transcript`, `run.dlq` (exact names, used everywhere via constants).
- Run states: `queued dispatched running succeeded failed timed_out killed rejected dlq` (exact strings).
- Defaults: global concurrency 3, per-agent concurrency 1, run timeout 1800s.
- Postgres row is written **before** any Kafka publish, always.
- Env vars are prefixed `AP_` (e.g. `AP_DB_URL`).
- Git is source of truth for agent definitions; the DB never stores them.
- Monorepo layout per `docs/design/00-overview.md`: `services/backend`, `services/runner`, `services/web`, `charts/agent-platform`, `agents/`, `bin/`.
- Commit after every task at minimum; conventional-commit style subjects.

---

### Task 1: Backend scaffold + Settings

**Files:**
- Create: `services/backend/pyproject.toml`
- Create: `services/backend/agentplatform/__init__.py` (empty)
- Create: `services/backend/agentplatform/config.py`
- Create: `services/backend/tests/__init__.py` (empty)
- Test: `services/backend/tests/test_config.py`

**Interfaces:**
- Produces: `agentplatform.config.Settings` — pydantic-settings class, env prefix `AP_`, fields: `db_url: str = "sqlite+aiosqlite:///:memory:"`, `kafka_bootstrap: str = "localhost:9092"`, `k8s_namespace: str = "agent-platform"`, `runner_image: str = "agent-platform-runner:dev"`, `agents_root: str = "./agents"`, `session_secret: str = "dev-insecure"`, `global_concurrency: int = 3`, `run_timeout_seconds: int = 1800`. Plus `get_settings()` returning a cached instance.

- [ ] **Step 1: Write pyproject**

```toml
# services/backend/pyproject.toml
[project]
name = "agentplatform"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115", "uvicorn[standard]>=0.30", "sqlalchemy[asyncio]>=2.0",
  "asyncpg>=0.29", "aiosqlite>=0.20", "aiokafka>=0.11", "kubernetes>=30.1",
  "pydantic-settings>=2.4", "argon2-cffi>=23.1", "itsdangerous>=2.2",
  "pyyaml>=6.0", "websockets>=12.0",
]
[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "httpx>=0.27"]
[tool.pytest.ini_options]
asyncio_mode = "auto"
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
include = ["agentplatform*"]
```

- [ ] **Step 2: Write the failing test**

```python
# services/backend/tests/test_config.py
from agentplatform.config import Settings

def test_defaults():
    s = Settings()
    assert s.global_concurrency == 3
    assert s.run_timeout_seconds == 1800

def test_env_override(monkeypatch):
    monkeypatch.setenv("AP_GLOBAL_CONCURRENCY", "5")
    assert Settings().global_concurrency == 5
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd services/backend && python -m venv .venv && .venv/bin/pip install -e '.[dev]' && .venv/bin/pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError` / `ImportError` for `agentplatform.config`.

- [ ] **Step 4: Implement config**

```python
# services/backend/agentplatform/config.py
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AP_")
    db_url: str = "sqlite+aiosqlite:///:memory:"
    kafka_bootstrap: str = "localhost:9092"
    k8s_namespace: str = "agent-platform"
    runner_image: str = "agent-platform-runner:dev"
    agents_root: str = "./agents"
    session_secret: str = "dev-insecure"
    global_concurrency: int = 3
    run_timeout_seconds: int = 1800

@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Run tests, verify pass**

Run: `.venv/bin/pytest tests/test_config.py -v` — Expected: 2 PASS.

- [ ] **Step 6: Commit**

```bash
git add services/backend && git commit -m "feat(backend): scaffold agentplatform package with settings"
```

---

### Task 2: Database models + session factory

**Files:**
- Create: `services/backend/agentplatform/db.py`
- Test: `services/backend/tests/test_db.py`

**Interfaces:**
- Produces: `RunState` (StrEnum with the nine states from Global Constraints); SQLAlchemy models `Run`, `TranscriptEvent`, `Principal`, `SecretMeta`; `make_engine(db_url) -> AsyncEngine`; `make_session_factory(engine) -> async_sessionmaker`; `async init_db(engine)` (create_all).
- `Run` columns: `id: str` pk (uuid4 hex), `agent: str`, `trigger: str`, `requested_by: str`, `state: str` default `queued`, `prompt: str`, `created_at/started_at/finished_at: datetime | None` (created_at defaults now-utc), `exit_code: int | None`, `error: str | None`, `tokens_in: int = 0`, `tokens_out: int = 0`, `tool_calls: int = 0`.
- `TranscriptEvent`: `run_id: str` pk-part, `seq: int` pk-part, `payload: JSON`.
- `Principal`: `id: str` pk, `name: str` unique, `role: str`, `password_hash: str | None`.
- `SecretMeta`: `name: str` pk, `status: str` default `"missing"` (`missing|unprobed|ok|invalid`), `updated_at: datetime`.

- [ ] **Step 1: Write the failing test**

```python
# services/backend/tests/test_db.py
import pytest
from sqlalchemy import select
from agentplatform.db import Run, RunState, make_engine, make_session_factory, init_db

@pytest.fixture
async def sf():
    engine = make_engine("sqlite+aiosqlite:///:memory:")
    await init_db(engine)
    yield make_session_factory(engine)
    await engine.dispose()

async def test_run_defaults(sf):
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="admin", prompt="hi")
        s.add(run); await s.commit()
        got = (await s.execute(select(Run))).scalar_one()
        assert got.state == RunState.QUEUED == "queued"
        assert len(got.id) == 32 and got.created_at is not None
```

- [ ] **Step 2: Run, expect FAIL** — `ImportError`.

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/db.py
import uuid
from datetime import datetime, timezone
from enum import StrEnum
from sqlalchemy import JSON, DateTime, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

class RunState(StrEnum):
    QUEUED = "queued"; DISPATCHED = "dispatched"; RUNNING = "running"
    SUCCEEDED = "succeeded"; FAILED = "failed"; TIMED_OUT = "timed_out"
    KILLED = "killed"; REJECTED = "rejected"; DLQ = "dlq"

ACTIVE_STATES = (RunState.QUEUED, RunState.DISPATCHED, RunState.RUNNING)

class Base(DeclarativeBase): pass

class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    agent: Mapped[str] = mapped_column(String(128))
    trigger: Mapped[str] = mapped_column(String(32))
    requested_by: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default=RunState.QUEUED)
    prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    tool_calls: Mapped[int] = mapped_column(Integer, default=0)

class TranscriptEvent(Base):
    __tablename__ = "run_transcript_events"
    run_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    payload: Mapped[dict] = mapped_column(JSON)

class Principal(Base):
    __tablename__ = "principals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    name: Mapped[str] = mapped_column(String(128), unique=True)
    role: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str | None] = mapped_column(String(256), nullable=True)

class SecretMeta(Base):
    __tablename__ = "secrets_meta"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="missing")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

def make_engine(db_url: str) -> AsyncEngine:
    return create_async_engine(db_url)

def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)

async def init_db(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

- [ ] **Step 4: Run tests, verify pass** — `.venv/bin/pytest tests/test_db.py -v`.

- [ ] **Step 5: Commit** — `git commit -m "feat(backend): db models, run state machine, session factory"`.

---

### Task 3: Kafka topics + producer (with fake)

**Files:**
- Create: `services/backend/agentplatform/events.py`
- Test: `services/backend/tests/test_events.py`

**Interfaces:**
- Produces: constants `TOPIC_RUN_REQUESTS = "run.requests"`, `TOPIC_RUN_EVENTS = "run.events"`, `TOPIC_RUN_TRANSCRIPT = "run.transcript"`, `TOPIC_RUN_DLQ = "run.dlq"`; `class Producer` with `async start()`, `async stop()`, `async publish(topic: str, key: str, value: dict) -> None` (JSON-serializes value, key utf-8); `class FakeProducer(Producer)` recording `.published: list[tuple[topic, key, value]]`.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_events.py
from agentplatform.events import FakeProducer, TOPIC_RUN_REQUESTS

async def test_fake_records():
    p = FakeProducer()
    await p.start()
    await p.publish(TOPIC_RUN_REQUESTS, "abc", {"type": "run"})
    assert p.published == [(TOPIC_RUN_REQUESTS, "abc", {"type": "run"})]
    await p.stop()
```

- [ ] **Step 2: Run, expect FAIL** (ImportError).

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/events.py
import json
from aiokafka import AIOKafkaProducer

TOPIC_RUN_REQUESTS = "run.requests"
TOPIC_RUN_EVENTS = "run.events"
TOPIC_RUN_TRANSCRIPT = "run.transcript"
TOPIC_RUN_DLQ = "run.dlq"
ALL_TOPICS = [TOPIC_RUN_REQUESTS, TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT, TOPIC_RUN_DLQ]

class Producer:
    def __init__(self, bootstrap: str = "localhost:9092"):
        self._bootstrap = bootstrap
        self._p: AIOKafkaProducer | None = None
    async def start(self) -> None:
        self._p = AIOKafkaProducer(bootstrap_servers=self._bootstrap)
        await self._p.start()
    async def stop(self) -> None:
        if self._p: await self._p.stop()
    async def publish(self, topic: str, key: str, value: dict) -> None:
        assert self._p, "producer not started"
        await self._p.send_and_wait(topic, json.dumps(value).encode(), key=key.encode())

class FakeProducer(Producer):
    def __init__(self):
        self.published: list[tuple[str, str, dict]] = []
    async def start(self) -> None: pass
    async def stop(self) -> None: pass
    async def publish(self, topic: str, key: str, value: dict) -> None:
        self.published.append((topic, key, value))
```

- [ ] **Step 4: Run tests pass; Step 5: Commit** — `git commit -m "feat(backend): kafka topic constants and producer with test fake"`.

---

### Task 4: API app factory, setup + auth

**Files:**
- Create: `services/backend/agentplatform/api/__init__.py` (empty)
- Create: `services/backend/agentplatform/api/app.py`
- Create: `services/backend/agentplatform/api/auth.py`
- Create: `services/backend/tests/conftest.py`
- Test: `services/backend/tests/test_auth.py`

**Interfaces:**
- Produces: `create_app(settings, session_factory, producer, secret_store, agent_store) -> FastAPI` (later tasks add routers to it; store args typed as the classes from Tasks 5–6 — during this task, accept them as `object | None = None`).
- `app.state` carries: `settings`, `session_factory`, `producer`, `secret_store`, `agent_store`.
- Auth endpoints: `GET /api/setup-state` → `{"needs_admin": bool}` (no auth). `POST /api/setup` `{"password": str}` → 200 once, 409 after. `POST /api/login` `{"password": str}` → 200 + signed `ap_session` cookie, 401 on bad. `POST /api/logout`. Dependency `require_admin` (reads cookie, 401 otherwise) for all other routes.
- Conftest produces reusable fixtures: `sf` (sqlite session factory), `producer` (FakeProducer), `client` (httpx AsyncClient over the app).

- [ ] **Step 1: Conftest**

```python
# services/backend/tests/conftest.py
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
```

- [ ] **Step 2: Failing test**

```python
# services/backend/tests/test_auth.py
async def test_setup_flow(client):
    r = await client.get("/api/setup-state")
    assert r.json()["needs_admin"] is True
    assert (await client.post("/api/setup", json={"password": "pw12345678"})).status_code == 200
    assert (await client.post("/api/setup", json={"password": "x"})).status_code == 409
    assert (await client.get("/api/setup-state")).json()["needs_admin"] is False

async def test_login_required_and_works(client):
    await client.post("/api/setup", json={"password": "pw12345678"})
    assert (await client.get("/api/runs")).status_code == 401
    assert (await client.post("/api/login", json={"password": "wrong"})).status_code == 401
    assert (await client.post("/api/login", json={"password": "pw12345678"})).status_code == 200
```

(The `/api/runs` 401 check needs a placeholder authed route this task: add `GET /api/runs` returning `[]` behind `require_admin`; Task 7 replaces its body.)

- [ ] **Step 3: Run, expect FAIL.**

- [ ] **Step 4: Implement**

```python
# services/backend/agentplatform/api/auth.py
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request, Response, Depends
from itsdangerous import BadSignature, URLSafeSerializer
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.db import Principal

ph = PasswordHasher()
router = APIRouter()

class Creds(BaseModel):
    password: str

def _signer(request: Request) -> URLSafeSerializer:
    return URLSafeSerializer(request.app.state.settings.session_secret, salt="ap-session")

async def _admin(request: Request) -> Principal | None:
    async with request.app.state.session_factory() as s:
        return (await s.execute(select(Principal).where(Principal.name == "admin"))).scalar_one_or_none()

async def require_admin(request: Request) -> str:
    cookie = request.cookies.get("ap_session")
    if not cookie:
        raise HTTPException(401)
    try:
        data = _signer(request).loads(cookie)
    except BadSignature:
        raise HTTPException(401)
    return data["principal"]

@router.get("/api/setup-state")
async def setup_state(request: Request):
    return {"needs_admin": await _admin(request) is None}

@router.post("/api/setup")
async def setup(request: Request, creds: Creds):
    if await _admin(request) is not None:
        raise HTTPException(409, "already set up")
    async with request.app.state.session_factory() as s:
        s.add(Principal(name="admin", role="admin", password_hash=ph.hash(creds.password)))
        await s.commit()
    return {"ok": True}

@router.post("/api/login")
async def login(request: Request, response: Response, creds: Creds):
    admin = await _admin(request)
    if admin is None:
        raise HTTPException(401)
    try:
        ph.verify(admin.password_hash, creds.password)
    except VerifyMismatchError:
        raise HTTPException(401)
    response.set_cookie("ap_session", _signer(request).dumps({"principal": "admin"}),
                        httponly=True, samesite="lax")
    return {"ok": True}

@router.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("ap_session")
    return {"ok": True}
```

```python
# services/backend/agentplatform/api/app.py
from fastapi import Depends, FastAPI
from agentplatform.api import auth

def create_app(settings, session_factory, producer, secret_store=None, agent_store=None) -> FastAPI:
    app = FastAPI(title="agent-platform", version="0.1.0")
    st = app.state
    st.settings, st.session_factory, st.producer = settings, session_factory, producer
    st.secret_store, st.agent_store = secret_store, agent_store
    app.include_router(auth.router)

    @app.get("/api/runs", dependencies=[Depends(auth.require_admin)])
    async def list_runs_placeholder():  # replaced in Task 7
        return []
    return app
```

- [ ] **Step 5: Run all tests pass; Step 6: Commit** — `git commit -m "feat(api): app factory, first-run setup, admin session auth"`.

---

### Task 5: Secret store + secrets API

**Files:**
- Create: `services/backend/agentplatform/secrets.py`
- Create: `services/backend/agentplatform/api/secrets.py`
- Modify: `services/backend/agentplatform/api/app.py` (include router; default `secret_store` to `InMemorySecretStore()` when None)
- Modify: `services/backend/tests/conftest.py` (pass `InMemorySecretStore()` into `create_app`, expose as fixture `secret_store`)
- Test: `services/backend/tests/test_secrets.py`

**Interfaces:**
- Produces: `class SecretStore` (abstract): `async set(name: str, data: dict[str, str])`, `async get(name: str) -> dict[str, str] | None`, `async exists(name: str) -> bool`. `class InMemorySecretStore(SecretStore)` (dict-backed). `class K8sSecretStore(SecretStore)` (namespace-scoped Opaque secrets, base64 handled, `kubernetes.client.CoreV1Api` injected so it's testable).
- REQUIRED_SECRETS = `["claude-credentials"]`.
- Endpoints (admin): `GET /api/secrets` → `[{"name", "status", "required": bool}]` (status from `SecretMeta`, `missing` if no row; every REQUIRED_SECRETS name always listed). `PUT /api/secrets/{name}` body `{"data": {k: v}}` → stores, upserts meta status=`unprobed`. Values are never returned by any endpoint.
- `GET /api/setup-state` extended → `{"needs_admin", "secrets": [same as GET /api/secrets]}` (unauthenticated but value-free).

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_secrets.py
async def test_secret_lifecycle(admin_client, secret_store):
    r = await admin_client.get("/api/secrets")
    assert r.json() == [{"name": "claude-credentials", "status": "missing", "required": True}]
    r = await admin_client.put("/api/secrets/claude-credentials",
                               json={"data": {"credentials.json": "{\"tok\":1}"}})
    assert r.status_code == 200
    assert await secret_store.get("claude-credentials") == {"credentials.json": "{\"tok\":1}"}
    r = await admin_client.get("/api/secrets")
    assert r.json()[0]["status"] == "unprobed"

async def test_setup_state_includes_secrets(client):
    assert client is not None
    r = await client.get("/api/setup-state")
    assert r.json()["secrets"][0]["name"] == "claude-credentials"
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/secrets.py
import base64
from kubernetes import client as k8s

REQUIRED_SECRETS = ["claude-credentials"]

class SecretStore:
    async def set(self, name: str, data: dict[str, str]) -> None: raise NotImplementedError
    async def get(self, name: str) -> dict[str, str] | None: raise NotImplementedError
    async def exists(self, name: str) -> bool:
        return await self.get(name) is not None

class InMemorySecretStore(SecretStore):
    def __init__(self): self._d: dict[str, dict[str, str]] = {}
    async def set(self, name, data): self._d[name] = dict(data)
    async def get(self, name): return self._d.get(name)

class K8sSecretStore(SecretStore):
    def __init__(self, core: k8s.CoreV1Api, namespace: str):
        self._core, self._ns = core, namespace
    async def set(self, name, data):
        body = k8s.V1Secret(metadata=k8s.V1ObjectMeta(name=name),
                            string_data=data, type="Opaque")
        try:
            self._core.replace_namespaced_secret(name, self._ns, body)
        except k8s.exceptions.ApiException as e:
            if e.status != 404: raise
            self._core.create_namespaced_secret(self._ns, body)
    async def get(self, name):
        try:
            sec = self._core.read_namespaced_secret(name, self._ns)
        except k8s.exceptions.ApiException as e:
            if e.status == 404: return None
            raise
        return {k: base64.b64decode(v).decode() for k, v in (sec.data or {}).items()}
```

```python
# services/backend/agentplatform/api/secrets.py
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import require_admin
from agentplatform.db import SecretMeta
from agentplatform.secrets import REQUIRED_SECRETS

router = APIRouter()

class SecretIn(BaseModel):
    data: dict[str, str]

async def secret_listing(request: Request) -> list[dict]:
    async with request.app.state.session_factory() as s:
        rows = {m.name: m.status for m in (await s.execute(select(SecretMeta))).scalars()}
    names = sorted(set(REQUIRED_SECRETS) | set(rows))
    return [{"name": n, "status": rows.get(n, "missing"), "required": n in REQUIRED_SECRETS}
            for n in names]

@router.get("/api/secrets", dependencies=[Depends(require_admin)])
async def list_secrets(request: Request):
    return await secret_listing(request)

@router.put("/api/secrets/{name}", dependencies=[Depends(require_admin)])
async def put_secret(request: Request, name: str, body: SecretIn):
    await request.app.state.secret_store.set(name, body.data)
    async with request.app.state.session_factory() as s:
        meta = await s.get(SecretMeta, name) or SecretMeta(name=name)
        meta.status = "unprobed"
        s.add(meta); await s.commit()
    return {"ok": True}
```

In `app.py`: `from agentplatform.secrets import InMemorySecretStore`; default `secret_store = secret_store or InMemorySecretStore()`; `app.include_router(secrets_router)`; extend `setup_state` response by calling `secret_listing` (move the route into app.py or add the secrets list in auth's `setup_state` via `from agentplatform.api.secrets import secret_listing`).

- [ ] **Step 4: All tests pass; Step 5: Commit** — `git commit -m "feat(api): k8s-backed secret store with metadata and required-secret gate data"`.

---

### Task 6: Agent store (git checkout reader) + agents API

**Files:**
- Create: `services/backend/agentplatform/agents.py`
- Create: `services/backend/agentplatform/api/agents.py`
- Modify: `services/backend/agentplatform/api/app.py` (default `agent_store` from `settings.agents_root`, include router)
- Test: `services/backend/tests/test_agents.py`

**Interfaces:**
- Produces: `class Manifest(BaseModel)`: `role: str = "operator"`, `concurrency: int = 1`, `timeout_seconds: int = 1800`, `skills: list[str] = []`, `secrets: list[str] = []`, `description: str = ""`. `class AgentInfo(BaseModel)`: `name, manifest: Manifest | None, agent_md: str, error: str | None` (error set = quarantined). `class AgentStore`: `__init__(root: Path)`, `list() -> list[AgentInfo]`, `get(name) -> AgentInfo | None`, `reload()` (re-scan; store caches between reloads).
- Endpoints (admin): `GET /api/agents` → `[{name, description, quarantined: bool, error}]`; `GET /api/agents/{name}` → full AgentInfo dump.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_agents.py
from pathlib import Path
from agentplatform.agents import AgentStore

def make_agent(root: Path, name: str, manifest: str = "description: test\n"):
    d = root / name; d.mkdir(parents=True)
    (d / "agent.md").write_text(f"# {name}\nYou are {name}.")
    (d / "manifest.yaml").write_text(manifest)

def test_list_and_quarantine(tmp_path):
    make_agent(tmp_path, "hello-world")
    make_agent(tmp_path, "broken", manifest="concurrency: 'not-an-int'\n")
    store = AgentStore(tmp_path)
    byname = {a.name: a for a in store.list()}
    assert byname["hello-world"].manifest.concurrency == 1
    assert byname["hello-world"].error is None
    assert byname["broken"].error is not None and byname["broken"].manifest is None

async def test_agents_api(admin_client):
    r = await admin_client.get("/api/agents")
    assert r.status_code == 200
```

(Conftest: point `Settings(agents_root=str(tmp_agents))` at a tmp dir fixture seeded with `hello-world` via `make_agent`; add fixture `agent_store`.)

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/agents.py
from pathlib import Path
import yaml
from pydantic import BaseModel, ValidationError

class Manifest(BaseModel):
    role: str = "operator"
    concurrency: int = 1
    timeout_seconds: int = 1800
    skills: list[str] = []
    secrets: list[str] = []
    description: str = ""

class AgentInfo(BaseModel):
    name: str
    manifest: Manifest | None
    agent_md: str
    error: str | None = None

class AgentStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._cache: dict[str, AgentInfo] = {}
        self.reload()
    def reload(self) -> None:
        found: dict[str, AgentInfo] = {}
        if self.root.is_dir():
            for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
                found[d.name] = self._load(d)
        self._cache = found
    def _load(self, d: Path) -> AgentInfo:
        md = d / "agent.md"
        agent_md = md.read_text() if md.is_file() else ""
        try:
            raw = yaml.safe_load((d / "manifest.yaml").read_text()) or {}
            return AgentInfo(name=d.name, manifest=Manifest(**raw), agent_md=agent_md)
        except (OSError, yaml.YAMLError, ValidationError) as e:
            return AgentInfo(name=d.name, manifest=None, agent_md=agent_md, error=str(e))
    def list(self) -> list[AgentInfo]:
        return list(self._cache.values())
    def get(self, name: str) -> AgentInfo | None:
        return self._cache.get(name)
```

```python
# services/backend/agentplatform/api/agents.py
from fastapi import APIRouter, Depends, HTTPException, Request
from agentplatform.api.auth import require_admin

router = APIRouter(dependencies=[Depends(require_admin)])

@router.get("/api/agents")
async def list_agents(request: Request):
    request.app.state.agent_store.reload()
    return [{"name": a.name, "description": a.manifest.description if a.manifest else "",
             "quarantined": a.error is not None, "error": a.error}
            for a in request.app.state.agent_store.list()]

@router.get("/api/agents/{name}")
async def get_agent(request: Request, name: str):
    a = request.app.state.agent_store.get(name)
    if a is None: raise HTTPException(404)
    return a.model_dump()
```

- [ ] **Step 4: Tests pass; Step 5: Commit** — `git commit -m "feat(api): agent store reads git checkout with manifest validation and quarantine"`.

---

### Task 7: Runs API (create, list, get, events, kill)

**Files:**
- Create: `services/backend/agentplatform/api/runs.py`
- Modify: `services/backend/agentplatform/api/app.py` (drop placeholder route, include router)
- Test: `services/backend/tests/test_runs_api.py`

**Interfaces:**
- Consumes: `Run`, `RunState`, `TranscriptEvent`, producer, `TOPIC_RUN_REQUESTS`, agent_store.
- Produces (admin-authed): `POST /api/runs` `{"agent": str, "prompt": str}` → 404 unknown agent, 409 quarantined, else insert Run(trigger="manual", requested_by=principal) **commit first**, then publish `{"type": "run", "run_id": id}` key=run_id → `{"id", "state"}`. `GET /api/runs?limit=50` newest-first summaries. `GET /api/runs/{id}` full row. `GET /api/runs/{id}/events` ordered payload list. `POST /api/runs/{id}/kill` → publish `{"type": "cancel", "run_id": id}`, 409 if run already terminal.
- Message contract on `run.requests` (dispatcher consumes in Task 9): `{"type": "run"|"cancel", "run_id": str}`.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_runs_api.py
from agentplatform.events import TOPIC_RUN_REQUESTS

async def test_create_run_writes_db_then_kafka(admin_client, producer):
    r = await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})
    assert r.status_code == 200
    run_id = r.json()["id"]
    assert producer.published == [(TOPIC_RUN_REQUESTS, run_id, {"type": "run", "run_id": run_id})]
    r = await admin_client.get(f"/api/runs/{run_id}")
    assert r.json()["state"] == "queued" and r.json()["agent"] == "hello-world"

async def test_unknown_agent_404(admin_client):
    assert (await admin_client.post("/api/runs", json={"agent": "nope", "prompt": "x"})).status_code == 404

async def test_kill_publishes_cancel(admin_client, producer):
    run_id = (await admin_client.post("/api/runs", json={"agent": "hello-world", "prompt": "hi"})).json()["id"]
    assert (await admin_client.post(f"/api/runs/{run_id}/kill")).status_code == 200
    assert producer.published[-1] == (TOPIC_RUN_REQUESTS, run_id, {"type": "cancel", "run_id": run_id})
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/api/runs.py
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from agentplatform.api.auth import require_admin
from agentplatform.db import ACTIVE_STATES, Run, TranscriptEvent
from agentplatform.events import TOPIC_RUN_REQUESTS

router = APIRouter()

class RunIn(BaseModel):
    agent: str
    prompt: str

def _summary(r: Run) -> dict:
    return {"id": r.id, "agent": r.agent, "state": r.state, "trigger": r.trigger,
            "created_at": r.created_at.isoformat() if r.created_at else None}

@router.post("/api/runs")
async def create_run(request: Request, body: RunIn, principal: str = Depends(require_admin)):
    info = request.app.state.agent_store.get(body.agent)
    if info is None: raise HTTPException(404, "unknown agent")
    if info.error is not None: raise HTTPException(409, "agent quarantined")
    run = Run(agent=body.agent, trigger="manual", requested_by=principal, prompt=body.prompt)
    async with request.app.state.session_factory() as s:
        s.add(run); await s.commit()
    await request.app.state.producer.publish(TOPIC_RUN_REQUESTS, run.id,
                                             {"type": "run", "run_id": run.id})
    return {"id": run.id, "state": run.state}

@router.get("/api/runs", dependencies=[Depends(require_admin)])
async def list_runs(request: Request, limit: int = 50):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(Run).order_by(Run.created_at.desc()).limit(limit))).scalars()
        return [_summary(r) for r in rows]

@router.get("/api/runs/{run_id}", dependencies=[Depends(require_admin)])
async def get_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        d = _summary(run)
        d.update({"prompt": run.prompt, "exit_code": run.exit_code, "error": run.error,
                  "tokens_in": run.tokens_in, "tokens_out": run.tokens_out,
                  "tool_calls": run.tool_calls,
                  "started_at": run.started_at.isoformat() if run.started_at else None,
                  "finished_at": run.finished_at.isoformat() if run.finished_at else None})
        return d

@router.get("/api/runs/{run_id}/events", dependencies=[Depends(require_admin)])
async def run_events(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(TranscriptEvent)
                .where(TranscriptEvent.run_id == run_id).order_by(TranscriptEvent.seq))).scalars()
        return [e.payload for e in rows]

@router.post("/api/runs/{run_id}/kill", dependencies=[Depends(require_admin)])
async def kill_run(request: Request, run_id: str):
    async with request.app.state.session_factory() as s:
        run = await s.get(Run, run_id)
        if run is None: raise HTTPException(404)
        if run.state not in ACTIVE_STATES: raise HTTPException(409, "run is terminal")
    await request.app.state.producer.publish(TOPIC_RUN_REQUESTS, run_id,
                                             {"type": "cancel", "run_id": run_id})
    return {"ok": True}
```

- [ ] **Step 4: Full suite passes; Step 5: Commit** — `git commit -m "feat(api): runs endpoints with postgres-first publish and cancel"`.

---

### Task 8: WebSocket live tail

**Files:**
- Create: `services/backend/agentplatform/api/tail.py`
- Modify: `services/backend/agentplatform/api/app.py` (include router; add `consumer_factory` arg, default builds AIOKafkaConsumer, tests inject fake)
- Test: `services/backend/tests/test_tail.py`

**Interfaces:**
- Produces: `WS /api/runs/{run_id}/tail` — sends stored events first (replay), then live messages from `run.transcript` filtered by key == run_id, as JSON text frames; closes when a frame has `{"type": "lifecycle", "terminal": true}`.
- `consumer_factory() -> AsyncIterator[tuple[key: str, value: dict]]` — app-level injectable; production impl wraps AIOKafkaConsumer on `run.transcript` + `run.events` with a fresh group id per socket; `FakeConsumer(items)` yields a fixed list.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_tail.py
from starlette.testclient import TestClient
from agentplatform.config import Settings
from agentplatform.api.app import create_app

def test_tail_replays_then_streams(sf_sync, producer, agent_store):
    async def fake_consumer():
        yield ("RUNID", {"type": "assistant", "seq": 1, "text": "hi"})
        yield ("RUNID", {"type": "lifecycle", "terminal": True, "state": "succeeded"})
    app = create_app(Settings(), sf_sync, producer, agent_store=agent_store,
                     consumer_factory=fake_consumer)
    with TestClient(app) as tc:
        tc.post("/api/setup", json={"password": "pw12345678"})
        tc.post("/api/login", json={"password": "pw12345678"})
        with tc.websocket_connect("/api/runs/RUNID/tail") as ws:
            assert ws.receive_json()["type"] == "assistant"
            assert ws.receive_json()["terminal"] is True
```

(Conftest gains `sf_sync`: same sqlite factory but built inside the test's event loop via `TestClient` lifespan — implementer: reuse `sf` pattern with `asyncio.run` bridge or make the fixture return the factory directly; `TestClient` drives its own loop, so construct engine lazily inside app startup using `settings.db_url` when `session_factory is None`.)

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/api/tail.py
import json
from fastapi import APIRouter, WebSocket
from sqlalchemy import select
from agentplatform.db import TranscriptEvent

router = APIRouter()

@router.websocket("/api/runs/{run_id}/tail")
async def tail(ws: WebSocket, run_id: str):
    await ws.accept()
    async with ws.app.state.session_factory() as s:
        rows = (await s.execute(select(TranscriptEvent)
                .where(TranscriptEvent.run_id == run_id).order_by(TranscriptEvent.seq))).scalars()
        for e in rows:
            await ws.send_text(json.dumps(e.payload))
    factory = ws.app.state.consumer_factory
    if factory is None:
        await ws.close(); return
    async for key, value in factory():
        if key != run_id:
            continue
        await ws.send_text(json.dumps(value))
        if value.get("terminal"):
            break
    await ws.close()
```

Production `consumer_factory` (add to `app.py`, used when none injected):

```python
# in app.py
def kafka_consumer_factory(settings):
    async def factory():
        import uuid
        from aiokafka import AIOKafkaConsumer
        from agentplatform.events import TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT
        c = AIOKafkaConsumer(TOPIC_RUN_TRANSCRIPT, TOPIC_RUN_EVENTS,
                             bootstrap_servers=settings.kafka_bootstrap,
                             group_id=f"tail-{uuid.uuid4().hex}",
                             auto_offset_reset="latest")
        await c.start()
        try:
            async for msg in c:
                yield (msg.key.decode() if msg.key else "", json.loads(msg.value))
        finally:
            await c.stop()
    return factory
```

- [ ] **Step 4: Tests pass; Step 5: Commit** — `git commit -m "feat(api): websocket live tail with replay from postgres"`.

---

### Task 9: Dispatcher core logic (fake launcher)

**Files:**
- Create: `services/backend/agentplatform/dispatcher.py`
- Test: `services/backend/tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `run.requests` messages `{"type": "run"|"cancel", "run_id"}`; `Run`, `ACTIVE_STATES`, producer, agent_store, settings.
- Produces: `class Launcher` (abstract): `async launch(run: Run, manifest: Manifest) -> None`, `async cancel(run_id: str) -> None`. `class FakeLauncher(Launcher)` recording `.launched: list[str]`, `.cancelled: list[str]`. `class Dispatcher`: `__init__(settings, session_factory, producer, agent_store, launcher)`, `async handle(message: dict) -> None`, `async run_forever()` (aiokafka consume loop, group `dispatcher`, manual commit after handle). Lifecycle events published to `run.events`: `{"run_id", "type": "state", "state", "detail"?}`.
- Handle semantics: unknown/terminal run → no-op (idempotency). `run`: reject to `rejected` + event if agent missing/quarantined; requeue-skip (leave `queued`, do not launch) if global cap (count of `dispatched`+`running`) or per-agent cap reached — publish the message back to `run.requests` after a 5s asyncio.sleep to retry; else set `dispatched` + event, `launcher.launch`. Launch exception → state `dlq`, publish original message + error to `run.dlq`. `cancel`: if active, `launcher.cancel(run_id)`, set `killed` + event.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_dispatcher.py
import pytest
from agentplatform.config import Settings
from agentplatform.db import Run, RunState
from agentplatform.dispatcher import Dispatcher, FakeLauncher
from agentplatform.events import FakeProducer, TOPIC_RUN_DLQ

@pytest.fixture
def disp(sf, agent_store):
    return Dispatcher(Settings(global_concurrency=2), sf, FakeProducer(), agent_store, FakeLauncher())

async def make_run(sf, agent="hello-world", state=RunState.QUEUED) -> str:
    async with sf() as s:
        run = Run(agent=agent, trigger="manual", requested_by="t", prompt="x", state=state)
        s.add(run); await s.commit(); return run.id

async def test_dispatches_queued_run(sf, disp):
    rid = await make_run(sf)
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == [rid]
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.DISPATCHED

async def test_terminal_run_is_noop(sf, disp):
    rid = await make_run(sf, state=RunState.SUCCEEDED)
    await disp.handle({"type": "run", "run_id": rid})
    assert disp.launcher.launched == []

async def test_rejects_unknown_agent(sf, disp):
    rid = await make_run(sf, agent="ghost")
    await disp.handle({"type": "run", "run_id": rid})
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.REJECTED

async def test_launch_failure_goes_dlq(sf, disp):
    disp.launcher.fail_next = True
    rid = await make_run(sf)
    await disp.handle({"type": "run", "run_id": rid})
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.DLQ
    assert disp.producer.published[-1][0] == TOPIC_RUN_DLQ

async def test_cancel_active_run(sf, disp):
    rid = await make_run(sf, state=RunState.RUNNING)
    await disp.handle({"type": "cancel", "run_id": rid})
    assert disp.launcher.cancelled == [rid]
    async with sf() as s:
        assert (await s.get(Run, rid)).state == RunState.KILLED
```

- [ ] **Step 2: Run, expect FAIL.**

- [ ] **Step 3: Implement**

```python
# services/backend/agentplatform/dispatcher.py
import asyncio, json, logging
from sqlalchemy import func, select
from agentplatform.agents import AgentStore, Manifest
from agentplatform.db import ACTIVE_STATES, Run, RunState, utcnow
from agentplatform.events import (TOPIC_RUN_DLQ, TOPIC_RUN_EVENTS, TOPIC_RUN_REQUESTS)

log = logging.getLogger("dispatcher")

class Launcher:
    async def launch(self, run: Run, manifest: Manifest) -> None: raise NotImplementedError
    async def cancel(self, run_id: str) -> None: raise NotImplementedError

class FakeLauncher(Launcher):
    def __init__(self):
        self.launched: list[str] = []; self.cancelled: list[str] = []
        self.fail_next = False
    async def launch(self, run, manifest):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("boom")
        self.launched.append(run.id)
    async def cancel(self, run_id): self.cancelled.append(run_id)

class Dispatcher:
    def __init__(self, settings, session_factory, producer, agent_store: AgentStore, launcher: Launcher):
        self.settings, self.sf, self.producer = settings, session_factory, producer
        self.agents, self.launcher = agent_store, launcher

    async def _event(self, run_id: str, state: str, detail: str = "") -> None:
        await self.producer.publish(TOPIC_RUN_EVENTS, run_id,
            {"run_id": run_id, "type": "state", "state": state, "detail": detail})

    async def _set_state(self, run: Run, state: RunState, error: str | None = None) -> None:
        async with self.sf() as s:
            db_run = await s.get(Run, run.id)
            db_run.state = state
            if error: db_run.error = error
            if state == RunState.DISPATCHED: db_run.started_at = utcnow()
            if state in (RunState.REJECTED, RunState.DLQ, RunState.KILLED): db_run.finished_at = utcnow()
            await s.commit()
        await self._event(run.id, state, error or "")

    async def handle(self, message: dict) -> None:
        run_id = message.get("run_id", "")
        async with self.sf() as s:
            run = await s.get(Run, run_id)
        if run is None: return
        if message.get("type") == "cancel":
            if run.state in ACTIVE_STATES:
                await self.launcher.cancel(run_id)
                await self._set_state(run, RunState.KILLED)
            return
        if run.state != RunState.QUEUED: return  # idempotency
        info = self.agents.get(run.agent)
        if info is None or info.error is not None:
            await self._set_state(run, RunState.REJECTED, "unknown or quarantined agent")
            return
        manifest = info.manifest
        async with self.sf() as s:
            busy = (await s.execute(select(func.count()).select_from(Run)
                    .where(Run.state.in_([RunState.DISPATCHED, RunState.RUNNING])))).scalar_one()
            agent_busy = (await s.execute(select(func.count()).select_from(Run)
                    .where(Run.agent == run.agent,
                           Run.state.in_([RunState.DISPATCHED, RunState.RUNNING])))).scalar_one()
        if busy >= self.settings.global_concurrency or agent_busy >= manifest.concurrency:
            await asyncio.sleep(5)
            await self.producer.publish(TOPIC_RUN_REQUESTS, run_id, message)
            return
        try:
            await self.launcher.launch(run, manifest)
        except Exception as e:
            await self._set_state(run, RunState.DLQ, str(e))
            await self.producer.publish(TOPIC_RUN_DLQ, run_id,
                                        {"message": message, "error": str(e)})
            return
        await self._set_state(run, RunState.DISPATCHED)

    async def run_forever(self) -> None:
        from aiokafka import AIOKafkaConsumer
        consumer = AIOKafkaConsumer(TOPIC_RUN_REQUESTS,
                                    bootstrap_servers=self.settings.kafka_bootstrap,
                                    group_id="dispatcher", enable_auto_commit=False)
        await consumer.start(); await self.producer.start()
        try:
            async for msg in consumer:
                try:
                    await self.handle(json.loads(msg.value))
                except Exception:
                    log.exception("handle failed")
                await consumer.commit()
        finally:
            await consumer.stop(); await self.producer.stop()
```

Note for implementer: the concurrency-requeue `asyncio.sleep(5)` is acceptable at this scale (single consumer, requeue is rare); do not add delay-queue machinery.

- [ ] **Step 4: Tests pass; Step 5: Commit** — `git commit -m "feat(dispatcher): idempotent run handling, caps, cancel, dlq"`.

---

### Task 10: K8s Job launcher + watcher + timeout + dispatcher entrypoint

**Files:**
- Create: `services/backend/agentplatform/joblauncher.py`
- Create: `services/backend/agentplatform/dispatcher_main.py`
- Test: `services/backend/tests/test_joblauncher.py`

**Interfaces:**
- Produces: `class K8sJobLauncher(Launcher)`: `__init__(batch: BatchV1Api, settings)`. `launch` builds Job `run-{run.id[:12]}` in `settings.k8s_namespace`: runner container `settings.runner_image`, env `AP_RUN_ID`, `AP_AGENT`, `AP_PROMPT`, `AP_KAFKA_BOOTSTRAP`; volumes: secret `claude-credentials` mounted read-only at `/secrets/claude`, agents checkout via the `agents-sync` PVC-or-emptyDir at `/agents` (volume name `agents`, claimName from `settings` — add field `agents_volume_claim: str = "agent-definitions"` to Settings); `restartPolicy: Never`, `backoffLimit: 0`, `activeDeadlineSeconds` = manifest.timeout_seconds, resources requests 1Gi / limits 3Gi memory. `cancel` deletes the job with propagation Foreground. `build_job(run, manifest) -> V1Job` exposed for tests.
- `class JobWatcher`: `__init__(batch, settings, session_factory, producer)`; `async poll_once()` — for every Run in `dispatched`/`running`, read Job status: active→`running` (once), succeeded→leave to runner's terminal event but mark `succeeded` if runner never reported (belt and braces), failed with DeadlineExceeded→`timed_out`, failed otherwise→`failed` with reason; publish the same `run.events` state events as the Dispatcher does. `async run_forever()` — poll every 10s.
- `dispatcher_main.py`: builds real k8s clients (`config.load_incluster_config()` fallback `load_kube_config()`), real Producer, engine from settings, `AgentStore(settings.agents_root)`, runs `Dispatcher.run_forever()` and `JobWatcher.run_forever()` under `asyncio.gather`. Entrypoint: `python -m agentplatform.dispatcher_main`.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_joblauncher.py
from agentplatform.agents import Manifest
from agentplatform.config import Settings
from agentplatform.db import Run
from agentplatform.joblauncher import K8sJobLauncher

def test_build_job_spec():
    launcher = K8sJobLauncher(batch=None, settings=Settings(runner_image="r:1", k8s_namespace="ap"))
    run = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="say hi")
    run.id = "a" * 32
    job = launcher.build_job(run, Manifest(timeout_seconds=600))
    assert job.metadata.name == "run-aaaaaaaaaaaa"
    c = job.spec.template.spec.containers[0]
    env = {e.name: e.value for e in c.env}
    assert env["AP_RUN_ID"] == run.id and env["AP_AGENT"] == "hello-world"
    assert job.spec.active_deadline_seconds == 600
    assert job.spec.backoff_limit == 0
    mounts = {m.name: m.mount_path for m in c.volume_mounts}
    assert mounts == {"claude-credentials": "/secrets/claude", "agents": "/agents"}
```

- [ ] **Step 2: Run, expect FAIL. Step 3: Implement** (`build_job` returns `k8s.V1Job` exactly matching the interface block above; `launch` calls `batch.create_namespaced_job(ns, job)`; `cancel` calls `batch.delete_namespaced_job(f"run-{run_id[:12]}", ns, propagation_policy="Foreground")`, swallowing 404. `JobWatcher.poll_once` per the interface block; keep it ~60 lines, straightforward reads of `job.status.active/succeeded/failed` and `job.status.conditions` for `DeadlineExceeded`.)

- [ ] **Step 4: Tests pass; Step 5: Commit** — `git commit -m "feat(dispatcher): k8s job launcher, watcher with timeout detection, entrypoint"`.

---

### Task 11: Recorder

**Files:**
- Create: `services/backend/agentplatform/recorder.py`
- Create: `services/backend/agentplatform/recorder_main.py` (mirrors dispatcher_main: real consumer on `run.events` + `run.transcript` + `run.dlq`, group `recorder`, calls `Recorder.handle(topic, key, value)`)
- Test: `services/backend/tests/test_recorder.py`

**Interfaces:**
- Consumes: `run.events` state events (from dispatcher/watcher/runner), `run.transcript` stream-json events (from runner, each carries `"seq"`), `run.dlq`.
- Produces: `class Recorder`: `__init__(session_factory)`, `async handle(topic: str, key: str, value: dict)`. Transcript → insert TranscriptEvent (ignore duplicate (run_id, seq)); bump `tool_calls` when `value.get("type") == "tool_use"`; accumulate `tokens_in`/`tokens_out` from `value.get("usage", {})` (`input_tokens`/`output_tokens`). State events → update Run.state (never regress a terminal state), set `finished_at` on terminal, `exit_code` from `value.get("exit_code")`, error from detail.

- [ ] **Step 1: Failing test**

```python
# services/backend/tests/test_recorder.py
from agentplatform.db import Run, RunState, TranscriptEvent
from agentplatform.events import TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT
from agentplatform.recorder import Recorder
from sqlalchemy import select

async def seed(sf) -> str:
    async with sf() as s:
        run = Run(agent="hello-world", trigger="manual", requested_by="t",
                  prompt="x", state=RunState.RUNNING)
        s.add(run); await s.commit(); return run.id

async def test_transcript_and_metrics(sf):
    rid = await seed(sf); rec = Recorder(sf)
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "tool_use", "name": "Bash"})
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid, {"seq": 1, "type": "tool_use", "name": "Bash"})  # dup
    await rec.handle(TOPIC_RUN_TRANSCRIPT, rid,
                     {"seq": 2, "type": "result", "usage": {"input_tokens": 10, "output_tokens": 5}})
    async with sf() as s:
        assert len((await s.execute(select(TranscriptEvent))).scalars().all()) == 2
        run = await s.get(Run, rid)
        assert run.tool_calls == 1 and run.tokens_in == 10 and run.tokens_out == 5

async def test_state_event_terminal(sf):
    rid = await seed(sf); rec = Recorder(sf)
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "succeeded", "exit_code": 0})
    await rec.handle(TOPIC_RUN_EVENTS, rid, {"type": "state", "state": "running"})  # no regress
    async with sf() as s:
        run = await s.get(Run, rid)
        assert run.state == "succeeded" and run.finished_at is not None and run.exit_code == 0
```

- [ ] **Step 2: FAIL. Step 3: Implement** per the interface block (use `session.merge`-free upsert: try insert TranscriptEvent, catch `IntegrityError`, rollback, skip). **Step 4: pass. Step 5: Commit** — `git commit -m "feat(recorder): persist transcript, metrics, and state transitions"`.

---

### Task 12: Runner wrapper + image

**Files:**
- Create: `services/runner/runner.py`
- Create: `services/runner/requirements.txt` (`aiokafka>=0.11`)
- Create: `services/runner/Dockerfile`
- Test: `services/runner/test_runner.py` (plain pytest, run from `services/runner`)

**Interfaces:**
- Consumes env: `AP_RUN_ID`, `AP_AGENT`, `AP_PROMPT`, `AP_KAFKA_BOOTSTRAP`; `/secrets/claude/credentials.json` (mounted secret); `/agents` (definitions checkout).
- Produces: copies credentials to `~/.claude/.credentials.json` (never writes back to the mount); runs `claude --agent <agent> -p <prompt> --output-format stream-json --verbose` with `cwd=/workspace` and the agent's dir contents available; publishes each stdout JSON line to `run.transcript` key=run_id with `seq` added (1-based); on process exit publishes to `run.events`: `{"run_id", "type": "state", "state": "succeeded"|"failed", "exit_code": rc, "terminal": True}` and mirrors a terminal frame to `run.transcript` (`{"type": "lifecycle", "terminal": true, "state": ...}`) so tails close. `CLAUDE_BIN` env (default `claude`) enables the fake in tests. `main()` returns the exit code.

- [ ] **Step 1: Failing test**

```python
# services/runner/test_runner.py
import json, os, stat
from pathlib import Path
import runner

class FakeProducer:
    def __init__(self): self.published = []
    async def start(self): pass
    async def stop(self): pass
    async def publish(self, topic, key, value): self.published.append((topic, key, value))

def test_relays_stream_and_terminal(tmp_path, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\necho '{\"type\":\"assistant\",\"text\":\"hi\"}'\nexit 0\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    creds = tmp_path / "secrets"; creds.mkdir()
    (creds / "credentials.json").write_text("{}")
    monkeypatch.setenv("AP_RUN_ID", "RID"); monkeypatch.setenv("AP_AGENT", "hello-world")
    monkeypatch.setenv("AP_PROMPT", "hi"); monkeypatch.setenv("CLAUDE_BIN", str(fake))
    monkeypatch.setenv("AP_SECRETS_DIR", str(creds))
    monkeypatch.setenv("HOME", str(tmp_path))
    p = FakeProducer()
    rc = runner.run(producer=p)
    assert rc == 0
    topics = [t for t, _, _ in p.published]
    assert "run.transcript" in topics and "run.events" in topics
    first = p.published[0][2]
    assert first["seq"] == 1 and first["type"] == "assistant"
    assert p.published[-1][2]["terminal"] is True
```

- [ ] **Step 2: FAIL. Step 3: Implement**

```python
# services/runner/runner.py
import asyncio, json, os, shutil, subprocess, sys
from pathlib import Path
from aiokafka import AIOKafkaProducer

TOPIC_TRANSCRIPT, TOPIC_EVENTS = "run.transcript", "run.events"

class KafkaProducerWrapper:
    def __init__(self, bootstrap): self._p = AIOKafkaProducer(bootstrap_servers=bootstrap)
    async def start(self): await self._p.start()
    async def stop(self): await self._p.stop()
    async def publish(self, topic, key, value):
        await self._p.send_and_wait(topic, json.dumps(value).encode(), key=key.encode())

def _install_credentials() -> None:
    src = Path(os.environ.get("AP_SECRETS_DIR", "/secrets/claude")) / "credentials.json"
    dst = Path.home() / ".claude" / ".credentials.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(src, dst)  # copy: never write back to the mount

def run(producer=None) -> int:
    run_id, agent = os.environ["AP_RUN_ID"], os.environ["AP_AGENT"]
    prompt = os.environ["AP_PROMPT"]
    producer = producer or KafkaProducerWrapper(os.environ.get("AP_KAFKA_BOOTSTRAP", "kafka:9092"))
    return asyncio.run(_run(producer, run_id, agent, prompt))

async def _run(producer, run_id: str, agent: str, prompt: str) -> int:
    _install_credentials()
    await producer.start()
    claude = os.environ.get("CLAUDE_BIN", "claude")
    proc = subprocess.Popen(
        [claude, "--agent", agent, "-p", prompt, "--output-format", "stream-json", "--verbose"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seq = 0
    for line in proc.stdout:
        line = line.strip()
        if not line: continue
        seq += 1
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            payload = {"type": "raw", "text": line}
        payload["seq"] = seq
        await producer.publish(TOPIC_TRANSCRIPT, run_id, payload)
    rc = proc.wait()
    state = "succeeded" if rc == 0 else "failed"
    await producer.publish(TOPIC_TRANSCRIPT, run_id,
                           {"seq": seq + 1, "type": "lifecycle", "terminal": True, "state": state})
    await producer.publish(TOPIC_EVENTS, run_id,
                           {"run_id": run_id, "type": "state", "state": state,
                            "exit_code": rc, "terminal": True})
    await producer.stop()
    return rc

if __name__ == "__main__":
    sys.exit(run())
```

```dockerfile
# services/runner/Dockerfile
FROM node:22-slim
RUN npm install -g @anthropic-ai/claude-code@2.1.214 \
 && apt-get update && apt-get install -y --no-install-recommends python3 python3-pip git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip3 install --break-system-packages -r requirements.txt
COPY runner.py .
RUN useradd -m runner && mkdir /workspace && chown runner /workspace
USER runner
WORKDIR /workspace
ENTRYPOINT ["python3", "/app/runner.py"]
```

- [ ] **Step 4: `cd services/runner && python -m pytest test_runner.py -v` passes (install aiokafka + pytest in the backend venv or a local one). Step 5: Commit** — `git commit -m "feat(runner): claude stream-json relay wrapper and pinned image"`.

---

### Task 13: Backend image + entrypoints + api main

**Files:**
- Create: `services/backend/agentplatform/api_main.py`
- Create: `services/backend/Dockerfile`

**Interfaces:**
- `api_main.py`: builds Settings, engine (`init_db` on startup), session factory, real `Producer` (started on app startup via FastAPI lifespan), `K8sSecretStore` when in-cluster (else InMemory), `AgentStore(settings.agents_root)`, `kafka_consumer_factory`; serves with uvicorn on 8000. Entrypoint `python -m agentplatform.api_main`.
- One image, three commands: `python -m agentplatform.api_main` / `dispatcher_main` / `recorder_main`.

- [ ] **Step 1: api_main**

```python
# services/backend/agentplatform/api_main.py
import uvicorn
from agentplatform.agents import AgentStore
from agentplatform.api.app import create_app, kafka_consumer_factory
from agentplatform.config import get_settings
from agentplatform.db import init_db, make_engine, make_session_factory
from agentplatform.events import Producer
from agentplatform.secrets import InMemorySecretStore, K8sSecretStore

def build_app():
    settings = get_settings()
    engine = make_engine(settings.db_url)
    sf = make_session_factory(engine)
    producer = Producer(settings.kafka_bootstrap)
    try:
        from kubernetes import client, config
        config.load_incluster_config()
        store = K8sSecretStore(client.CoreV1Api(), settings.k8s_namespace)
    except Exception:
        store = InMemorySecretStore()
    app = create_app(settings, sf, producer, secret_store=store,
                     agent_store=AgentStore(settings.agents_root),
                     consumer_factory=kafka_consumer_factory(settings))
    @app.on_event("startup")
    async def _start():
        await init_db(engine)
        await producer.start()
    @app.on_event("shutdown")
    async def _stop():
        await producer.stop()
    return app

if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=8000)
```

```dockerfile
# services/backend/Dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY agentplatform ./agentplatform
RUN pip install --no-cache-dir .
USER nobody
CMD ["python", "-m", "agentplatform.api_main"]
```

- [ ] **Step 2: Verify** — `docker build -t agent-platform-backend:dev services/backend` succeeds; `.venv/bin/pytest` still green. **Step 3: Commit** — `git commit -m "feat(backend): api entrypoint and shared service image"`.

---

### Task 14: Web SPA — scaffold, auth flow, secrets gate

**Files:**
- Create: `services/web/` via Vite (`npm create vite@latest . -- --template react-ts`), plus `src/api.ts`, `src/App.tsx`, `src/pages/{Setup,Login,Secrets}.tsx`, `src/Gate.tsx`, `nginx.conf`, `Dockerfile`
- Test: `npm run build` is the check for all web tasks (no JS unit tests in M1 — the verification checklist covers behavior; this is a deliberate scope call).

**Interfaces:**
- `src/api.ts`: `api<T>(path, opts) => Promise<T>` — fetch with `credentials: "include"`, JSON, throws on !ok with status; `SetupState = {needs_admin: boolean, secrets: {name: string, status: string, required: boolean}[]}`.
- Routing (`react-router-dom`): `/setup`, `/login`, `/secrets`, `/agents`, `/agents/:name`, `/runs`, `/runs/:id`, `/` (dashboard).
- `Gate.tsx` wraps all routes: fetches `/api/setup-state`; `needs_admin` → redirect `/setup`; any required secret not `ok` and not on `/secrets` or auth pages → banner + redirect `/secrets`; 401 from any api call → redirect `/login`.

- [ ] **Step 1: Scaffold** — `cd services/web && npm create vite@latest . -- --template react-ts && npm i react-router-dom`.
- [ ] **Step 2: Implement api.ts**

```typescript
// services/web/src/api.ts
export type SecretStatus = { name: string; status: string; required: boolean };
export type SetupState = { needs_admin: boolean; secrets: SecretStatus[] };

export async function api<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (res.status === 401) { window.location.href = "/login"; throw new Error("401"); }
  if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}
```

- [ ] **Step 3: Implement Setup/Login/Secrets pages + Gate.** Setup: one password field posting `/api/setup` then `/api/login` then navigate `/`. Login: password → `/api/login`. Secrets: list from `/api/secrets` with status chips; textarea + save per secret (PUT `/api/secrets/{name}` with `{data: {"credentials.json": <textarea>}}`). Gate per interface block. App.tsx wires routes with a left nav (Dashboard, Agents, Runs, Secrets).
- [ ] **Step 4: nginx + Dockerfile**

```nginx
# services/web/nginx.conf
server {
  listen 8080;
  root /usr/share/nginx/html;
  location /api/ {
    proxy_pass http://agent-platform-api:8000;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
  }
  location / { try_files $uri /index.html; }
}
```

```dockerfile
# services/web/Dockerfile
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
RUN npm run build
FROM nginx:1.29-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
```

- [ ] **Step 5: `npm run build` passes. Commit** — `git commit -m "feat(web): spa scaffold, setup/login flow, required-secrets gate"`.

---

### Task 15: Web SPA — agents, runs, live transcript, dashboard

**Files:**
- Create: `services/web/src/pages/{Agents,AgentDetail,Runs,RunDetail,Dashboard}.tsx`
- Modify: `services/web/src/App.tsx` (routes)

**Interfaces:**
- Agents: table from `GET /api/agents` (name, description, quarantined badge with error tooltip) → detail link. AgentDetail: `GET /api/agents/{name}` — render `agent_md` in a `<pre>`, manifest as definition list, prompt textarea + "Run now" → `POST /api/runs` → navigate to `/runs/{id}`.
- Runs: table from `GET /api/runs` (id short, agent, state chip, created) auto-refresh 5s → detail link.
- RunDetail: `GET /api/runs/{id}` header (state, timings, tokens, tool calls, exit code) + Kill button (`POST /api/runs/{id}/kill`, disabled when terminal); transcript pane opens `WebSocket` to `/api/runs/{id}/tail` (`ws(s)://` from `location`), appends each JSON frame: `assistant` text plain, `tool_use` collapsed `<details>` with name + JSON input, other frames as dim JSON; websocket close or `terminal` frame stops the spinner and refetches the header.
- Dashboard: `GET /api/setup-state` secret statuses + last 10 runs (reuse Runs fetch) + link tiles.

- [ ] **Step 1: Implement the five pages per interface block.** RunDetail transcript core:

```typescript
// inside RunDetail component
useEffect(() => {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/api/runs/${id}/tail`);
  ws.onmessage = (e) => {
    const frame = JSON.parse(e.data);
    setEvents((prev) => [...prev, frame]);
    if (frame.terminal) { setLive(false); refetchHeader(); }
  };
  ws.onclose = () => setLive(false);
  return () => ws.close();
}, [id]);
```

- [ ] **Step 2: `npm run build` passes. Step 3: Commit** — `git commit -m "feat(web): agents, runs, live transcript, dashboard"`.

---

### Task 16: Helm chart

**Files:**
- Create: `charts/agent-platform/Chart.yaml`, `values.yaml`, `values-pai-nuc.yaml`
- Create: `charts/agent-platform/templates/`: `api.yaml`, `dispatcher.yaml`, `recorder.yaml`, `web.yaml`, `rbac.yaml`, `topics-job.yaml`, `agents-sync.yaml`, `_helpers.tpl`

**Interfaces:**
- Chart deps: run `helm repo add bitnami https://charts.bitnami.com/bitnami && helm search repo bitnami/postgresql bitnami/kafka` and pin the exact versions it prints into `Chart.yaml` `dependencies:` (postgresql with `auth.database=agentplatform`, kafka with KRaft single replica: `controller.replicaCount=1`, `broker.replicaCount=0` per current bitnami layout — verify against the chart's values). `helm dependency update` commits `Chart.lock`.
- `values.yaml` keys (used by templates): `images.backend`, `images.runner`, `images.web` (each `repository`/`tag`); `resources` per service (defaults from design: api/dispatcher/recorder 256Mi req, postgres 1Gi/2Gi, kafka 2Gi/3Gi via subchart values, web 128Mi); `web.service.type: LoadBalancer`, `web.service.port: 8090`; `agents.gitRepo: "https://github.com/kylep/agent-platform.git"`, `agents.syncIntervalSeconds: 60`; `env.AP_SESSION_SECRET` (required, no default).
- `agents-sync.yaml`: PVC `agent-definitions` (1Gi, `local-path`) + Deployment `agents-sync` — `alpine/git` container looping `git clone --depth 1` (first run) / `git -C /agents pull` every `syncIntervalSeconds`; PVC mounted at `/agents`; api and runner pods mount the same PVC read-only (`AP_AGENTS_ROOT=/agents/agents` — the `agents/` dir inside the checkout).
- `rbac.yaml`: ServiceAccount `dispatcher` + Role (jobs create/get/list/delete, pods get/list) + RoleBinding; ServiceAccount `api` + Role (secrets get/create/replace) + RoleBinding.
- `topics-job.yaml`: post-install/post-upgrade hook Job using the kafka subchart image running `kafka-topics.sh --create --if-not-exists` for the four topics against the chart's broker service.
- Backend deployments: env from values (`AP_DB_URL` assembled from the postgresql subchart service + secret, `AP_KAFKA_BOOTSTRAP=<release>-kafka:9092`, `AP_K8S_NAMESPACE`, `AP_RUNNER_IMAGE`, `AP_AGENTS_ROOT`).

- [ ] **Step 1: Write chart per interface block.** Templates are standard Deployment/Service boilerplate; `_helpers.tpl` for name/labels.
- [ ] **Step 2: Verify** — `helm dependency update charts/agent-platform && helm lint charts/agent-platform && helm template charts/agent-platform --set env.AP_SESSION_SECRET=x | kubectl apply --dry-run=client -f -` all succeed.
- [ ] **Step 3: Commit** — `git commit -m "feat(chart): umbrella helm chart with bitnami postgres+kafka, topics job, agents sync"`.

---

### Task 17: Seed agent, token script, setup docs

**Files:**
- Create: `agents/hello-world/agent.md`, `agents/hello-world/manifest.yaml`
- Create: `bin/set-claude-token.sh` (chmod +x)
- Create: `docs/setup.md`

- [ ] **Step 1: Seed agent**

```markdown
<!-- agents/hello-world/agent.md -->
---
name: hello-world
description: Trivial agent that proves the platform loop.
tools: Bash
---
You are hello-world, the agent-platform smoke-test agent. Follow the user's
prompt exactly and keep output short. If asked to say OK, reply with exactly: OK
```

```yaml
# agents/hello-world/manifest.yaml
description: Trivial agent that proves the platform loop.
role: operator
concurrency: 1
timeout_seconds: 300
```

- [ ] **Step 2: Token script**

```bash
#!/usr/bin/env bash
# bin/set-claude-token.sh — install Claude subscription credentials as the
# platform's claude-credentials secret.
# Modes:  pre-boot   set-claude-token.sh kubectl [namespace]
#         post-boot  AP_URL=http://pai:8090 AP_COOKIE_JAR=~/.ap-cookies set-claude-token.sh api
set -euo pipefail
MODE="${1:-kubectl}"
NS="${2:-agent-platform}"
CREDS="${CLAUDE_CREDENTIALS_FILE:-$HOME/.claude/.credentials.json}"
[ -f "$CREDS" ] || { echo "No credentials at $CREDS (set CLAUDE_CREDENTIALS_FILE)"; exit 1; }
case "$MODE" in
  kubectl)
    kubectl -n "$NS" create secret generic claude-credentials \
      --from-file=credentials.json="$CREDS" \
      --dry-run=client -o yaml | kubectl apply -f -
    echo "Secret claude-credentials applied to namespace $NS" ;;
  api)
    : "${AP_URL:?set AP_URL, e.g. http://pai:8090}"
    curl -sf -b "${AP_COOKIE_JAR:-$HOME/.ap-cookies}" -X PUT \
      -H 'Content-Type: application/json' \
      --data "{\"data\":{\"credentials.json\":$(jq -Rs . < "$CREDS")}}" \
      "$AP_URL/api/secrets/claude-credentials" >/dev/null
    echo "Secret set via API" ;;
  *) echo "usage: $0 kubectl [ns] | api"; exit 2 ;;
esac
```

- [ ] **Step 3: docs/setup.md** — install prereqs (helm, kubectl context), `helm dependency update && helm install ap charts/agent-platform -f charts/agent-platform/values-pai-nuc.yaml --set env.AP_SESSION_SECRET=$(openssl rand -hex 32) -n agent-platform --create-namespace`, first-launch flow (browse `http://pai:8090` → create admin → secrets gate → paste token or run the script), smoke test (run hello-world with prompt "Say OK", watch transcript), teardown, troubleshooting (pod pending = PVC, gate stuck = probe failing).
- [ ] **Step 4: Commit** — `git commit -m "feat: hello-world seed agent, token install script, setup docs"`.

---

### Task 18: CI + subscription-only guard

**Files:**
- Create: `.github/workflows/ci.yaml`

- [ ] **Step 1: Workflow** — jobs: `backend` (setup-python 3.12, `pip install -e 'services/backend[dev]'`, `pytest services/backend`), `runner` (`pip install aiokafka pytest`, `pytest services/runner`), `web` (setup-node 22, `npm ci && npm run build` in services/web), `helm` (`helm dependency update charts/agent-platform && helm lint charts/agent-platform`), `subscription-guard`:

```yaml
  subscription-guard:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: forbid API-key auth
        run: |
          ! grep -rn --exclude-dir=.git --exclude=ci.yaml -e 'sk-ant-' -e 'ANTHROPIC_API_KEY' .
```

- [ ] **Step 2: Push, verify all jobs green on GitHub. Step 3: Commit** (workflow itself) — `git commit -m "ci: tests, builds, helm lint, subscription-only guard"`.

---

### Task 19: Deploy to pai-nuc and run the verification checklist

**Files:**
- Modify: `docs/design/01-walking-skeleton.md` (tick verification checklist, record token-refresh findings)

- [ ] **Step 1: Build + load images on pai** — build `agent-platform-backend`, `agent-platform-runner`, `agent-platform-web` images for amd64 and import into k3s containerd (`docker save | ssh pai 'sudo k3s ctr images import -'`), tags matching values-pai-nuc.
- [ ] **Step 2: `helm install`** per docs/setup.md. All pods Ready.
- [ ] **Step 3: Walk the milestone-01 verification checklist** from `docs/design/01-walking-skeleton.md` item by item (setup flow, gate, probe, hello-world run with live transcript, kill, concurrency, kafka-down queueing, grep guard, token-refresh observation). Fix what fails; each fix is a normal TDD commit.
- [ ] **Step 4: Record results** — tick boxes, document token-refresh behavior + steward go/no-go decision in 01-walking-skeleton.md. Commit — `git commit -m "docs: milestone 01 verification results"`.

---

## Self-Review Notes

- Spec coverage: chart(16), setup+auth(4), secrets store/API/gate(5,14), agents read+sync+quarantine(6,16), runs API+ws(7,8), dispatcher(9,10), recorder(11), runner+image(12), web(14,15), seed agent+script+docs(17), CI+grep(18), deploy+checklist(19). Claude-probe is intentionally folded into the smoke-run flow: a dedicated `/probe` endpoint was cut as YAGNI. The gate treats `unprobed` as passing; validity is proven by running hello-world from the UI (verification checklist item). Automatic `unprobed→ok` status transition is deferred to M2 if the manual flow bites.
- Types checked: `Run`/`RunState`/producer/`Manifest` signatures consistent across Tasks 2–11; runner duplicates topic constants deliberately (separate image, no shared package).
- Known deliberate cuts: schedules table (M3), API keys for agents (M2), frontend unit tests (build + checklist instead), alembic (create_all until schema churn demands it).
