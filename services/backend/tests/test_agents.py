from pathlib import Path
from agentplatform.agents import AgentStore

def make_agent(root: Path, name: str, manifest: str = "description: test\n"):
    d = root / name; d.mkdir(parents=True)
    (d / "agent.md").write_text(f"# {name}\nYou are {name}.")
    (d / "manifest.yaml").write_text(manifest)

def test_list_and_quarantine(tmp_path):
    make_agent(tmp_path, "hello-world")
    make_agent(tmp_path, "broken", manifest="concurrency: 'not-an-int'\n")
    store = AgentStore(tmp_path)
    byname = {a.name: a for a in store.list()}
    assert byname["hello-world"].manifest.concurrency == 1
    assert byname["hello-world"].error is None
    assert byname["broken"].error is not None and byname["broken"].manifest is None

async def test_agents_api(admin_client):
    r = await admin_client.get("/api/agents")
    assert r.status_code == 200
