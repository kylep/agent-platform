"""Custom platform tools (docs/design/12) — the read surface over the tools/
registry. Tools are git-defined executables the MCP broker serves and the
tool-executor runs; this API is what the Skills & Tools page renders. Writes
ride the standard change loop (quick-edit lands with the UI phase)."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.agentspec import parse_agent_tools
from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, require_role

router = APIRouter(dependencies=[Depends(require_role(*READ_ROLES))])

log = logging.getLogger("tools-api")


def _agents_using(request: Request, mcp_name: str) -> list[str]:
    """Names of agents whose agent.md declares this tool."""
    out = []
    for a in request.app.state.agent_store.list():
        declared = parse_agent_tools(a.agent_md) or []
        if mcp_name in declared:
            out.append(a.name)
    return out


def _view(request: Request, t) -> dict:
    m = t.manifest
    return {"name": t.name,
            "description": m.description if m else "",
            "secrets": [s.name for s in m.infra.secrets] if m else [],
            "database": m.infra.database if m else False,
            "has_requirements": t.has_requirements,
            "timeout_seconds": m.timeout_seconds if m else 0,
            "error": t.error,
            "used_by": _agents_using(request, f"mcp__platform__{t.name}")}


@router.get("/api/tools", response_model=list[S.ToolView])
async def list_tools(request: Request):
    request.app.state.tool_registry.reload()
    request.app.state.agent_store.reload()
    return [_view(request, t) for t in request.app.state.tool_registry.list()]


@router.get("/api/tools/{name}", response_model=S.ToolDetail)
async def get_tool(request: Request, name: str):
    request.app.state.tool_registry.reload()
    t = request.app.state.tool_registry.get(name)
    if t is None:
        raise HTTPException(404, "unknown tool")
    files = {}
    for fname in ("tool.yaml", "run.py", "requirements.txt", "test_run.py"):
        p = t.dir / fname
        if p.is_file():
            files[fname] = p.read_text()
    return {**_view(request, t),
            "params": t.manifest.params if t.manifest else {},
            "files": files}
