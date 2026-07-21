from fastapi import APIRouter, Depends, Request

from agentplatform.api.auth import READ_ROLES, require_admin, require_role
from agentplatform.pruning import TranscriptPruner

router = APIRouter()


def _pruner(request: Request) -> TranscriptPruner:
    st = request.app.state
    return TranscriptPruner(st.session_factory, st.agent_store, st.settings)


@router.get("/api/maintenance/retention", dependencies=[Depends(require_role(*READ_ROLES))])
async def retention(request: Request):
    """The effective transcript-retention window (days) per agent, and the
    platform default. <= 0 means keep forever."""
    request.app.state.agent_store.reload()
    p = _pruner(request)
    agents = {a.name: p.retention_days(a.name) for a in request.app.state.agent_store.list()}
    return {"default_days": request.app.state.settings.transcript_retention_days,
            "per_agent_days": agents}


@router.post("/api/maintenance/prune-transcripts", dependencies=[Depends(require_admin)])
async def prune_transcripts(request: Request):
    """Prune transcript events past their agent's retention now. Run metadata is
    kept; only the bulky per-frame events are deleted."""
    deleted = await _pruner(request).prune_once()
    return {"ok": True, "deleted": deleted}
