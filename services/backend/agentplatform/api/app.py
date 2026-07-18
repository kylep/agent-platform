from pathlib import Path

from fastapi import FastAPI
from agentplatform.agents import AgentStore
from agentplatform.api import agents as agents_api
from agentplatform.api import auth
from agentplatform.api import runs as runs_api
from agentplatform.api import secrets as secrets_api
from agentplatform.secrets import InMemorySecretStore

def create_app(settings, session_factory, producer, secret_store=None, agent_store=None) -> FastAPI:
    app = FastAPI(title="agent-platform", version="0.1.0")
    st = app.state
    st.settings, st.session_factory, st.producer = settings, session_factory, producer
    secret_store = secret_store or InMemorySecretStore()
    agent_store = agent_store or AgentStore(Path(settings.agents_root))
    st.secret_store, st.agent_store = secret_store, agent_store
    app.include_router(auth.router)
    app.include_router(secrets_api.router)
    app.include_router(agents_api.router)
    app.include_router(runs_api.router)
    return app
