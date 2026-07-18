import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from agentplatform.agents import AgentStore
from agentplatform.api import agents as agents_api
from agentplatform.api import auth
from agentplatform.api import runs as runs_api
from agentplatform.api import secrets as secrets_api
from agentplatform.api import tail as tail_api
from agentplatform.db import make_engine, make_session_factory, init_db
from agentplatform.secrets import InMemorySecretStore


def kafka_consumer_factory(settings):
    """Production consumer_factory: wraps AIOKafkaConsumer on the run
    transcript/events topics with a fresh consumer group per socket."""

    async def factory():
        import uuid
        from aiokafka import AIOKafkaConsumer
        from agentplatform.events import TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT

        c = AIOKafkaConsumer(
            TOPIC_RUN_TRANSCRIPT,
            TOPIC_RUN_EVENTS,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=f"tail-{uuid.uuid4().hex}",
            auto_offset_reset="latest",
        )
        await c.start()
        try:
            async for msg in c:
                yield (msg.key.decode() if msg.key else "", json.loads(msg.value))
        finally:
            await c.stop()

    return factory


def create_app(settings, session_factory, producer, secret_store=None, agent_store=None,
                consumer_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        if st.session_factory is None:
            engine = make_engine(settings.db_url)
            await init_db(engine)
            st.session_factory = make_session_factory(engine)
        yield

    app = FastAPI(title="agent-platform", version="0.1.0", lifespan=lifespan)
    st = app.state
    st.settings, st.session_factory, st.producer = settings, session_factory, producer
    st.consumer_factory = consumer_factory
    secret_store = secret_store or InMemorySecretStore()
    agent_store = agent_store or AgentStore(Path(settings.agents_root))
    st.secret_store, st.agent_store = secret_store, agent_store

    app.include_router(auth.router)
    app.include_router(secrets_api.router)
    app.include_router(agents_api.router)
    app.include_router(runs_api.router)
    app.include_router(tail_api.router)
    return app
