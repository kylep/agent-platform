from fastapi import APIRouter, Depends, Request

from fastapi import HTTPException

from agentplatform import maintenance_mode
from agentplatform.api.auth import READ_ROLES, require_admin, require_role
from agentplatform.pruning import TranscriptPruner

from agentplatform.api import schemas as S
router = APIRouter()


def _pruner(request: Request) -> TranscriptPruner:
    st = request.app.state
    return TranscriptPruner(st.session_factory, st.agent_store, st.settings)


@router.get("/api/maintenance/retention", response_model=S.Retention, dependencies=[Depends(require_role(*READ_ROLES))])
async def retention(request: Request):
    """The effective transcript-retention window (days) per agent, and the
    platform default. <= 0 means keep forever."""
    await request.app.state.agent_store.reload()
    p = _pruner(request)
    agents = {a.name: p.retention_days(a.name) for a in request.app.state.agent_store.list()}
    return {"default_days": request.app.state.settings.transcript_retention_days,
            "per_agent_days": agents}


@router.post("/api/maintenance/prune-transcripts", response_model=S.PruneResult, dependencies=[Depends(require_admin)])
async def prune_transcripts(request: Request):
    """Prune transcript events past their agent's retention now. Run metadata is
    kept; only the bulky per-frame events are deleted."""
    deleted = await _pruner(request).prune_once()
    return {"ok": True, "deleted": deleted}


@router.get("/api/maintenance/status", dependencies=[Depends(require_admin)])
async def maintenance_status(request: Request):
    """Whether automation is paused. Any admin credential may read it."""
    async with request.app.state.session_factory() as s:
        return await maintenance_mode.status(s)


@router.post("/api/maintenance/resume")
async def maintenance_resume(request: Request, principal: str = Depends(require_admin)):
    """Leave restore mode. Kyle's browser session only: an admin API key (which
    an agent could hold) reads the status but cannot release the pause."""
    if getattr(request.state, "auth_kind", None) != "session":
        raise HTTPException(403, "only Kyle's session can resume automation")
    async with request.app.state.session_factory() as s:
        await maintenance_mode.resume(s, principal)
        await s.commit()
        return await maintenance_mode.status(s)
