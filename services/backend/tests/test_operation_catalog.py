"""A new MCP branch is unavailable to pages until its catalog is reviewed."""
import importlib.util
import json
from pathlib import Path

from agentplatform import operation_catalog
from agentplatform.api.live_views import READ_FIELDS
from jsonschema import Draft202012Validator


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
        set(READ_FIELDS) | {"tickets.create@1", "relay.channel.post@1",
                            "app_data.write@1", "tool.app_summary.counts@1",
                            "tool.running.dashboard@1", "tool.running.calendar@1",
                            "tool.running.weekly@1"})
    assert {item["id"] for item in compiled["operations"]
            if "unknown" in item["effects"]} == {
        "core.query_app.call@1", "tool.linear.raw_graphql@1",
        # Design 35 introduced these Tools without a reviewed page contract.
        "tool.backtest.describe_primitives@1", "tool.backtest.validate@1",
        "tool.backtest.run@1", "tool.backtest.rerun@1",
        "tool.stockmarket.add_symbol@1", "tool.stockmarket.remove_symbol@1",
        "tool.stockmarket.brief@1", "tool.stockmarket.latest@1"}
    assert all(not item["view_eligible"] for item in compiled["operations"]
               if item["source"] in ("mcp-core", "mcp-custom")
               and item["id"] not in {"tool.app_summary.counts@1",
                                      "tool.running.dashboard@1",
                                      "tool.running.calendar@1",
                                      "tool.running.weekly@1"})
    for item in compiled["operations"]:
        if item["view_eligible"]:
            assert item["input_schema"] is not None
            assert item["output_schema"] is not None
            assert item["target_scope"]
            assert item["supported_callers"]
            assert item["limits"] is not None
            assert item["limits"]["provider_spend"] is False
            assert isinstance(item["snapshot_eligible"], bool)
            Draft202012Validator.check_schema(item["input_schema"])
            Draft202012Validator.check_schema(item["output_schema"])
        else:
            assert item["input_schema"] is None
            assert item["output_schema"] is None
            assert item["target_scope"] is None
            assert item["supported_callers"] == []
            assert item["limits"] is None
            assert item["snapshot_eligible"] is False


async def test_catalog_exposes_admitted_contracts_and_rejects_unreviewed_branch(
        admin_client):
    response = await admin_client.get("/api/live-operations?eligible_only=true")
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()}
    assert ids == set(READ_FIELDS) | {"tickets.create@1", "relay.channel.post@1"}
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


def _compiler():
    root = Path(__file__).resolve().parents[3]
    spec = importlib.util.spec_from_file_location(
        "compile_catalog", root / "scripts/compile_live_operation_catalog.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VIEW_TOOL = """\
name: {name}
description: A test tool that declares one view action over an App role.
params:
  type: object
  properties:
    action: {{type: string, enum: [{actions}]}}
    field: {{type: string}}
app_access: {{roles: [source], verbs: [read]}}
view_actions:
  {view}:
    output_schema:
      type: object
      properties: {{rows: {{type: array, items: {{type: object}}}}}}
      required: [rows]
      additionalProperties: false
    max_rows: 25
    max_bytes: 8192
    sources: [source]
    params:
      type: object
      properties: {{field: {{type: string}}}}
      additionalProperties: false
timeout_seconds: 12
"""


def _tool(root, name, actions, view):
    d = root / name
    d.mkdir()
    (d / "tool.yaml").write_text(VIEW_TOOL.format(name=name, actions=actions, view=view))
    (d / "run.py").write_text("")


def test_declared_view_action_is_eligible_only_with_a_reads_sensitive_row(tmp_path):
    """Design 39, "Tool views" -> Eligibility (plan D3): the manifest's
    declaration and the reviewed effect row must BOTH say read-only."""
    # judgment.recall's reviewed row is exactly reads_sensitive.
    _tool(tmp_path, "judgment", "belief, recall", "recall")
    # memory.write mutates: declaring it a view does not make it one.
    _tool(tmp_path, "memory", "read, write", "write")
    # strava.activities reads, but also reaches outside: not only reads_sensitive.
    _tool(tmp_path, "strava", "activities, sync", "activities")
    # No reviewed row at all: unknown effects.
    _tool(tmp_path, "unreviewed", "counts", "counts")
    ops = {op["id"]: op for op in _compiler().compile_catalog(tmp_path)["operations"]}

    recall = ops["tool.judgment.recall@1"]
    assert recall["view_eligible"] is True
    assert recall["effects"] == ["reads_sensitive"]
    assert recall["target_scope"] == ["source"]
    assert recall["input_schema"] == {"type": "object", "properties": {
        "field": {"type": "string"}}, "additionalProperties": False}
    assert recall["output_schema"]["required"] == ["rows"]
    assert recall["limits"] == {"max_rows": 25, "max_output_bytes": 8192,
                                "timeout_seconds": 12, "provider_spend": False}
    assert recall["supported_callers"] and recall["snapshot_eligible"] is False
    Draft202012Validator.check_schema(recall["input_schema"])
    Draft202012Validator.check_schema(recall["output_schema"])
    for op_id in ("tool.memory.write@1", "tool.strava.activities@1",
                  "tool.unreviewed.counts@1", "tool.judgment.belief@1"):
        op = ops[op_id]
        assert op["view_eligible"] is False, op_id
        assert op["output_schema"] is None and op["target_scope"] is None
        assert op["limits"] is None and op["supported_callers"] == []
    assert "declared" in ops["tool.memory.write@1"]["reason"]


def test_a_broken_view_declaration_fails_the_compile(tmp_path):
    _tool(tmp_path, "judgment", "belief, recall", "pending")   # not in the enum
    try:
        _compiler().compile_catalog(tmp_path)
    except ValueError as e:
        assert "action enum" in str(e)
    else:
        raise AssertionError("an invalid manifest compiled")


def test_tool_view_actions_stay_off_typed_v1_pages(monkeypatch):
    """A tool view is a design-39 page binding. The typed/v1 Live View
    admission (`admitted`) has its own adapters and must not pick it up."""
    op = {"id": "tool.x.counts@1", "source": "mcp-custom", "tool": "x",
          "action": "counts", "effects": ["reads_sensitive"], "view_eligible": True}
    monkeypatch.setitem(operation_catalog.OPERATIONS, op["id"], op)
    assert operation_catalog.admitted(op["id"]) is False
    assert operation_catalog.view_action("x", "counts") == op
    assert operation_catalog.view_action("x", "other") is None
    assert operation_catalog.view_action("judgment", "recall") is None   # declared nowhere
