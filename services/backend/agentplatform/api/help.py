"""The Help surface. Concept docs are NOT duplicated into the UI: the pages
under docs/building-blocks/ in the synced git checkout ARE the help content
(single source — editing the doc updates /help within one sync). Tool help
comes from agentspec.TOOL_HELP, the registry a test keeps in lockstep with
the grantable-tool list."""
import re
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from agentplatform.agentspec import TOOL_HELP
from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, require_role

router = APIRouter(dependencies=[Depends(require_role(*READ_ROLES))])

_SLUG = re.compile(r"^[a-z0-9-]+$")


def _docs_root(request: Request) -> Path:
    return Path(request.app.state.settings.agents_root).parent / "docs" / "building-blocks"


def _title(md: str, fallback: str) -> str:
    for line in md.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


@router.get("/api/help/topics", response_model=list[S.HelpTopic])
async def list_help_topics(request: Request):
    """The concept pages (from docs/building-blocks in the synced checkout)."""
    root = _docs_root(request)
    out = []
    if root.is_dir():
        for f in sorted(root.glob("*.md")):
            if f.stem == "README":
                continue
            out.append({"slug": f.stem, "title": _title(f.read_text(), f.stem)})
    return out


@router.get("/api/help/topics/{slug}", response_model=S.HelpTopicDetail)
async def get_help_topic(request: Request, slug: str):
    if not _SLUG.match(slug):
        raise HTTPException(400, "invalid slug")
    f = _docs_root(request) / f"{slug}.md"
    if not f.is_file():
        raise HTTPException(404, "unknown topic")
    md = f.read_text()
    return {"slug": slug, "title": _title(md, slug), "markdown": md}


@router.get("/api/help/tools", response_model=list[S.ToolHelp])
async def list_tool_help():
    """Every grantable tool with what enabling it actually does (incl. which
    ones the runner denies for normal agents regardless of declaration)."""
    return [{"sensitive": False, **t} for t in TOOL_HELP]
