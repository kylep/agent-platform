from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from agentplatform.api.auth import require_admin
from agentplatform.db import SecretAccess

router = APIRouter()


def _view(a: SecretAccess) -> dict:
    return {"id": a.id, "run_id": a.run_id, "agent": a.agent, "secret": a.secret,
            "granted_at": a.granted_at.isoformat() if a.granted_at else None}


@router.get("/api/audit/secret-access", dependencies=[Depends(require_admin)])
async def secret_access(request: Request, run_id: str | None = None,
                        secret: str | None = None, agent: str | None = None,
                        limit: int = Query(100, ge=1, le=1000)):
    """Audit trail of which k8s secrets each run's pod was granted. Filter by
    run_id, secret, or agent. Admin-only (secret names are sensitive)."""
    conds = []
    if run_id:
        conds.append(SecretAccess.run_id == run_id)
    if secret:
        conds.append(SecretAccess.secret == secret)
    if agent:
        conds.append(SecretAccess.agent == agent)
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(select(SecretAccess).where(*conds)
                .order_by(SecretAccess.granted_at.desc()).limit(limit))).scalars().all()
    return [_view(a) for a in rows]
