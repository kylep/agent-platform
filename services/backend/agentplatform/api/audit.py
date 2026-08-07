from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from agentplatform.api.auth import require_admin
from agentplatform.db import SecretAccess

from agentplatform.api import schemas as S
router = APIRouter()


def _view(a: SecretAccess) -> dict:
    return {"id": a.id, "run_id": a.run_id, "agent": a.agent, "secret": a.secret,
            "granted_at": a.granted_at.isoformat() if a.granted_at else None}


@router.get("/api/audit/secret-access", response_model=list[S.SecretAccessView], dependencies=[Depends(require_admin)])
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


@router.get("/api/audit/tools", response_model=list[S.ToolAuditView], dependencies=[Depends(require_admin)])
async def list_tool_audit(request: Request, limit: int = Query(100, ge=1, le=1000),
                          decision: str | None = None, agent: str | None = None):
    """The broker's custom-tool audit trail (docs/design/13 E), newest first.
    Args are digests, never raw — safe to show."""
    from sqlalchemy import select
    from agentplatform.db import ToolAudit
    stmt = select(ToolAudit).order_by(ToolAudit.ts.desc()).limit(limit)
    if decision:
        stmt = stmt.where(ToolAudit.decision.startswith(decision))
    if agent:
        stmt = stmt.where(ToolAudit.agent == agent)
    async with request.app.state.session_factory() as s:
        rows = (await s.execute(stmt)).scalars().all()
    return [{"id": r.id, "ts": r.ts.isoformat() if r.ts else None, "run_id": r.run_id,
             "agent": r.agent, "initiated_by": r.initiated_by, "tool": r.tool,
             "args_digest": r.args_digest, "decision": r.decision,
             "latency_ms": r.latency_ms, "result_bytes": r.result_bytes}
            for r in rows]
