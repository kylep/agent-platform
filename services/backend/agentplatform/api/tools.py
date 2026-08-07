"""Custom platform tools (docs/design/12) — the surface over the tools/
registry. Tools are git-defined executables the MCP broker serves and the
tool-executor runs; this API is what the Skills & Tools page renders. Writes
ride the standard change loop: quick-edit = deterministic PR on
`coder/tool-<name>`, the wizard = platform-coder authors the tool."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from agentplatform.agentspec import parse_agent_tools, validate_agent_name
from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, require_admin, require_role

router = APIRouter()

log = logging.getLogger("tools-api")

_read = Depends(require_role(*READ_ROLES))


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
            "secrets": list(m.infra.secrets) if m else [],
            "database": m.infra.database if m else False,
            "has_requirements": t.has_requirements,
            "timeout_seconds": m.timeout_seconds if m else 0,
            "error": t.error,
            "used_by": _agents_using(request, f"mcp__platform__{t.name}")}


@router.get("/api/tools", response_model=list[S.ToolView], dependencies=[_read])
async def list_tools(request: Request):
    request.app.state.tool_registry.reload()
    request.app.state.agent_store.reload()
    return [_view(request, t) for t in request.app.state.tool_registry.list()]


@router.get("/api/tools/{name}", response_model=S.ToolDetail, dependencies=[_read])
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


_EDITABLE = ("tool.yaml", "run.py", "requirements.txt", "test_run.py")


class ToolQuickEditIn(BaseModel):
    files: dict[str, str]   # filename → full new content (subset of _EDITABLE)


@router.post("/api/tools/{name}/quick-edit", response_model=S.EditResult)
async def tool_quick_edit(request: Request, name: str, body: ToolQuickEditIn,
                          principal: str = Depends(require_admin)):
    """Deterministic multi-file edit: writes exactly what the caller supplies
    and ALWAYS opens a pull request on `coder/tool-{name}` — the same
    save→pending-change→review contract as the skill editor. tool.yaml is
    validated before proposing (a broken manifest would silently unregister
    the tool on merge)."""
    from agentplatform.api.agents import _apply_files
    registry = request.app.state.tool_registry
    registry.reload()
    if registry.get(name) is None:
        raise HTTPException(404, "unknown tool")
    bad = [f for f in body.files if f not in _EDITABLE]
    if bad:
        raise HTTPException(422, f"not editable here: {', '.join(bad)}")
    if not body.files:
        raise HTTPException(422, "no files to change")
    if "tool.yaml" in body.files:
        import yaml as _yaml
        from agentplatform.toolregistry import ToolManifest
        try:
            raw = _yaml.safe_load(body.files["tool.yaml"]) or {}
            raw.setdefault("name", name)
            m = ToolManifest(**raw)
            if m.name != name:
                raise ValueError(f"name {m.name!r} must match the directory {name!r}")
        except Exception as e:
            raise HTTPException(422, f"invalid tool.yaml: {e}")
    return await _apply_files(
        request, {f"tools/{name}/{f}": content for f, content in body.files.items()},
        message=f"{principal}: quick-edit tool {name}",
        branch=f"coder/tool-{name}", pr_title=f"Edit tool: {name}",
        pr_body=f"Direct edit of `tools/{name}/` from the tools editor.",
        force_review=True)


class ToolWizardSecret(BaseModel):
    name: str
    env_var: str = ""
    description: str = ""


class ToolWizardIn(BaseModel):
    name: str
    purpose: str            # what the tool does
    arguments: str = ""     # what the model should pass, in prose
    needs_database: bool = False
    secret: ToolWizardSecret | None = None
    notes: str = ""


@router.post("/api/tools/new", status_code=202, response_model=S.EditDispatch)
async def tool_wizard(request: Request, body: ToolWizardIn,
                      principal: str = Depends(require_admin)):
    """The New-Tool wizard: platform-coder authors tool.yaml + run.py (+ test,
    + requirements.txt when deps are needed) as a pending change. The prompt
    teaches it the executor contract so authored tools actually run."""
    st = request.app.state
    name = body.name.strip().lower().replace("-", "_")
    import re as _re
    if not _re.match(r"^[a-z][a-z0-9_]{1,40}$", name):
        raise HTTPException(422, "tool name must be snake_case (letters/digits/_)")
    st.tool_registry.reload()
    if st.tool_registry.get(name) is not None:
        raise HTTPException(409, "a tool with this name already exists")
    st.agent_store.reload()
    coder = st.agent_store.get("platform-coder")
    if coder is None or coder.error is not None:
        raise HTTPException(409, "platform-coder agent is unavailable")
    scope = f"`tools/{name}/`"
    secret_part = ""
    if body.secret:
        try:
            validate_agent_name(body.secret.name)
        except ValueError as e:
            raise HTTPException(422, f"secret {e}")
        scope += f" and `secrets/{body.secret.name}/`"
        secret_part = (
            f"\nIt needs a credential: scaffold `secrets/{body.secret.name}/secret.yaml` "
            f"(mirror existing folders under `secrets/`) with key "
            f"`{body.secret.env_var or 'TOKEN'}` — {body.secret.description or 'see notes'}. "
            f"Bind it in tool.yaml via `infra.secrets: [{body.secret.name}]`; the executor "
            f"injects its keys into run.py's env at call time.")
    db_part = ("\nIt needs storage: set `infra.database: true` — the provisioner makes a "
               f"pg role+schema `tool_{name}` and run.py gets TOOL_DB_URL (psycopg URL). "
               "Qualify tables with the schema name.") if body.needs_database else ""
    prompt = (
        f"Author a new custom platform tool `{name}` under {scope} — read "
        f"`tools/README.md` and mirror an existing tool (e.g. `tools/stocks/`).\n"
        f"What it does: {body.purpose}\n"
        f"Arguments: {body.arguments or 'design a minimal JSON-schema params object'}\n"
        f"{secret_part}{db_part}\n"
        f"Contract: run.py reads the JSON args on stdin, prints a JSON result, exits "
        f"non-zero with a stderr message on failure; env is MINIMAL (only declared "
        f"secrets + TOOL_CALLER_AGENT/TOOL_RUN_ID{' + TOOL_DB_URL' if body.needs_database else ''}). "
        f"tool.yaml needs a model-facing description (>=20 chars) and JSON-schema params. "
        f"Add test_run.py covering the pure logic (no network/db in tests). "
        f"Pip deps go in requirements.txt (they get baked into the executor image).\n"
        f"{('Notes: ' + body.notes) if body.notes else ''}")
    from agentplatform.db import Run
    from agentplatform.events import TOPIC_RUN_REQUESTS
    run = Run(agent="platform-coder", trigger="self-edit", requested_by=principal, prompt=prompt)
    async with st.session_factory() as s:
        s.add(run)
        await s.commit()
    try:
        await st.producer.publish(TOPIC_RUN_REQUESTS, run.id,
                                  {"type": "run", "run_id": run.id}, type="run.request")
    except Exception:
        log.warning("publish failed for tool-wizard run %s; sweep will drain it", run.id)
    return {"id": run.id, "state": run.state, "target_agent": name}
