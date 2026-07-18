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
