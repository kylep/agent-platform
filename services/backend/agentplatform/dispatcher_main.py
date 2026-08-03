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
from agentplatform.conversation_ingest import ConversationIngestor
from agentplatform.ingest import Ingestor
from agentplatform.joblauncher import JobWatcher, K8sJobLauncher
from agentplatform.github import GitHubClient
from agentplatform.pruning import ReportPruner, TranscriptPruner, sweep_orphaned_keys_forever
from agentplatform.prsummarizer import PrSummarizer
from agentplatform.reportregistry import ReportTypeRegistry
from agentplatform.scheduler import Scheduler
from agentplatform.secretregistry import SecretRegistry
from agentplatform.secrets import K8sSecretStore
from agentplatform.skills import SkillStore
from agentplatform.verifierloop import SecretVerifier

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
    core = k8s.CoreV1Api()
    github_app = _load_github_app(core, settings.k8s_namespace)
    log.info("self-edit %s", "enabled (github-app loaded)" if github_app else "disabled")

    engine = make_engine(settings.db_url)
    await init_db(engine)
    session_factory = make_session_factory(engine)

    producer = Producer(settings.kafka_bootstrap, source="dispatcher")
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

    verifier = SecretVerifier(SecretRegistry(settings.secrets_root),
                              K8sSecretStore(core, settings.k8s_namespace),
                              session_factory,
                              settings.secret_verify_interval_seconds)
    dispatcher = Dispatcher(settings, session_factory, producer, agent_store, launcher,
                            skill_store=skill_store, verifier=verifier)
    watcher = JobWatcher(batch, settings, session_factory, producer)
    scheduler = Scheduler(session_factory, agent_store, producer)
    pruner = TranscriptPruner(session_factory, agent_store, settings)
    report_pruner = ReportPruner(session_factory, ReportTypeRegistry(settings.reports_root))
    ingestor = Ingestor(settings, session_factory, producer)
    conv_ingestor = ConversationIngestor(settings, session_factory, producer)

    # Auto AI summaries on pending changes: needs the GitHub App (comments)
    # and only makes sense when self-edit is configured.
    def _gh_client():
        if github_app is None or not settings.github_repo:
            return None
        return GitHubClient(github_app.installation_token(), settings.github_repo)

    pr_summarizer = PrSummarizer(_gh_client, session_factory, producer, agent_store)

    try:
        await asyncio.gather(dispatcher.run_forever(), watcher.run_forever(),
                             dispatcher.sweep_forever(), scheduler.run_forever(),
                             pruner.run_forever(), report_pruner.run_forever(),
                             ingestor.run_forever(),
                             conv_ingestor.run_forever(), verifier.run_forever(),
                             sweep_orphaned_keys_forever(session_factory),
                             pr_summarizer.run_forever())
    finally:
        await producer.stop()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
