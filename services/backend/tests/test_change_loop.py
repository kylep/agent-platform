"""The standardized change loop (docs/building-blocks/changes.md): sync-status,
validation-before-propose, secret declarations."""
from pathlib import Path

from agentplatform.api.pulls import synced_head


# --- sync-status -------------------------------------------------------------

def _mk_git(root: Path, sha: str, ref="refs/heads/main", packed=False):
    git = root / ".git"
    (git / "refs" / "heads").mkdir(parents=True)
    (git / "HEAD").write_text(f"ref: {ref}\n")
    if packed:
        (git / "packed-refs").write_text(f"# pack-refs\n{sha} {ref}\n")
    else:
        (git / ref).parent.mkdir(parents=True, exist_ok=True)
        (git / ref).write_text(sha + "\n")


def test_synced_head_loose_packed_detached_missing(tmp_path):
    a = tmp_path / "loose"; a.mkdir(); _mk_git(a, "a" * 40)
    assert synced_head(a) == "a" * 40
    b = tmp_path / "packed"; b.mkdir(); _mk_git(b, "b" * 40, packed=True)
    (b / ".git" / "refs" / "heads" / "main").unlink(missing_ok=True)
    assert synced_head(b) == "b" * 40
    c = tmp_path / "detached"; c.mkdir(); (c / ".git").mkdir()
    (c / ".git" / "HEAD").write_text("c" * 40 + "\n")
    assert synced_head(c) == "c" * 40
    assert synced_head(tmp_path / "nope") is None


async def test_sync_status_endpoint(admin_client, tmp_agents):
    # the test app's agents_root is tmp_agents; its parent has no .git → null
    r = await admin_client.get("/api/sync-status")
    assert r.status_code == 200 and r.json() == {"sha": None}
    _mk_git(tmp_agents.parent, "d" * 40)
    r = await admin_client.get("/api/sync-status")
    assert r.json() == {"sha": "d" * 40}


# --- validation before propose ----------------------------------------------

async def test_entrypoints_quick_edit_validates(admin_client):
    r = await admin_client.post("/api/agents/hello-world/quick-edit",
                                json={"field": "entrypoints", "value": 'cron: ["bogus"]'})
    assert r.status_code == 422 and "invalid entrypoints.yaml" in r.json()["detail"]
    # valid yaml reaches the git layer, which 409s in tests (no remote) —
    # proving validation passed
    r = await admin_client.post("/api/agents/hello-world/quick-edit",
                                json={"field": "entrypoints", "value": 'cron: ["0 9 * * *"]'})
    assert r.status_code == 409


async def test_skill_quick_edit_validates_frontmatter(admin_client):
    r = await admin_client.post("/api/skills/git/quick-edit",
                                json={"value": "---\nsecrets: [unclosed\n---\nbody"})
    assert r.status_code == 422 and "frontmatter" in r.json()["detail"]


# --- secret declarations -----------------------------------------------------

async def test_secret_declaration_read(admin_client):
    r = await admin_client.get("/api/secrets/github-token/declaration")
    assert r.status_code == 200
    assert "GITHUB_TOKEN" in r.json()["raw"] and r.json()["error"] is None
    assert (await admin_client.get("/api/secrets/ghost/declaration")).status_code == 404


async def test_secret_quick_edit_validates(admin_client):
    r = await admin_client.post("/api/secrets/github-token/quick-edit",
                                json={"value": "verify:\n  probe: {url: x}\n  script: y\n"})
    assert r.status_code == 422 and "invalid secret.yaml" in r.json()["detail"]
    assert (await admin_client.post("/api/secrets/ghost/quick-edit",
                                    json={"value": "x: 1"})).status_code == 404
    # valid → git layer 409 (unconfigured in tests)
    r = await admin_client.post("/api/secrets/github-token/quick-edit",
                                json={"value": "description: updated\n"})
    assert r.status_code == 409


async def test_declare_secret_validates_and_scaffolds(admin_client):
    import yaml
    from agentplatform.api.secrets import SecretDeclareIn, _render_secret_yaml
    from agentplatform.secretregistry import SecretSpec

    assert (await admin_client.post("/api/secrets/declare",
            json={"name": "Bad Name"})).status_code == 422
    assert (await admin_client.post("/api/secrets/declare",
            json={"name": "github-token"})).status_code == 409  # already declared
    # valid → 409 from the unconfigured git layer (validation + scaffold passed)
    assert (await admin_client.post("/api/secrets/declare", json={
        "name": "notion-token", "description": "Notion integration token",
        "keys": [{"name": "NOTION_TOKEN", "hint": "from notion.so/my-integrations"}],
        "probe": {"url": "https://api.notion.com/v1/users/me",
                  "headers": {"Authorization": "Bearer {NOTION_TOKEN}",
                              "Notion-Version": "2022-06-28"}},
    })).status_code == 409
    # the scaffold round-trips to a valid, faithful SecretSpec
    text = _render_secret_yaml(SecretDeclareIn(
        name="notion-token", description="d", required=True, hint="h",
        keys=[{"name": "NOTION_TOKEN", "hint": "k"}],
        probe={"url": "https://x", "headers": {"Authorization": "Bearer {NOTION_TOKEN}"}}))
    spec = SecretSpec(**yaml.safe_load(text))
    assert spec.required and spec.keys[0].name == "NOTION_TOKEN"
    assert spec.verify.probe.headers["Authorization"] == "Bearer {NOTION_TOKEN}"
