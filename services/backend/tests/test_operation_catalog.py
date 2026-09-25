"""A new MCP branch is unavailable to pages until its catalog is reviewed."""
import importlib.util
import json
from pathlib import Path

from agentplatform import operation_catalog
from agentplatform.api.live_views import READ_FIELDS


def test_catalog_matches_broker_and_custom_manifest_actions():
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts/compile_live_operation_catalog.py"
    spec = importlib.util.spec_from_file_location("compile_catalog", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    compiled = module.compile_catalog()
    checked_in = json.loads((root / "services/backend/agentplatform/"
                             "live_operation_catalog.json").read_text())
    assert checked_in == compiled
    assert len(compiled["operations"]) == len(operation_catalog.OPERATIONS)
    assert {item["id"] for item in compiled["operations"] if item["view_eligible"]} == (
        set(READ_FIELDS) | {"tickets.create@1"})
    assert {item["id"] for item in compiled["operations"]
            if "unknown" in item["effects"]} == {
        "core.query_app.call@1", "tool.linear.raw_graphql@1"}
    assert all(not item["view_eligible"] for item in compiled["operations"]
               if item["source"] in ("mcp-core", "mcp-custom"))


async def test_catalog_exposes_admitted_contracts_and_rejects_unreviewed_branch(
        admin_client):
    response = await admin_client.get("/api/live-operations?eligible_only=true")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == set(READ_FIELDS) | {"tickets.create@1"}
    all_rows = (await admin_client.get("/api/live-operations")).json()
    excluded = next(item for item in all_rows if item["id"] == "tool.linear.raw_graphql@1")
    assert excluded["view_eligible"] is False
    assert excluded["effects"] == ["unknown"]

    await admin_client.post("/api/app-collections", json={
        "name": "running", "display_name": "Running"})
    bad = await admin_client.post("/api/live-views", json={
        "app_name": "running", "slug": "unsafe", "definition": {
            "title": "Unsafe", "reads": [{"alias": "data",
                "operation": "tool.linear.raw_graphql@1"}]}})
    assert bad.status_code == 422
