"""Durable health handoff survives unavailable Pai and repeated observations."""
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from agentplatform.db import (AgentDef, Base, RelayMessage, Run, RunState, Ticket,
                              ToolAudit, utcnow)
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


async def test_reworded_evidence_is_refreshed_at_most_every_six_hours(sf, producer):
    """The worker rewords its evidence each pass; OPS-26 took ~1,400 edits."""
    from datetime import timedelta

    from agentplatform.db import TicketEvent
    from agentplatform.health_incidents import EVIDENCE_REFRESH

    run_id = await _setup(sf)
    ticket = await _report(sf, producer, run_id)

    async def edits():
        async with sf() as s:
            return await s.scalar(select(func.count()).select_from(TicketEvent).where(
                TicketEvent.ticket_id == ticket.id, TicketEvent.kind == "edited"))

    async def observe(body):
        async with sf() as s:
            await report_incident(s, producer, run=await s.get(Run, run_id),
                incident_key="kafka-down", title="Kafka unreachable", body=body)

    for minute in range(3):
        await observe(f"Broker unreachable, observed 12:{minute:02d}Z.")
    assert await edits() == 0
    async with sf() as s:
        record = await s.get(HealthIncident, "kafka-down")
        record.updated_at = record.updated_at - EVIDENCE_REFRESH - timedelta(minutes=1)
        await s.commit()
    await observe("Broker unreachable, observed 18:05Z.")
    assert await edits() == 1
    async with sf() as s:
        assert "18:05Z" in (await s.get(Ticket, ticket.id)).body


async def _tool_denial_incident(sf, client, *, failure=""):
    from .test_relay_api import _agent_token

    monitor_run = await _setup(sf)
    headers = await _agent_token(sf, "health-monitor", run_id=monitor_run)
    now = utcnow()
    # A denial already outside today's aggregate window still belongs to this
    # incident. Evidence refreshes must not move the verification baseline.
    denied_at = now - timedelta(hours=26)
    async with sf() as s:
        s.add(ToolAudit(tool="memory", agent="health-monitor", action="read",
            args_digest="test-call-signature" if failure != "legacy-digest" else "",
            decision="deny:api", ts=denied_at, run_id=monitor_run))
        await s.commit()
    body = {"incident_key": "tool-denials:memory", "title": "Memory read denied",
            "body": "Inspect the denied memory call."}
    opened = await client.post("/api/health/incidents", json=body, headers=headers)
    assert opened.status_code == 200, opened.text
    async with sf() as s:
        ticket = await s.scalar(select(Ticket).where(Ticket.key == opened.json()["ticket"]))
        ticket.created_at = denied_at + timedelta(minutes=1)
        ticket_id = ticket.id
        recovery_run = Run(agent="pai" if failure == "foreign-run" else "health-monitor",
            state={"failed-run": RunState.FAILED, "pending-run": RunState.RUNNING}.get(
                failure, RunState.SUCCEEDED),
            trigger="manual", requested_by="test", prompt="verification", depth=0,
            started_at=now - timedelta(minutes=5), finished_at=now - timedelta(minutes=1))
        s.add(recovery_run)
        await s.flush()
        if failure not in {"aged-out", "missing-history"}:
            s.add(ToolAudit(tool="wiki" if failure == "wrong-tool" else "memory",
                agent="pai" if failure == "wrong-agent" else "health-monitor",
                action="save" if failure == "wrong-action" else "read",
                args_digest="different-call" if failure == "wrong-arguments" else "test-call-signature",
                decision="allow", ts=now - timedelta(minutes=2),
                run_id=None if failure == "missing-run" else recovery_run.id))
        if failure in {"later-error", "later-denial", "simultaneous-denial"}:
            s.add(ToolAudit(tool="memory", agent="health-monitor", action="read",
                args_digest="test-call-signature",
                decision="error:tool" if failure == "later-error" else "deny:api",
                ts=now - timedelta(minutes=2 if failure == "simultaneous-denial" else 1),
                run_id=recovery_run.id))
        if failure == "missing-history":
            from sqlalchemy import delete
            await s.execute(delete(ToolAudit))
        if failure == "partial-recovery":
            s.add(ToolAudit(tool="memory", agent="health-monitor", action="save",
                args_digest="another-call", decision="deny:api", ts=denied_at))
        await s.commit()
        return headers, ticket_id, recovery_run.id


