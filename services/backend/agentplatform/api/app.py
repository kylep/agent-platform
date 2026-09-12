import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from agentplatform.agents import AgentStore
from agentplatform.api import agents as agents_api
from agentplatform.api import apikeys as apikeys_api
from agentplatform.api import apps as apps_api
from agentplatform.api import audit as audit_api
from agentplatform.api import auth
from agentplatform.api import conversations as conversations_api
from agentplatform.api import cron as cron_api
from agentplatform.api import dlq as dlq_api
from agentplatform.api import health as health_api
from agentplatform.api import help as help_api
from agentplatform.api import integrations as integrations_api
from agentplatform.api import maintenance as maintenance_api
from agentplatform.api import memory as memory_api
from agentplatform.api import metrics as metrics_api
from agentplatform.api import pulls as pulls_api
from agentplatform.api import relay as relay_api
from agentplatform.api import reports as reports_api
from agentplatform.api import jobs as jobs_api
from agentplatform.api import notify as notify_api
from agentplatform.api import schedules as schedules_api
from agentplatform.api import webhooks as webhooks_api
from agentplatform.api import runs as runs_api
from agentplatform.api import secrets as secrets_api
from agentplatform.api import skills as skills_api
from agentplatform.api import tickets as tickets_api
from agentplatform.api import tools as tools_api
from agentplatform.api import tail as tail_api
from agentplatform.db import make_engine, make_session_factory, init_db
from agentplatform.relay_feed import RelayFeed
from agentplatform.secrets import InMemorySecretStore


def kafka_consumer_factory(settings):
    """Production consumer_factory: wraps AIOKafkaConsumer on the run
    transcript/events topics with a fresh consumer group per socket."""

    async def factory():
        import uuid
        from aiokafka import AIOKafkaConsumer
        from agentplatform.events import TOPIC_RUN_EVENTS, TOPIC_RUN_TRANSCRIPT

        from agentplatform.events import unwrap
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
                # Unwrap the envelope → the UI receives the domain frame/data.
                _, data = unwrap(msg.value)
                yield (msg.key.decode() if msg.key else "", data)
        finally:
            await c.stop()


    return factory


def relay_feed_consumer_factory(settings):
    """Production factory for Relay's live feed: `relay.messages` for the room
    and `run.events` for presence. A fresh consumer group per pod, reading from
    `latest`, because this is a live feed and not a ledger — a pod that was down
    has nothing to catch up on, since a reconnecting UI names the last message
    it holds and the gap is replayed from postgres.

    A FACTORY, and passed in rather than built from settings, for the same
    reason `consumer_factory` is: `kafka_bootstrap` always has a value, so
    anything keyed off it alone would have every test that enters the lifespan
    dialling a broker that is not there."""

    def factory():
        import socket
        import uuid
        from aiokafka import AIOKafkaConsumer
        from agentplatform.events import TOPIC_RELAY_MESSAGES, TOPIC_RUN_EVENTS

        return AIOKafkaConsumer(
            TOPIC_RELAY_MESSAGES, TOPIC_RUN_EVENTS,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=f"api-sse-{socket.gethostname() or uuid.uuid4().hex[:8]}",
            auto_offset_reset="latest",
        )

    return factory


def tickets_feed_consumer_factory(settings):
    """Production factory for the ticket board's live feed: `tickets.events`,
    a fresh group per pod from `latest`. Its own consumer rather than another
    topic on Relay's, because the two feeds fan out to different subscribers
    and a board falling behind must not cost a room its messages."""

    def factory():
        import socket
        import uuid
        from aiokafka import AIOKafkaConsumer
        from agentplatform.events import TOPIC_TICKETS_EVENTS

        return AIOKafkaConsumer(
            TOPIC_TICKETS_EVENTS,
            bootstrap_servers=settings.kafka_bootstrap,
            group_id=f"api-tickets-{socket.gethostname() or uuid.uuid4().hex[:8]}",
            auto_offset_reset="latest",
        )

    return factory


