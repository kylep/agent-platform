from agentplatform.db import Run, RunState


async def _seed(sf, **kw):
    async with sf() as s:
        r = Run(agent="hello-world", trigger="manual", requested_by="t", prompt="x",
                state=RunState.SUCCEEDED, **kw)
        s.add(r); await s.commit(); return r.id


async def test_annotate_sets_summary_and_tags(admin_client, sf):
    rid = await _seed(sf)
    r = await admin_client.post(f"/api/runs/{rid}/annotate",
                                json={"summary": "greeted the user", "tags": ["smoke", "greeting"]})
    assert r.status_code == 200
    got = (await admin_client.get(f"/api/runs/{rid}")).json()
    assert got["summary"] == "greeted the user" and got["tags"] == ["smoke", "greeting"]


async def test_tags_list_and_filter(admin_client, sf):
    a = await _seed(sf); b = await _seed(sf)
    await admin_client.post(f"/api/runs/{a}/annotate", json={"tags": ["alpha", "shared"]})
    await admin_client.post(f"/api/runs/{b}/annotate", json={"tags": ["beta", "shared"]})
    tags = (await admin_client.get("/api/tags")).json()
    assert tags == ["alpha", "beta", "shared"]
    only_alpha = (await admin_client.get("/api/runs?tag=alpha")).json()
    assert [r["id"] for r in only_alpha] == [a]
    shared = (await admin_client.get("/api/runs?tag=shared")).json()
    assert {r["id"] for r in shared} == {a, b}


async def test_needs_summary_filter(admin_client, sf):
    done = await _seed(sf); todo = await _seed(sf)
    await admin_client.post(f"/api/runs/{done}/annotate", json={"summary": "done"})
    ids = [r["id"] for r in (await admin_client.get("/api/runs?needs_summary=true")).json()]
    assert todo in ids and done not in ids


async def test_reader_key_can_read_operator_key_can_annotate(admin_client, sf):
    rid = await _seed(sf)
    reader = (await admin_client.post("/api/api-keys", json={"name": "r", "role": "reader", "agent": None})).json()["token"]
    operator = (await admin_client.post("/api/api-keys", json={"name": "o", "role": "operator", "agent": None})).json()["token"]
    admin_client.cookies.clear()
    # reader can read
    assert (await admin_client.get("/api/runs", headers={"Authorization": f"Bearer {reader}"})).status_code == 200
    # reader cannot annotate
    assert (await admin_client.post(f"/api/runs/{rid}/annotate", json={"summary": "x"},
            headers={"Authorization": f"Bearer {reader}"})).status_code == 403
    # operator can annotate
    assert (await admin_client.post(f"/api/runs/{rid}/annotate", json={"summary": "x"},
            headers={"Authorization": f"Bearer {operator}"})).status_code == 200
