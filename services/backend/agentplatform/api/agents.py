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
    if a is None:
        raise HTTPException(404)
    return a.model_dump()
