import asyncio
import base64
import logging

from kubernetes import client as k8s
from kubernetes import config as k8s_config
from kubernetes.client.rest import ApiException

from agentplatform.agents import AgentStore
from agentplatform.config import get_settings
from agentplatform.db import init_db, make_engine, make_session_factory
from agentplatform.dispatcher import Dispatcher
from agentplatform.events import Producer
from agentplatform.githubapp import GitHubApp
from agentplatform.joblauncher import JobWatcher, K8sJobLauncher
from agentplatform.scheduler import Scheduler
from agentplatform.skills import SkillStore

log = logging.getLogger("dispatcher_main")


def _load_k8s_config() -> None:
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


def _load_github_app(core, namespace: str) -> GitHubApp | None:
    """Build a GitHubApp from the `github-app` secret so coder runs can open
    PRs. Returns None if the secret is absent or incomplete (self-edit off)."""
    try:
        sec = core.read_namespaced_secret("github-app", namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    d = {k: base64.b64decode(v).decode() for k, v in (sec.data or {}).items()}
    if not (d.get("app_id") and d.get("install_id") and d.get("private_key")):
        return None
    return GitHubApp(d["app_id"], d["install_id"], d["private_key"])


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()

    _load_k8s_config()
    batch = k8s.BatchV1Api()
    github_app = _load_github_app(k8s.CoreV1Api(), settings.k8s_namespace)
    log.info("self-edit %s", "enabled (github-app loaded)" if github_app else "disabled")

    engine = make_engine(settings.db_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    producer = Producer(settings.kafka_bootstrap)
    while True:
        try:
            await producer.start()
            break
        except Exception:
            log.warning("kafka unreachable; retrying producer start in 5s")
            await asyncio.sleep(5)

    agent_store = AgentStore(settings.agents_root)
    skill_store = SkillStore(settings.skills_root)
    launcher = K8sJobLauncher(batch, settings, github_app=github_app,
                              session_factory=session_factory, skill_store=skill_store)

    dispatcher = Dispatcher(settings, session_factory, producer, agent_store, launcher)
    watcher = JobWatcher(batch, settings, session_factory, producer)
    scheduler = Scheduler(session_factory, agent_store, producer)

    try:
        await asyncio.gather(dispatcher.run_forever(), watcher.run_forever(),
                             dispatcher.sweep_forever(), scheduler.run_forever())
    finally:
        await producer.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
