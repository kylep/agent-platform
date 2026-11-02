"""Persona commitments (docs/design/44 A, GEN-8): a promise made in one run is
pushed to every later run of the persona until it is completed or voided."""
from sqlalchemy import select

from agentplatform.commitments import OPEN_LIMIT, Commitment, CommitmentDelivery
from agentplatform.db import Run, RunState

from .test_agentdef_api import _auth, _session_key
from .test_relay_api import _agent_token

MEM = ["mcp__platform__memory"]


async def _run(sf, agent, *, tools=MEM, **fields):
    async with sf() as s:
        run = Run(agent=agent, trigger=fields.pop("trigger", "manual"), requested_by="t", prompt="x",
                  state=RunState.RUNNING, definition_snapshot={"name": agent, "prompt": "p",
                                                               "platform_tools": tools}, **fields)
        s.add(run)
        await s.commit()
        return run.id


async def _persona(seed_agent, name="pai", **fields):
    fields.setdefault("platform_tools", MEM)
    await seed_agent(name, agent_type="persona", **fields)


async def _commit(client, sf, agent, run_id, **body):
    headers = await _agent_token(sf, agent, role="reader", run_id=run_id)
    return await client.post("/api/commitments", headers=headers,
                             json={"summary": "Bring the model-transplant question to the roundtable",
                                   "destination": "roundtable", **body})


async def _agentdef(client, sf, agent, run_id):
    tok = await _session_key(sf, run_id, agent)
    r = await client.get(f"/api/runs/{run_id}/agentdef", headers=_auth(tok))
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_promise_in_one_run_reaches_a_fresh_run_until_completed(token_client, sf, seed_agent):
    await _persona(seed_agent)
    dm = await _run(sf, "pai", conversation_id="c" * 32, trigger_message_id="m" * 32)
    made = await _commit(token_client, sf, "pai", dm,
                         meaning="Kyle wants the personas to weigh whether swapping models changes who they are")
    assert made.status_code == 201, made.text
    c = made.json()
    # Provenance comes from the creating run's row, not from the model.
    assert (c["source_run_id"], c["source_channel_id"], c["source_message_id"]) == (dm, "c" * 32, "m" * 32)
    assert c["backfilled"] is False

    roundtable = await _run(sf, "pai", trigger="mention")
    delivered = (await _agentdef(token_client, sf, "pai", roundtable))["open_commitments"]
    assert [d["id"] for d in delivered] == [c["id"]]
    # Meaning survives, verbatim, not just keywords.
    assert "swapping models changes who they are" in delivered[0]["meaning"]
    async with sf() as s:
        assert (await s.execute(select(CommitmentDelivery.run_id).where(
            CommitmentDelivery.commitment_id == c["id"]))).scalars().all() == [roundtable]

    headers = await _agent_token(sf, "pai", role="reader", run_id=roundtable)
    done = await token_client.post(f"/api/commitments/{c['id']}/complete", headers=headers,
                                   json={"result_ref": "roundtable 2026-11-02"})
    assert done.status_code == 200 and done.json()["status"] == "done"
    later = await _run(sf, "pai")
    assert (await _agentdef(token_client, sf, "pai", later))["open_commitments"] == []
    again = await token_client.post(f"/api/commitments/{c['id']}/complete", headers=headers, json={})
    assert again.status_code == 409


async def test_void_removes_it_and_the_open_list_shows_only_open(token_client, sf, seed_agent):
    await _persona(seed_agent)
    rid = await _run(sf, "pai")
    keep = (await _commit(token_client, sf, "pai", rid, summary="keep")).json()
    drop = (await _commit(token_client, sf, "pai", rid, summary="drop")).json()
    headers = await _agent_token(sf, "pai", role="reader", run_id=rid)
    r = await token_client.post(f"/api/commitments/{drop['id']}/void", headers=headers,
                                json={"note": "Kyle cancelled it"})
    assert r.json()["status"] == "void"
    listed = await token_client.get("/api/commitments", headers=headers)
    assert [c["id"] for c in listed.json()] == [keep["id"]]
    assert [c["id"] for c in (await _agentdef(token_client, sf, "pai", await _run(sf, "pai")))["open_commitments"]] == [keep["id"]]


async def test_another_persona_cannot_see_or_close_it(token_client, sf, seed_agent):
    await _persona(seed_agent, "pai")
    await _persona(seed_agent, "olu")
    pai_run = await _run(sf, "pai")
    c = (await _commit(token_client, sf, "pai", pai_run)).json()
    olu_run = await _run(sf, "olu")
    headers = await _agent_token(sf, "olu", role="reader", run_id=olu_run)
    assert (await token_client.post(f"/api/commitments/{c['id']}/complete", headers=headers, json={})).status_code == 404
    assert (await token_client.get("/api/commitments", headers=headers)).json() == []
    assert (await _agentdef(token_client, sf, "olu", olu_run))["open_commitments"] == []


async def test_workers_runs_without_frozen_memory_and_another_runs_token_are_refused(token_client, sf, seed_agent):
    await seed_agent("news", agent_type="worker", platform_tools=MEM)
    worker = await _run(sf, "news")
    assert (await _commit(token_client, sf, "news", worker)).status_code == 403
    await _persona(seed_agent)
    unfrozen = await _run(sf, "pai", tools=[])
    assert (await _commit(token_client, sf, "pai", unfrozen)).status_code == 403
    rid = await _run(sf, "pai")
    assert (await _commit(token_client, sf, "pai", rid)).status_code == 201
    # A run frozen without memory is never shown commitments it cannot close.
    assert (await _agentdef(token_client, sf, "pai", unfrozen))["open_commitments"] == []
    finished = await _run(sf, "pai")
    async with sf() as s:
        (await s.get(Run, finished)).state = RunState.SUCCEEDED
        await s.commit()
    assert (await _commit(token_client, sf, "pai", finished)).status_code == 403


async def test_the_open_limit_equals_the_delivery_limit(token_client, sf, seed_agent):
    await _persona(seed_agent)
    rid = await _run(sf, "pai")
    for i in range(OPEN_LIMIT):
        assert (await _commit(token_client, sf, "pai", rid, summary=f"promise {i}")).status_code == 201
    over = await _commit(token_client, sf, "pai", rid, summary="one too many")
    assert over.status_code == 409 and over.json()["detail"]["code"] == "commitment_limit"
    delivered = (await _agentdef(token_client, sf, "pai", await _run(sf, "pai")))["open_commitments"]
    assert len(delivered) == OPEN_LIMIT and delivered[0]["summary"] == "promise 0"


async def test_input_bounds_and_admin_surfaces(token_client, admin_client, sf, seed_agent):
    await _persona(seed_agent)
    rid = await _run(sf, "pai")
    assert (await _commit(token_client, sf, "pai", rid, summary="x" * 201)).status_code == 422
    assert (await _commit(token_client, sf, "pai", rid, meaning="nul\x00")).status_code == 422
    c = (await _commit(token_client, sf, "pai", rid)).json()
    listed = await admin_client.get("/api/agents/pai/commitments")
    assert [x["id"] for x in listed.json()] == [c["id"]]
    stats = (await admin_client.get("/api/commitments/stats")).json()
    assert stats[0]["agent"] == "pai" and stats[0]["open"] == 1
    voided = await admin_client.post(f"/api/agents/pai/commitments/{c['id']}/void", json={"note": "test"})
    assert voided.status_code == 200 and voided.json()["status"] == "void"
    async with sf() as s:
        assert (await s.get(Commitment, c["id"])).closed_by_run_id is None
