import asyncio
import logging

from kubernetes import client as k8s
from kubernetes import config as k8s_config

from agentplatform.agents import AgentStore
from agentplatform.config import get_settings
from agentplatform.db import init_db, make_engine, make_session_factory
from agentplatform.dispatcher import Dispatcher
from agentplatform.events import Producer
from agentplatform.joblauncher import JobWatcher, K8sJobLauncher

log = logging.getLogger("dispatcher_main")


def _load_k8s_config() -> None:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    _load_k8s_config()
    batch = k8s.BatchV1Api()

    engine = make_engine(settings.db_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    producer = Producer(settings.kafka_bootstrap)
    await producer.start()

    agent_store = AgentStore(settings.agents_root)
    launcher = K8sJobLauncher(batch, settings)

    dispatcher = Dispatcher(settings, session_factory, producer, agent_store, launcher)
    watcher = JobWatcher(batch, settings, session_factory, producer)

    try:
        await asyncio.gather(dispatcher.run_forever(), watcher.run_forever())
    finally:
        await producer.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
