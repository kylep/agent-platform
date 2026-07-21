from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.api.auth import READ_ROLES, require_role

router = APIRouter()


def _agents_using(request: Request, skill_name: str) -> list[str]:
    """Names of agents whose manifest references this skill."""
    out = []
    for a in request.app.state.agent_store.list():
        if a.manifest and skill_name in a.manifest.skills:
            out.append(a.name)
    return out


@router.get("/api/skills", dependencies=[Depends(require_role(*READ_ROLES))])
async def list_skills(request: Request):
    request.app.state.skill_store.reload()
    return [{"name": s.name,
             "description": s.skill.description if s.skill else "",
             "secrets": s.skill.secrets if s.skill else [],
             "error": s.error,
             "used_by": _agents_using(request, s.name)}
            for s in request.app.state.skill_store.list()]


@router.get("/api/skills/{name}", dependencies=[Depends(require_role(*READ_ROLES))])
async def get_skill(request: Request, name: str):
    s = request.app.state.skill_store.get(name)
    if s is None:
        raise HTTPException(404, "unknown skill")
    return {"name": s.name,
            "description": s.skill.description if s.skill else "",
            "secrets": s.skill.secrets if s.skill else [],
            "error": s.error,
            "body": s.body,
            "used_by": _agents_using(request, s.name)}