def create_app(settings, session_factory, producer, secret_store=None, agent_store=None,
                consumer_factory=None, feed_consumer_factory=None,
                ticket_feed_consumer_factory=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        st = app.state
        if st.session_factory is None:
            engine = make_engine(settings.db_url)
            await init_db(engine, settings.relay_default_grant,
                          settings.tickets_default_grant)
            st.session_factory = make_session_factory(engine)
        # The feed only needs a session for presence (a run event names a run,
        # not a room), so it is handed the factory here, once it is real.
        st.feed.session_factory = st.session_factory
        st.ticket_feed.session_factory = st.session_factory
        # Agent definitions are rows (docs/design/15): prime the cache once the
        # session factory exists, so the first request reads real agents rather
        # than an empty store waiting on its TTL refresh.
        if st.agent_store.session_factory is None:
            st.agent_store.session_factory = st.session_factory
        try:
            await st.agent_store.reload()
        except Exception:
            logging.getLogger("api").warning(
                "initial agent definition load failed; the store will retry",
                exc_info=True)
        # Kafka being down must not take the API down: runs are recorded in
        # postgres first and the dispatcher sweep drains them once Kafka
        # returns, so the producer connects in the background with retries.
        start_task = None
        if st.producer is not None:
            async def _start_with_retry():
                while True:
                    try:
                        await st.producer.start()
                        return
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logging.getLogger("api").warning(
                            "kafka producer start failed; retrying in 5s")
                        await asyncio.sleep(5)

            start_task = asyncio.create_task(_start_with_retry())
        # The live feeds are best-effort in exactly the same way: Relay's SSE
        # endpoint works off the local fan-out alone (this pod's own posts), and
        # the consumer only adds what the OTHER writers — another pod, the
        # recorder, a bridge — put on the bus. The ticket board is fed by its
        # consumer alone, so it goes quiet rather than wrong when Kafka is down.
        async def _feed_forever(name, feed, factory):
            while True:
                consumer = factory()
                try:
                    await consumer.start()
                    await feed.run(consumer, st.producer)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logging.getLogger("api").warning(
                        "%s feed consumer failed; retrying", name, exc_info=True)
                finally:
                    try:
                        await consumer.stop()
                    except Exception:
                        pass
                # Paced whichever way the loop ended — a broker that is
                # refusing connections and a consumer that closed cleanly
                # under us both have to wait, or this is a hot loop.
                await asyncio.sleep(5)

        feed_tasks = [asyncio.create_task(_feed_forever(name, feed, factory))
                      for name, feed, factory in
                      (("relay", st.feed, st.feed_consumer_factory),
                       ("tickets", st.ticket_feed, st.ticket_feed_consumer_factory))
                      if factory is not None]
        try:
            yield
        finally:
            for task in feed_tasks:
                task.cancel()
            if start_task is not None:
                start_task.cancel()
            # The store's TTL refresh is scheduled, not awaited, so shutdown can
            # land on top of one mid-query. Cancel it before the producer stops
            # and the engine goes: an ordered teardown, not a destroyed task.
            await st.agent_store.aclose()
            if st.producer is not None:
                try:
                    await st.producer.stop()
                except Exception:
                    pass

    # operationId = the endpoint function name (e.g. `list_agents`) instead of
    # FastAPI's default `list_agents_api_agents_get`. This is the SDK's method
    # name: the client is generated from this spec, so clean, stable operationIds
    # give a clean generated API. Endpoint function names are unique across the
    # app (asserted by tests/test_openapi.py).
    app = FastAPI(title="agent-platform", version="0.1.0", lifespan=lifespan,
                  generate_unique_id_function=lambda route: route.name)
    st = app.state
    st.settings, st.session_factory, st.producer = settings, session_factory, producer
    st.consumer_factory = consumer_factory
    st.feed_consumer_factory = feed_consumer_factory
    st.ticket_feed_consumer_factory = ticket_feed_consumer_factory
    secret_store = secret_store or InMemorySecretStore()
    agent_store = agent_store or AgentStore(session_factory)
    st.secret_store, st.agent_store = secret_store, agent_store
    # Created here rather than in the lifespan: a test (and the SDK generator)
    # drives the app without one, and an endpoint that publishes into a feed
    # that does not exist yet would fail on the first post.
    st.feed = RelayFeed(session_factory)
    st.ticket_feed = tickets_api.ticket_feed(session_factory)
    from agentplatform.skills import SkillStore
    st.skill_store = SkillStore(Path(settings.skills_root))
    from agentplatform.secretregistry import SecretRegistry
    st.secret_registry = SecretRegistry(Path(settings.secrets_root))
    from agentplatform.reportregistry import ReportTypeRegistry
    st.report_registry = ReportTypeRegistry(Path(settings.reports_root))
    from agentplatform.appregistry import AppRegistry
    st.app_registry = AppRegistry(Path(settings.apps_root))
    from agentplatform.toolregistry import ToolRegistry
    st.tool_registry = ToolRegistry(Path(settings.tools_root))

    app.include_router(auth.router)
    app.include_router(apps_api.router)
    app.include_router(apikeys_api.router)
    app.include_router(audit_api.router)
    app.include_router(conversations_api.router)
    app.include_router(cron_api.router)
    app.include_router(dlq_api.router)
    app.include_router(health_api.router)
    app.include_router(help_api.router)
    app.include_router(integrations_api.router)
    app.include_router(maintenance_api.router)
    app.include_router(memory_api.router)
    app.include_router(metrics_api.router)
    app.include_router(pulls_api.router)
    app.include_router(relay_api.router)
    app.include_router(reports_api.router)
    app.include_router(schedules_api.router)
    app.include_router(jobs_api.router)
    app.include_router(notify_api.router)
    app.include_router(webhooks_api.router)
    app.include_router(secrets_api.router)
    app.include_router(agents_api.router)
    app.include_router(runs_api.router)
    app.include_router(skills_api.router)
    app.include_router(tickets_api.router)
    app.include_router(tools_api.router)
    app.include_router(tail_api.router)
    return app