@pytest.mark.parametrize("failure", [
    "aged-out", "missing-history", "wrong-tool", "wrong-agent", "wrong-action",
    "wrong-arguments", "failed-run", "pending-run", "missing-run", "foreign-run",
    "later-error", "later-denial", "simultaneous-denial", "legacy-digest", "partial-recovery",
])
async def test_tool_denial_recovery_requires_matching_successful_evidence(token_client, sf, failure):
    headers, ticket_id, _ = await _tool_denial_incident(sf, token_client, failure=failure)
    async with sf() as s:
        before = (await s.get(HealthIncident, "tool-denials:memory")).updated_at
        messages = await s.scalar(select(func.count()).select_from(RelayMessage))
    response = await token_client.post("/api/health/incidents", headers=headers, json={
        "incident_key": "tool-denials:memory", "resolved": True,
        "body": "No denials remain in the rolling window."})
    assert response.status_code == 409, response.text
    assert "Expired counters are not recovery evidence" in response.json()["detail"]
    async with sf() as s:
        incident = await s.get(HealthIncident, "tool-denials:memory")
        assert incident.status == "active" and incident.updated_at == before
        assert (await s.get(Ticket, ticket_id)).state == "open"
        assert await s.scalar(select(func.count()).select_from(RelayMessage)) == messages
        assert "Memory read denied" in await pending_health_context(s, "pai")


async def test_verified_tool_recovery_records_runs_and_is_idempotent(token_client, sf):
    headers, ticket_id, recovery_run = await _tool_denial_incident(sf, token_client)
    body = {"incident_key": "tool-denials:memory", "resolved": True,
            "body": "The affected read succeeded."}
    for _ in range(2):
        response = await token_client.post("/api/health/incidents", headers=headers, json=body)
        assert response.status_code == 200, response.text
    async with sf() as s:
        assert (await s.get(HealthIncident, body["incident_key"])).status == "resolved"
        ticket = await s.get(Ticket, ticket_id)
        assert ticket.state == "done"
        assert await pending_health_context(s, "pai") == ""
        messages = (await s.scalars(select(RelayMessage.body).where(
            RelayMessage.thread_root == ticket.root_message_id))).all()
        receipts = [m for m in messages if "Verified matching tool calls" in m]
        assert len(receipts) == 1
        assert f"/runs/{recovery_run}" in receipts[0]
        assert "test-call-signature" not in receipts[0]

    # A later recurrence must not borrow the earlier recovery's evidence.
    async with sf() as s:
        s.add(ToolAudit(tool="memory", agent="health-monitor", action="read",
            args_digest="test-call-signature", decision="deny:api", ts=utcnow()))
        await s.commit()
    recurring = await token_client.post("/api/health/incidents", headers=headers, json={
        "incident_key": body["incident_key"], "title": "Memory denial recurred"})
    assert recurring.status_code == 200
    assert recurring.json()["ticket"] == response.json()["ticket"]
    assert (await token_client.post("/api/health/incidents", headers=headers, json=body)).status_code == 409


async def test_monitor_cannot_bypass_recovery_via_tickets_but_pai_can_dispose(token_client, sf):
    from .test_relay_api import _agent_token
    from agentplatform.tickets import CLOSED_STATES

    headers, ticket_id, _ = await _tool_denial_incident(sf, token_client, failure="aged-out")
    for state in CLOSED_STATES:
        response = await token_client.post(f"/api/tickets/{ticket_id}/move", headers=headers,
            json={"state": state, "reason": "Counters expired."})
        assert response.status_code == 409, response.text
        assert "through health_incident" in response.json()["detail"]
    async with sf() as s:
        pai_run = Run(agent="pai", state=RunState.RUNNING, trigger="manual",
                      requested_by="test", prompt="review", depth=0)
        s.add(pai_run)
        await s.commit()
        pai_run_id = pai_run.id
    pai = await _agent_token(sf, "pai", run_id=pai_run_id)
    response = await token_client.post(f"/api/tickets/{ticket_id}/move", headers=pai,
        json={"state": "done", "reason": "Reviewed and dismissed; no recovery claim."})
    assert response.status_code == 200, response.text
    async with sf() as s:
        assert (await s.get(HealthIncident, "tool-denials:memory")).status == "active"
        assert await pending_health_context(s, "pai") == ""
    # The disposition settles the incident: the monitor's retained key resolves
    # without evidence, and a later denial reopens the Ticket for Pai.
    resolved = await token_client.post("/api/health/incidents", headers=headers, json={
        "incident_key": "tool-denials:memory", "resolved": True, "body": "Pai dismissed it."})
    assert resolved.status_code == 200, resolved.text
    async with sf() as s:
        assert (await s.get(HealthIncident, "tool-denials:memory")).status == "resolved"
    recurred = await token_client.post("/api/health/incidents", headers=headers, json={
        "incident_key": "tool-denials:memory", "title": "Memory denial recurred", "body": "Again."})
    assert recurred.status_code == 200, recurred.text
    async with sf() as s:
        assert (await s.get(Ticket, ticket_id)).state == "open"
