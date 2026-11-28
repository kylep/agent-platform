"""U4: the village command port's identity, role table and idempotency
(plan 2026-11-27-village-game §7, §10.1). The handler is a fake: what an
action does is U5's; whether a call reaches it is this port's."""
import uuid

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from agentplatform.db import Run, RunState, VillageCommand, VillageConfig, VillageRun, VillageSession
from agentplatform.village import commands as C
from agentplatform.village import contract as c


class FakeHandler:
    def __init__(self, sf):
        self.sf, self.reads, self.submits = sf, [], []

    async def read(self, caller, action, args):
        self.reads.append((caller, action, args))
        return {"action": action, "persona": caller.persona}

    async def submit(self, caller, command):
        self.submits.append((caller, command))
        async with self.sf() as s:
            row = VillageCommand(**{k: v for k, v in command.to_dict().items()
                                    if k not in ("seq", "apply_tick")})
            s.add(row)
            await s.commit()
            command.seq = row.seq
        return command


async def _session(sf, state="live"):
    async with sf() as s:
        row = VillageSession(day=uuid.uuid4().hex[:10], kind="test", state=state)
        s.add(row)
        await s.commit()
        return row.id


async def _vrun(sf, session_id, *, role="planner", persona="pai", agent=None, slot=None,
                trigger="village", run_state=RunState.RUNNING, row_state="running", row=True):
    agent = agent or ("village-planner" if role == "planner" else persona)
    run_id = uuid.uuid4().hex
    async with sf() as s:
        s.add(Run(id=run_id, agent=agent, trigger=trigger, requested_by="village:test",
                  prompt="x", state=run_state))
        if row:
            s.add(VillageRun(session_id=session_id, persona=persona, role=role, run_id=run_id,
                             slot=slot, state=row_state))
        await s.commit()
    return agent, run_id


@pytest.fixture
async def world(sf):
    handler = FakeHandler(sf)
    port = C.CommandPort(sf, handler)
    sid = await _session(sf)
    return port, handler, sid


async def _refused(port, agent, run_id, body, status=403):
    with pytest.raises(C.Refused) as exc:
        await port.call(agent, run_id, body)
    assert exc.value.status == status, exc.value.reason
    return exc.value.reason


SAY = {"action": "say", "args": {"request_id": "r1", "line": "hello"}}


async def test_planner_reads_and_writes_as_its_bound_persona(sf, world):
    port, handler, sid = world
    agent, run_id = await _vrun(sf, sid, persona="kai")
    out = await port.call(agent, run_id, {"action": "wait", "args": {"cursor": "c0"}})
    assert out["data"] == {"action": "wait", "persona": "kai"}
    caller = handler.reads[0][0]
    assert (caller.persona, caller.role, caller.session_id) == ("kai", "planner", sid)
    out = await port.call(agent, run_id, SAY)
    assert out["data"]["state"] == "accepted" and out["data"]["seq"]
    command = handler.submits[0][1]
    assert command.persona == "kai" and command.source == "planner"
    assert "request_id" not in command.args and command.args_hash == c.args_hash(SAY["args"])


async def test_403_matrix(sf, world):
    """Forged caller, another persona's run id, non-village run, finished run,
    no row, row given up on, closed session (writes), role table, final slot."""
    port, _, sid = world
    planner, p_run = await _vrun(sf, sid, persona="pai")
    _, kai_reflect = await _vrun(sf, sid, role="reflection", persona="kai")
    # forged caller: right run id, wrong agent; or agent claims another persona's run
    assert "agent" in await _refused(port, "pai", p_run, SAY)
    assert "agent" in await _refused(port, "pai", kai_reflect, SAY)
    assert "agent" in await _refused(port, planner, kai_reflect, SAY)
    # no verified caller at all
    await _refused(port, "", p_run, SAY)
    await _refused(port, planner, "", SAY)
    await _refused(port, planner, "f" * 32, SAY)
    # a pai run that is not a village run, and a village-trigger run with no row
    a, r = await _vrun(sf, sid, role="reflection", trigger="conversation")
    assert "not a village run" in await _refused(port, a, r, SAY)
    a, r = await _vrun(sf, sid, role="reflection", row=False)
    assert "not in the village" in await _refused(port, a, r, SAY)
    # finished run, and a row the engine gave up on
    a, r = await _vrun(sf, sid, run_state=RunState.SUCCEEDED)
    assert "finished" in await _refused(port, a, r, {"action": "look", "args": {}})
    a, r = await _vrun(sf, sid, row_state="failed")
    assert "failed" in await _refused(port, a, r, {"action": "look", "args": {}})
    # a reflection row whose run is not the persona's own (planner agent posing)
    a, r = await _vrun(sf, sid, role="reflection", persona="pai", agent="village-planner")
    assert "role" in await _refused(port, a, r, {"action": "look", "args": {}})
    # role table: planner can't reflect, reflection can't wait
    for action, args in [("set_goals", {"request_id": "g", "goals": ["x"]}),
                         ("prompt", {"request_id": "p", "section": "s", "kind": "trial"}),
                         ("note", {"request_id": "n", "text": "t"}),
                         ("recall_notes", {"q": ""})]:
        assert "not for a planner" in await _refused(port, planner, p_run,
                                                     {"action": action, "args": args})
    assert "not for a reflection" in await _refused(port, "kai", kai_reflect,
                                                    {"action": "wait", "args": {}})
    tomorrow = {"action": "tomorrow", "args": {"request_id": "t", "steps": [{"op": "rest"}]}}
    assert "final" in await _refused(port, "kai", kai_reflect, tomorrow)
    _, final = await _vrun(sf, sid, role="reflection", persona="kai", slot="final")
    assert (await port.call("kai", final, tomorrow))["ok"]


