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
        # The session factory is built by the lifespan (db_url only resolves in
        # the pod); it hands the store the real one before priming it.
        agent_store=AgentStore(None),
        consumer_factory=kafka_consumer_factory(settings),
    )
    app.state.sa_validator = _make_sa_validator()
    return app


def _make_sa_validator():
    """Production SA-token validator (docs/design/13 A): TokenReview against
    the apiserver, audience-bound to the platform. None outside a cluster —
    the auth path then simply rejects SA-shaped bearers."""
    try:
        from kubernetes import client
        authn = client.AuthenticationV1Api()
    except Exception:
        return None

    async def validate(token: str) -> str | None:
        import asyncio
        from kubernetes.client import V1TokenReview, V1TokenReviewSpec

        def review() -> str | None:
            r = authn.create_token_review(V1TokenReview(
                spec=V1TokenReviewSpec(token=token, audiences=["agent-platform"])))
            if not (r.status and r.status.authenticated):
                return None
            # The audience must actually be ours, not merely accepted.
            if r.status.audiences and "agent-platform" not in r.status.audiences:
                return None
            return r.status.user.username if r.status.user else None

        try:
            return await asyncio.to_thread(review)
        except Exception:
            return None

    return validate


if __name__ == "__main__":
    uvicorn.run(build_app(), host="0.0.0.0", port=8000)
