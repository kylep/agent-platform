"""Secret-access audit: the launcher records which k8s secrets each run's pod
was granted; the audit API and run detail expose it."""
from agentplatform.agents import Manifest
from agentplatform.config import Settings
from agentplatform.db import Run, SecretAccess
from agentplatform.joblauncher import K8sJobLauncher
from tests.test_joblauncher import _skill_store


async def _run(sf, agent="a"):
    async with sf() as s:
        r = Run(agent=agent, trigger="manual", requested_by="t", prompt="x")
        s.add(r)
        await s.commit()
        return r.id


async def test_audit_records_base_plus_bound_secrets(sf, tmp_path):
    launcher = K8sJobLauncher(batch=None, settings=Settings(), session_factory=sf,
                              skill_store=_skill_store(tmp_path))  # git skill → github-token
    rid = await _run(sf)
    run = Run(agent="a", trigger="manual", requested_by="t", prompt="x"); run.id = rid
    await launcher._audit_secret_access(run, Manifest(skills=["git"], secrets=["extra"]))
    from sqlalchemy import select
    async with sf() as s:
        secrets = set((await s.execute(select(SecretAccess.secret)
                       .where(SecretAccess.run_id == rid))).scalars())
    assert secrets == {"claude-credentials", "github-token", "extra"}


async def test_audit_api_filters(admin_client, sf):
    async with sf() as s:
        s.add(SecretAccess(run_id="r1", agent="a", secret="claude-credentials"))
        s.add(SecretAccess(run_id="r1", agent="a", secret="github-token"))
        s.add(SecretAccess(run_id="r2", agent="b", secret="claude-credentials"))
        await s.commit()
    all_rows = (await admin_client.get("/api/audit/secret-access")).json()
    assert len(all_rows) == 3
    r1 = (await admin_client.get("/api/audit/secret-access?run_id=r1")).json()
    assert {x["secret"] for x in r1} == {"claude-credentials", "github-token"}
    gh = (await admin_client.get("/api/audit/secret-access?secret=github-token")).json()
    assert len(gh) == 1 and gh[0]["run_id"] == "r1"


async def test_audit_api_admin_only(client, sf):
    assert (await client.get("/api/audit/secret-access")).status_code == 401


async def test_run_detail_includes_granted_secrets(admin_client, sf):
    async with sf() as s:
        run = Run(agent="a", trigger="manual", requested_by="t", prompt="x")
        s.add(run)
        await s.flush()
        rid = run.id
        s.add(SecretAccess(run_id=rid, agent="a", secret="claude-credentials"))
        s.add(SecretAccess(run_id=rid, agent="a", secret="github-token"))
        await s.commit()
    d = (await admin_client.get(f"/api/runs/{rid}")).json()
    assert d["secrets_granted"] == ["claude-credentials", "github-token"]
