"""Durable health handoff survives unavailable Pai and repeated observations."""
from sqlalchemy import func, select

from agentplatform.db import AgentDef, Base, RelayMessage, Run, RunState, Ticket
from agentplatform.health_incidents import (HealthIncident, pending_health_context,
                                             report_incident)


async def _setup(sf, *, pai_enabled=True):
    async with sf() as s:
        connection = await s.connection()
        await connection.run_sync(Base.metadata.create_all)
        for name in ("health-monitor", "pai"):
            row = await s.get(AgentDef, name)
            if row is None:
                row = AgentDef(name=name)
                s.add(row)
            row.enabled = pai_enabled if name == "pai" else True
        run = Run(agent="health-monitor", state=RunState.RUNNING, trigger="cron",
                  requested_by="scheduler", prompt="health", depth=0)
        s.add(run)
        await s.commit()
        return run.id


async def _report(sf, producer, run_id, **kwargs):
    async with sf() as s:
        run = await s.get(Run, run_id)
        return await report_incident(s, producer, run=run, incident_key="kafka-down",
            title="Kafka unreachable", body="Broker unreachable; inspect broker service.", **kwargs)


async def test_incident_dedup_recovery_and_recurrence(sf, producer):
    run_id = await _setup(sf)
    ticket = await _report(sf, producer, run_id)
    async with sf() as s:
        messages_before = await s.scalar(select(func.count()).select_from(RelayMessage))
    duplicate = await _report(sf, producer, run_id)
    assert ticket.id == duplicate.id
    async with sf() as s:
        assert await s.scalar(select(func.count()).select_from(RelayMessage)) == messages_before
        assert ticket.key in await pending_health_context(s, "pai")
        assert await pending_health_context(s, "other") == ""
    await _report(sf, producer, run_id, resolved=True)
    async with sf() as s:
        assert (await s.get(HealthIncident, "kafka-down")).status == "resolved"
        assert (await s.get(Ticket, ticket.id)).state == "done"
        assert await pending_health_context(s, "pai") == ""
    recurring = await _report(sf, producer, run_id)
    assert recurring.id == ticket.id and recurring.state == "open"


async def test_unavailable_pai_does_not_lose_incident_and_disposition_stops_reminder(sf, producer):
    run_id = await _setup(sf, pai_enabled=False)
    ticket = await _report(sf, producer, run_id)
    assert ticket.assignee is None
    async with sf() as s:
        assert await pending_health_context(s, "pai") == ""
        (await s.get(AgentDef, "pai")).enabled = True
        await s.commit()
        assert ticket.key in await pending_health_context(s, "pai")
        (await s.get(Ticket, ticket.id)).state = "done"
        await s.commit()
    # A consciously dismissed but still observed incident is not reopened.
    await _report(sf, producer, run_id)
    async with sf() as s:
        assert await pending_health_context(s, "pai") == ""
        assert (await s.get(HealthIncident, "kafka-down")).status == "active"


async def test_incident_endpoint_checks_current_grant_run_and_identity(token_client, sf):
    from .test_relay_api import _agent_token
    from agentplatform.system_agents import HEALTH_TOOL

    run_id = await _setup(sf)
    headers = await _agent_token(sf, "health-monitor", run_id=run_id)
    body = {"incident_key": "dlq", "title": "Messages need inspection", "body": "DLQ > 0"}
    response = await token_client.post("/api/health/incidents", json=body, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["ticket"].startswith("OPS-")
    assert response.json()["human_notified"] is False
    async with sf() as s:
        agent = await s.get(AgentDef, "health-monitor")
        agent.platform_tools = [t for t in agent.platform_tools if t != HEALTH_TOOL]
        await s.commit()
    assert (await token_client.post("/api/health/incidents", json=body, headers=headers)).status_code == 403
    other = await _agent_token(sf, "pai", run_id=run_id)
    assert (await token_client.post("/api/health/incidents", json=body, headers=other)).status_code == 403