async def test_closed_session_refuses_writes_but_still_answers_reads(sf, world):
    port, handler, _ = world
    closed = await _session(sf, "closed")
    agent, run_id = await _vrun(sf, closed)
    assert "closed" in await _refused(port, agent, run_id, SAY)
    out = await port.call(agent, run_id, {"action": "wait", "args": {}})
    assert out["ok"] and handler.reads[-1][0].session_state == "closed"


async def test_planner_agent_follows_the_config_row(sf, world):
    port, _, sid = world
    async with sf() as s:
        s.add(VillageConfig(id=1, rules={"planner_agent": "village-planner-2"}))
        await s.commit()
    agent, run_id = await _vrun(sf, sid)              # still village-planner
    await _refused(port, agent, run_id, SAY)
    agent, run_id = await _vrun(sf, sid, agent="village-planner-2")
    assert (await port.call(agent, run_id, SAY))["ok"]


async def test_request_id_idempotency(sf, world):
    port, handler, sid = world
    agent, run_id = await _vrun(sf, sid)
    first = (await port.call(agent, run_id, SAY))["data"]
    again = (await port.call(agent, run_id, SAY))["data"]
    assert again["seq"] == first["seq"] and again["repeat"] is True
    assert len(handler.submits) == 1
    changed = {"action": "say", "args": {"request_id": "r1", "line": "something else"}}
    assert "different arguments" in await _refused(port, agent, run_id, changed, 409)
    # the same request_id from ANOTHER run is that run's own first write
    agent2, run2 = await _vrun(sf, sid, persona="kai")
    assert (await port.call(agent2, run2, changed))["data"]["seq"] != first["seq"]
    # writes need one, reads take none (and refuse one)
    assert "request_id: required" in await _refused(
        port, agent, run_id, {"action": "say", "args": {"line": "hi"}}, 422)
    await _refused(port, agent, run_id, {"action": "look", "args": {"request_id": "x"}}, 422)
    assert (await port.call(agent, run_id, {"action": "look", "args": {}}))["ok"]


async def test_a_concurrent_retry_gets_the_first_receipt(sf, world):
    port, handler, sid = world
    agent, run_id = await _vrun(sf, sid)
    await port.call(agent, run_id, SAY)

    class Racing(FakeHandler):
        async def submit(self, caller, command):
            raise IntegrityError("insert", {}, Exception("unique"))

    racing = C.CommandPort(sf, Racing(sf))
    real_prior = C._prior
    calls = []

    async def prior_after_race(s, rid, req):
        calls.append(1)
        return None if len(calls) == 1 else await real_prior(s, rid, req)
    C._prior = prior_after_race
    try:
        out = await racing.call(agent, run_id, SAY)
    finally:
        C._prior = real_prior
    assert out["data"]["repeat"] is True


async def test_no_action_can_address_another_character(sf, world):
    port, _, sid = world
    agent, run_id = await _vrun(sf, sid, persona="pai")
    for extra in ("agent", "persona", "character", "run_id"):
        body = {"action": "say", "args": {"request_id": "x" + extra, "line": "hi", extra: "kai"}}
        assert "unknown field" in await _refused(port, agent, run_id, body, 422)
    for args in c.ACTION_ARGS.values():
        assert not {"agent", "persona", "character"} & set(args["properties"])
    await _refused(port, agent, run_id, {"action": "shout", "args": {}}, 422)
    await _refused(port, agent, run_id, ["not", "an", "object"], 422)


async def test_http_face_maps_headers_and_refusals(sf, world):
    port, _, sid = world
    agent, run_id = await _vrun(sf, sid)
    app = C.build_app(port)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://v") as h:
        ok = await h.post("/cmd", json={"action": "look", "args": {}},
                          headers={C.CALLER_HEADER: agent, C.RUN_HEADER: run_id})
        assert ok.status_code == 200 and ok.json()["data"]["persona"] == "pai"
        forged = await h.post("/cmd", json={"action": "look", "args": {}},
                              headers={C.CALLER_HEADER: "pai", C.RUN_HEADER: run_id})
        assert forged.status_code == 403 and forged.json()["ok"] is False
        assert (await h.post("/cmd", json={"action": "look"})).status_code == 403
        assert (await h.post("/cmd", content=b"{nope", headers={
            C.CALLER_HEADER: agent, C.RUN_HEADER: run_id})).status_code == 422
        assert (await h.post("/cmd", content=b"x" * (C.MAX_BODY_BYTES + 1))).status_code == 413
    idle = C.build_app(C.CommandPort(sf))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=idle), base_url="http://v") as h:
        r = await h.post("/cmd", json={"action": "look", "args": {}},
                         headers={C.CALLER_HEADER: agent, C.RUN_HEADER: run_id})
        assert r.status_code == 503


async def test_rows_are_read_in_any_state(sf, world):
    _, _, sid = world
    _, run_id = await _vrun(sf, sid, row_state="cancelled", run_state=RunState.KILLED)
    async with sf() as s:
        assert (await C.village_run(s, run_id)).state == "cancelled"
        assert await C.village_run(s, None) is None
        assert (await s.execute(select(VillageRun))).scalars().first() is not None
