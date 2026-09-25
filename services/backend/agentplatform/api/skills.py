import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, ValidationError
from yaml import YAMLError

from agentplatform.agentspec import validate_agent_name
from agentplatform.api import schemas as S
from agentplatform.api.auth import READ_ROLES, require_admin, require_role
from agentplatform.db import Run
from agentplatform.events import TOPIC_RUN_REQUESTS

router = APIRouter()

log = logging.getLogger("skills-api")


def _agents_using(request: Request, skill_name: str) -> list[str]:
    """Names of agents whose manifest references this skill."""
    out = []
    for a in request.app.state.agent_store.list():
        if a.manifest and skill_name in a.manifest.skills:
            out.append(a.name)
    return out


@router.get("/api/skills", response_model=list[S.SkillView], dependencies=[Depends(require_role(*READ_ROLES))])
async def list_skills(request: Request):
    request.app.state.skill_store.reload()
    # Reload agents too so `used_by` reflects the latest synced manifests.
    await request.app.state.agent_store.reload()
    return [{"name": s.name,
             "description": s.skill.description if s.skill else "",
             "icon": s.skill.icon if s.skill else "",
             "origin": s.origin,
             "secrets": [],  # compatibility field; skills grant no secrets
             "error": s.error,
             "used_by": _agents_using(request, s.name)}
            for s in request.app.state.skill_store.list()]


@router.get("/api/skills/{name}", response_model=S.SkillDetail, dependencies=[Depends(require_role(*READ_ROLES))])
async def get_skill(request: Request, name: str):
    s = request.app.state.skill_store.get(name)
    if s is None:
        raise HTTPException(404, "unknown skill")
    return {"name": s.name,
            "description": s.skill.description if s.skill else "",
            "icon": s.skill.icon if s.skill else "",
            "origin": s.origin,
            "secrets": [],
            "error": s.error,
            "body": s.body,
            "raw": s.raw,
            "used_by": _agents_using(request, s.name)}


class SkillQuickEditIn(BaseModel):
    value: str            # the full SKILL.md text (frontmatter + body)


@router.post("/api/skills/{name}/quick-edit", response_model=S.EditResult)
async def skill_quick_edit(request: Request, name: str, body: SkillQuickEditIn,
                           principal: str = Depends(require_admin)):
    """Deterministic edit that skips the agent: writes the exact SKILL.md the
    caller supplies and ALWAYS opens a pull request on the skill's
    deterministic branch (`coder/skill-{name}`) — the same
    save→pending-change→review contract as the agent definition editor."""
    from agentplatform.api.gitedit import _apply_files
    request.app.state.skill_store.reload()
    info = request.app.state.skill_store.get(name)
    if info is None:
        raise HTTPException(404, "unknown skill")
    if info.origin == "plugin":
        raise HTTPException(409, "edit plugin skills in the reviewed package")
    # Validate BEFORE proposing: broken frontmatter would quarantine the skill
    # on merge — reject at save time with the parse error.
    from agentplatform.skills import Skill, parse_frontmatter
    try:
        fm, _ = parse_frontmatter(body.value)
        fm.setdefault("name", name)
        Skill(**fm)
    except (ValidationError, YAMLError) as e:
        raise HTTPException(422, f"invalid SKILL.md frontmatter: {e}")
    return await _apply_files(
        request, {f"skills/{name}/SKILL.md": body.value},
        message=f"{principal}: quick-edit skill {name}",
        branch=f"coder/skill-{name}", pr_title=f"Edit skill: {name}",
        pr_body=f"Direct SKILL.md edit for `{name}` from the skills editor.")


class SkillWizardIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    purpose: str          # what the skill does
    when_to_use: str = "" # when an agent should reach for it
    notes: str = ""


@router.post("/api/skills/new", status_code=202, response_model=S.EditDispatch)
async def skill_wizard(request: Request, body: SkillWizardIn,
                       principal: str = Depends(require_admin)):
    """The New-Skill wizard: turn interview answers into an engineer Workbench run
    that AUTHORS the skill. The result lands as a pull request under
    Changes — agent-authored, human-reviewed."""
    st = request.app.state
    try:
        validate_agent_name(body.name)
    except ValueError as e:
        raise HTTPException(422, str(e))
    st.skill_store.reload()
    if st.skill_store.get(body.name) is not None:
        raise HTTPException(409, "a skill with this name already exists")
    await st.agent_store.reload()
    engineer = st.agent_store.get("coder")
    if (engineer is None or engineer.error is not None or not engineer.enabled
            or engineer.manifest.role != "dev"):
        raise HTTPException(409, "coder Workbench is unavailable")
    scope = f"`skills/{body.name}/`"
    secret_part = "\nA skill grants no secrets or Tools. If the workflow needs one, document the needed explicit agent or Tool grant instead.\n"
    prompt = (
        "This is a platform-authored wizard run, not a ticket. Skip the ticket/thread "
        "steps in your standing prompt. Work only in the paths named below, run the "
        "relevant focused tests plus bin/ap-verify --changed, commit the result, and "
        "write .ap/pr.md so the Workbench opens a reviewable pull request.\n\n"
        f"Create a new skill `{body.name}` for the agent platform.\n\n"
        f"Purpose: {body.purpose}\n"
        + (f"When agents should use it: {body.when_to_use}\n" if body.when_to_use else "")
        + secret_part
        + (f"Notes: {body.notes}\n" if body.notes else "")
        + f"\nAuthor `skills/{body.name}/SKILL.md`: YAML frontmatter (name, "
        "description written as a when-to-use trigger, an icon emoji, and any "
        "secrets with state/severity) followed by concise, imperative usage "
        "instructions an agent can follow without guessing. Match the style of "
        f"the existing skills under `skills/`. Only create/modify files under {scope}.")
    run = Run(agent="coder", trigger="wizard", requested_by=principal,
              initiated_by=principal, prompt=prompt)
    async with st.session_factory() as s:
        s.add(run); await s.commit()
    try:
        await st.producer.publish(TOPIC_RUN_REQUESTS, run.id,
                                  {"type": "run", "run_id": run.id}, type="run.request")
    except Exception:  # noqa: BLE001 - sweep will recover a failed broker publish
        log.warning("publish failed for skill-wizard run %s; sweep will drain it", run.id)
    return {"id": run.id, "state": run.state, "target_agent": body.name}
