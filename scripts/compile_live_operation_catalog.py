"""Compile the reviewed Live App operation inventory from callable surfaces.

Unknown branches are included but never admitted to pages. Keep the generated
JSON checked in: a new Tool/action changes it and fails the lockstep test until
someone reviews the effect, output and human permission contract.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "services/backend/agentplatform/live_operation_catalog.json"

# Branches resolved by helpers or the default arm are not all visible as
# `action == ...` in broker.py. The AST scan below adds any newly explicit arm.
CORE_BRANCHES = {
    "agents_edit": ("create", "get", "list", "update", "delete"),
    "agents_grant": ("get", "set_grants", "add_grant", "remove_grant"),
    "artifacts": ("get", "list", "save", "delete"),
    "image_gen": ("generate", "models"),
}

APP_READS = {
    "running.summary.read@1": ("running", "private"),
    "running.activities.read@1": ("running", "private"),
    "news.summary.read@1": ("news", "private"),
    "news.items.read@1": ("news", "private"),
    "stockmarket.summary.read@1": ("stockmarket", "private"),
    "stockmarket.watchlist.read@1": ("stockmarket", "private"),
    "tcms.overview.read@1": ("tcms", "private"),
    "tcms.runs.read@1": ("tcms", "private"),
}

# These are the reviewed human adapters, not the raw MCP Tool schemas. Their
# bounds are enforced by api/live_views.py and api/live_invocations.py.
READ_CONTRACTS = {
    "running.summary.read@1": {"total_km": "number", "runs": "integer", "activities": "integer", "latest_day": "date?"},
    "running.activities.read@1": {"rows": "running_activity[]"},
    "news.summary.read@1": {"today": "integer", "week": "integer", "total": "integer", "topics": "integer", "latest_day": "date?"},
    "news.items.read@1": {"rows": "news_item[]"},
    "stockmarket.summary.read@1": {"indexes": "integer", "watchlist": "integer", "latest_day": "date?", "latest_brief_day": "date?"},
    "stockmarket.watchlist.read@1": {"rows": "watchlist_item[]"},
    "tcms.overview.read@1": {"failing": "integer", "flaky": "integer", "unlinked": "integer", "prune_candidates": "integer", "coverage_pct": "number"},
    "tcms.runs.read@1": {"rows": "test_run[]"},
}
READ_ROW_LIMITS = {
    "running.activities.read@1": 10,
    "news.items.read@1": 10,
    "stockmarket.watchlist.read@1": 20,
    "tcms.runs.read@1": 10,
}
PLATFORM_READS = {"relay.channel.read@1": "private"}
ROW_FIELDS = {
    "running_activity": {"day": "date?", "name": "string", "type": "string",
                         "distance_km": "number", "pace": "string?"},
    "news_item": {"day": "date?", "title": "string", "source": "string",
                  "topic": "string"},
    "watchlist_item": {"symbol": "string", "label": "string", "status": "string",
                       "latest_close": "number?", "change_pct": "number?"},
    "test_run": {"started_at": "string", "branch": "string", "agent": "string",
                 "n": "integer", "verify_ok": "boolean?"},
    "relay_message": {"created_at": "string", "author": "string", "body": "string"},
}


def _field_schema(kind: str, *, max_rows: int = 0) -> dict:
    if kind.endswith("[]"):
        return {"type": "array", "items": _object_schema(ROW_FIELDS[kind[:-2]]),
                "maxItems": max_rows}
    nullable = kind.endswith("?")
    scalar = kind.rstrip("?")
    shape = {"type": "string" if scalar == "date" else scalar}
    if scalar == "date":
        shape["format"] = "date"
    if scalar in ("integer", "number"):
        shape["minimum"] = 0
    if nullable:
        shape["type"] = [shape["type"], "null"]
    return shape


def _object_schema(fields: dict[str, str], *, max_rows: int = 0) -> dict:
    return {"type": "object", "properties": {
        name: _field_schema(kind, max_rows=max_rows) for name, kind in fields.items()},
        "required": list(fields), "additionalProperties": False}


def _effect_policy(source: str, tool: str, action: str) -> tuple[list[str], str]:
    """Conservative review of each branch; classification never grants access."""
    if source == "mcp-core":
        if tool == "runs_read" or tool == "metrics":
            return ["reads_sensitive"], "private"
        if tool == "runs_write":
            return ["mutates_platform"], "private"
        if tool == "agents_edit":
            return (["reads_sensitive"] if action in ("get", "list")
                    else ["mutates_platform"]), "restricted"
        if tool == "agents_grant":
            return (["reads_sensitive"] if action == "get"
                    else ["mutates_platform"]), "restricted"
        if tool == "relay":
            return (["reads_sensitive"] if action in ("channels", "read", "search")
                    else ["mutates_platform", "external_send", "invokes_agent"]), "private"
        if tool == "tickets":
            return (["reads_sensitive"] if action in ("get", "list", "search")
                    else ["mutates_platform", "invokes_agent"]), "private"
        if tool == "wiki":
            return (["reads_sensitive"] if action in ("read", "search", "list", "history", "wanted")
                    else ["mutates_platform"]), "internal"
        if tool == "artifacts":
            return (["reads_sensitive"] if action in ("list", "get")
                    else ["mutates_platform"]), "private"
        if tool == "image_gen":
            return (["reads_sensitive"] if action == "models"
                    else ["incurs_cost", "mutates_platform"]), "private"
        if tool in ("get_quota_usage", "quota_ok"):
            return ["reads_sensitive", "incurs_cost"], "private"
        # query_app has a variable App/path contract even though its HTTP
        # transport is GET. It cannot inherit a single read classification.
    if source == "mcp-custom":
        if tool == "discord_chat":
            return ["external_send"], "private"
        if tool == "image_gen":
            return (["reads_sensitive"] if action == "models"
                    else ["incurs_cost", "mutates_platform"]), "private"
        if tool in ("index_movers", "stocks"):
            return ["external_read"], "internal"
        if tool == "prices":
            return ["external_read", "mutates_platform"], "internal"
        if tool == "linear":
            if action in ("teams", "search"):
                return ["external_read", "reads_sensitive"], "private"
            if action in ("create", "update", "comment"):
                return ["external_send"], "private"
            # raw_graphql is arbitrarily read or write, never page-eligible.
        if tool == "memory":
            return (["reads_sensitive"] if action == "read"
                    else ["mutates_platform"]), "private"
        if tool == "strava":
            return (["external_read", "reads_sensitive"] if action != "sync"
                    else ["external_read", "reads_sensitive", "mutates_platform"]), "private"
        if tool == "tcms":
            return (["mutates_platform"] if action in ("sync_cases", "record_results")
                    else ["reads_sensitive"]), "internal"
        if tool == "ttrpg":
            return (["mutates_platform"] if action in ("roll", "floor", "command")
                    else ["reads_sensitive"]), "private"
    return ["unknown"], "restricted"


def _core_actions() -> dict[str, list[str]]:
    tree = ast.parse((ROOT / "services/mcp-broker/broker.py").read_text())
    result = {}
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        if not any(isinstance(d, ast.Attribute) and d.attr == "tool"
                   for d in node.decorator_list):
            continue
        actions = set(CORE_BRANCHES.get(node.name, ()))
        for compare in ast.walk(node):
            if (not isinstance(compare, ast.Compare) or
                    not isinstance(compare.left, ast.Name) or
                    compare.left.id not in ("action", "scope")):
                continue
            for target in compare.comparators:
                if isinstance(target, ast.Constant) and isinstance(target.value, str):
                    actions.add(target.value)
                elif isinstance(target, (ast.Tuple, ast.List)):
                    actions.update(item.value for item in target.elts
                                   if isinstance(item, ast.Constant)
                                   and isinstance(item.value, str))
        result[node.name] = sorted(actions or {"call"})
    return result


def _custom_actions() -> dict[str, tuple[str, list[str]]]:
    result = {}
    for path in sorted((ROOT / "tools").glob("*/tool.yaml")):
        manifest = yaml.safe_load(path.read_text())
        name = manifest.get("name", path.parent.name)
        assert name == path.parent.name, path
        choices = manifest.get("params", {}).get("properties", {}).get("action", {}).get("enum")
        result[name] = (manifest.get("category", "service_connector"),
                        sorted(choices or ["call"]))
    return result


def compile_catalog() -> dict:
    operations = []
    for tool, actions in sorted(_core_actions().items()):
        for action in actions:
            effects, classification = _effect_policy("mcp-core", tool, action)
            operations.append({
                "id": f"core.{tool}.{action}@1", "source": "mcp-core",
                "tool": tool, "action": action, "category": "platform_capability",
                "effects": effects, "output_classification": classification,
                "view_eligible": False, "reason": "No reviewed human page adapter",
                "input_schema": None, "output_schema": None,
                "target_scope": None, "supported_callers": [], "limits": None,
                "snapshot_eligible": False})
    for tool, (category, actions) in sorted(_custom_actions().items()):
        for action in actions:
            effects, classification = _effect_policy("mcp-custom", tool, action)
            operations.append({
                "id": f"tool.{tool}.{action}@1", "source": "mcp-custom",
                "tool": tool, "action": action, "category": category,
                "effects": effects, "output_classification": classification,
                "view_eligible": False, "reason": "No reviewed human page adapter",
                "input_schema": None, "output_schema": None,
                "target_scope": None, "supported_callers": [], "limits": None,
                "snapshot_eligible": False})
    for operation_id, (app, classification) in sorted(APP_READS.items()):
        operations.append({
            "id": operation_id, "source": "app-adapter", "tool": app,
            "action": operation_id.split(".")[1], "category": "domain_capability",
            "effects": ["reads_sensitive"], "output_classification": classification,
            "view_eligible": True, "reason": "Owner-scoped bounded adapter",
            "input_schema": _object_schema({}),
            "output_schema": _object_schema(
                READ_CONTRACTS[operation_id], max_rows=READ_ROW_LIMITS.get(operation_id, 0)),
            "target_scope": "owner_app", "supported_callers": ["human_session", "platform_key"],
            "limits": {"timeout_seconds": 8, "max_upstream_bytes": 262144,
                       "max_rows": READ_ROW_LIMITS.get(operation_id, 0),
                       "provider_spend": False}, "snapshot_eligible": True})
    for operation_id, classification in PLATFORM_READS.items():
        operations.append({
            "id": operation_id, "source": "platform-adapter", "tool": "relay",
            "action": "channel.read", "category": "platform_capability",
            "effects": ["reads_sensitive"], "output_classification": classification,
            "view_eligible": True, "reason": "Fixed room, current membership, bounded text",
            "input_schema": _object_schema({}),
            "output_schema": _object_schema({"rows": "relay_message[]"}, max_rows=10),
            "target_scope": "relay_channel_membership",
            "supported_callers": ["human_session", "platform_key"],
            "limits": {"max_rows": 10, "max_output_bytes": 32768,
                       "provider_spend": False}, "snapshot_eligible": False})
    operations.append({
        "id": "tickets.create@1", "source": "human-adapter", "tool": "tickets",
        "action": "create", "category": "platform_capability",
        "effects": ["mutates_platform"], "output_classification": "private",
        "view_eligible": True, "reason": "Explicit grant, target and durable intent",
        "input_schema": {"type": "object", "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 160},
            "body": {"type": "string", "maxLength": 4000}},
            "required": ["title"], "additionalProperties": False},
        "output_schema": _object_schema({"ticket_key": "string"}),
        "target_scope": "ticket_enabled_channel", "supported_callers": ["human_session"],
        "limits": {"intent_ttl_seconds": 300, "provider_spend": False},
        "snapshot_eligible": False})
    operations.sort(key=lambda item: item["id"])
    assert len({item["id"] for item in operations}) == len(operations)
    return {"schema_version": 1, "operations": operations}


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(compile_catalog(), indent=2) + "\n")
