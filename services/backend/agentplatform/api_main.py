import uvicorn

from agentplatform.agents import AgentStore
from agentplatform.api.app import create_app, kafka_consumer_factory
from agentplatform.config import get_settings
from agentplatform.events import Producer
from agentplatform.secrets import InMemorySecretStore, K8sSecretStore


def build_app():
    settings = get_settings()
    producer = Producer(settings.kafka_bootstrap, source="api")
    try:
        from kubernetes import client, config

        config.load_incluster_config()
        store = K8sSecretStore(client.CoreV1Api(), settings.k8s_namespace)
    except Exception:
        store = InMemorySecretStore()
    app = create_app(
        settings,
        None,
        producer,
        secret_store=store,
        agent_store=AgentStore(settings.agents_root),
        consumer_factory=kafka_consumer_factory(settings),
    )
    return app


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=8000)
