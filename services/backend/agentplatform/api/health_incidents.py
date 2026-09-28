"""The health worker's narrow durable intervention operation."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from agentplatform.api.auth import authenticate
from agentplatform.db import ACTIVE_STATES, AgentDef, Run
from agentplatform.health_incidents import report_incident
from agentplatform.system_agents import HEALTH_TOOL
from agentplatform.ticket_store import TicketBudgetError, TicketRuleError

router = APIRouter()


class IncidentIn(BaseModel):
    incident_key: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9:_.-]*$")
    title: str = Field(default="", max_length=256)
    body: str = Field(default="", max_length=16000)
    resolved: bool = False

    @model_validator(mode="after")
    def title_required(self):
        if not self.resolved and not self.title.strip():
            raise ValueError("an active incident needs a title")
        return self


@router.post("/api/health/incidents")
async def upsert_health_incident(request: Request, body: IncidentIn):
    if await authenticate(request) is None:
        raise HTTPException(401)
    if getattr(request.state, "api_key_agent", None) != "health-monitor":
        raise HTTPException(403, "only the health worker may report incidents")
    st = request.app.state
    async with st.session_factory() as s:
        agent = await s.get(AgentDef, "health-monitor")
        run_id = getattr(request.state, "api_key_run_id", None)
        run = await s.get(Run, run_id) if run_id else None
        frozen = getattr(request.state, "frozen_tools", None)
        if (not agent or not agent.enabled or agent.agent_type != "worker"
                or agent.system_source != "platform:health-monitor"
                or HEALTH_TOOL not in (agent.platform_tools or [])
                or (frozen is not None and HEALTH_TOOL not in frozen)
                or run is None or run.agent != agent.name or run.state not in ACTIVE_STATES):
            raise HTTPException(403, "an active health-worker run with its incident grant is required")
        try:
            ticket = await report_incident(s, st.producer, **body.model_dump(), run=run,
                budget_limit=st.settings.tickets_agent_creates_per_hour)
        except TicketBudgetError:
            await s.rollback()
            raise HTTPException(429, "health incident creation budget exhausted")
        except TicketRuleError as exc:
            await s.rollback()
            raise HTTPException(409, str(exc))
        return {"incident_key": body.incident_key, "resolved": body.resolved,
                "ticket": ticket.key if ticket else None,
                "url": f"/tickets/{ticket.key}" if ticket else None,
                "human_notified": False}
