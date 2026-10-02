"""Compile the reviewed Live App operation inventory from callable surfaces.

Unknown branches are included but never admitted to pages. Keep the generated
JSON checked in: a new Tool/action changes it and fails the lockstep test until
someone reviews the effect, output and human permission contract.
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "services/backend/agentplatform/live_operation_catalog.json"
# The manifest model, so a view declaration the registry would refuse can't
# compile into an eligible operation.
sys.path.insert(0, str(ROOT / "services/backend"))
from agentplatform.toolregistry import ToolManifest, ViewAction  # noqa: E402

# Branches resolved by helpers or the default arm are not all visible as
# `action == ...` in broker.py. The AST scan below adds any newly explicit arm.
CORE_BRANCHES = {
    "agents_edit": ("create", "get", "list", "update", "delete"),
    "agents_grant": ("get", "set_grants", "add_grant", "remove_grant"),
    "artifacts": ("get", "list", "save", "delete"),
    "image_gen": ("generate", "models"),
    # Dispatched by `action not in APPS_ACTIONS` and one body per arm, so the
    # verbs are listed here, in broker.APPS_ACTIONS / APP_DATA_ACTIONS order.
    "apps": ("schema", "list", "create", "get", "draft", "notes", "validate",
             "preview", "publish", "rollback", "retire", "authority", "health",
             "propose", "proposal"),
    "app_data": ("describe", "query", "get", "create", "update", "delete",
                 "delete_preview"),
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
PLATFORM_READS = {"relay.channel.read@1": "private",
                  "wiki.recent.read@1": "internal"}
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
    "wiki_summary": {"slug": "string", "title": "string",
                     "summary": "string", "updated_at": "string"},
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
        if tool in {"runs_write", "health_incident"}:
            return ["mutates_platform"], "private"
        if tool == "agent_self":
            return (["reads_sensitive"] if action in ("get", "models") else ["mutates_platform"]), "private"
        if tool == "agents_edit":
            return (["reads_sensitive"] if action in ("get", "list")
                    else ["mutates_platform"]), "restricted"
        if tool == "agents_grant":
            return (["reads_sensitive"] if action == "get"
                    else ["mutates_platform"]), "restricted"
        if tool == "discord":
            if action == "send":
                return ["external_send"], "private"
            if action in ("identities", "endpoints", "read", "receipt", "scan"):
                return ["reads_sensitive"], "private"
            if action == "scan_ack":
                # Advances the owned account's triage cursor; sends nothing.
                return ["mutates_platform"], "private"
            return ["unknown"], "restricted"
        if tool == "relay":
            return (["reads_sensitive"] if action in ("channels", "read", "search")
                    else ["mutates_platform", "invokes_agent"]), "private"
        if tool == "tasks":
            # schedule/reschedule queue a future run of an agent; cancel only
            # withdraws one, so it wakes nobody.
            if action in ("list", "get", "events", "models"):
                return ["reads_sensitive"], "private"
            if action == "cancel":
                return ["mutates_platform"], "private"
            return ["mutates_platform", "invokes_agent"], "private"
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
        if tool == "apps":
            # `notes` reads without text and writes with it, so it is a write.
            return (["reads_sensitive"] if action in (
                "schema", "list", "get", "validate", "preview", "authority", "health")
                else ["mutates_platform"]), "private"
        if tool == "app_data":
            return (["reads_sensitive"] if action in (
                "describe", "query", "get", "delete_preview")
                else ["mutates_platform"]), "private"
        if tool in ("get_quota_usage", "quota_ok"):
            return ["reads_sensitive", "incurs_cost"], "private"
        # query_app has a variable App/path contract even though its HTTP
        # transport is GET. It cannot inherit a single read classification.
    if source == "mcp-custom":
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
        if tool == "judgment":
            # Kai's private record of Kyle (docs/design/38); every action is
            # owner-only and none of it is page-eligible.
            return (["reads_sensitive"] if action in ("recall", "pending")
                    else ["mutates_platform"]), "private"
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


def _custom_actions(tools: Path) -> dict[str, ToolManifest]:
    result = {}
    for path in sorted(tools.glob("*/tool.yaml")):
        raw = yaml.safe_load(path.read_text()) or {}
        raw.setdefault("name", path.parent.name)
        manifest = ToolManifest(**raw)
        assert manifest.name == path.parent.name, path
        result[manifest.name] = manifest
    return result


def _custom_operation(manifest: ToolManifest, action: str) -> dict:
    """Design 39, "Tool views" -> Eligibility: the manifest declares the view
    action AND the reviewed effect row says it only reads. Either alone is not
    enough, so a manifest edit can't admit an action the review hasn't seen."""
    effects, classification = _effect_policy("mcp-custom", manifest.name, action)
    operation = {
        "id": f"tool.{manifest.name}.{action}@1", "source": "mcp-custom",
        "tool": manifest.name, "action": action, "category": manifest.category,
        "effects": effects, "output_classification": classification,
        "view_eligible": False, "reason": "No reviewed human page adapter",
        "input_schema": None, "output_schema": None,
        "target_scope": None, "supported_callers": [], "limits": None,
        "snapshot_eligible": False}
    view: ViewAction | None = manifest.view_actions.get(action)
    if view is None:
        return operation
    if effects != ["reads_sensitive"]:
        operation["reason"] = "View action declared, but its reviewed effects are not only reads_sensitive"
        return operation
    operation.update({
        "view_eligible": True,
        "reason": "Declared view action over App tool roles",
        "input_schema": view.params, "output_schema": view.output_schema,
        "target_scope": list(view.sources),
        # A page render, an agent's query and the materializer.
        "supported_callers": ["human_session", "agent_run", "materializer"],
        "limits": {"max_rows": view.max_rows, "max_output_bytes": view.max_bytes,
                   "timeout_seconds": manifest.timeout_seconds,
                   "provider_spend": False}})
    return operation


def compile_catalog(tools: Path = ROOT / "tools") -> dict:
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
    for _, manifest in sorted(_custom_actions(tools).items()):
        for action in sorted(manifest.actions):
            operations.append(_custom_operation(manifest, action))
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
        wiki = operation_id == "wiki.recent.read@1"
        operations.append({
            "id": operation_id, "source": "platform-adapter",
            "tool": "wiki" if wiki else "relay",
            "action": "recent.read" if wiki else "channel.read",
            "category": "platform_capability",
            "effects": [] if wiki else ["reads_sensitive"],
            "output_classification": classification,
            "view_eligible": True,
            "reason": ("Ten current, non-archived shared wiki summaries" if wiki else
                       "Fixed room, current membership, bounded text"),
            "input_schema": _object_schema({}),
            "output_schema": _object_schema({
                "rows": "wiki_summary[]" if wiki else "relay_message[]"}, max_rows=10),
            "target_scope": "shared_wiki" if wiki else "relay_channel_membership",
            "supported_callers": ["human_session", "platform_key"],
            "limits": {"max_rows": 10, "max_output_bytes": 32768,
                       "provider_spend": False}, "snapshot_eligible": False})
    operations.append({
        "id": "app_data.write@1", "source": "human-adapter", "tool": "app_data",
        "action": "write", "category": "platform_capability",
        "effects": ["mutates_platform"], "output_classification": "private",
        "view_eligible": True,
        "reason": "Only a published typed/v2 template through Kyle's confirmed intent",
        "input_schema": {"type": "object", "properties": {
            "intent_id": {"type": "string"}, "digest": {"type": "string"}},
            "required": ["intent_id", "digest"], "additionalProperties": False},
        "output_schema": _object_schema({"id": "string"}),
        "target_scope": "approved_app_page_template",
        "supported_callers": ["human_session"],
        "limits": {"intent_ttl_seconds": 300, "provider_spend": False},
        "snapshot_eligible": False})
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
    operations.append({
        "id": "relay.channel.post@1", "source": "human-adapter", "tool": "relay",
        "action": "post", "category": "platform_capability",
        "effects": ["mutates_platform"], "output_classification": "private",
        "view_eligible": True,
        "reason": "Human-reviewed text to a fixed, member-visible internal room; no mentions or bridge",
        "input_schema": {"type": "object", "properties": {
            "body": {"type": "string", "minLength": 1, "maxLength": 1000}},
            "required": ["body"], "additionalProperties": False},
        "output_schema": _object_schema({"message_id": "string"}),
        "target_scope": "unbound_relay_channel_membership",
        "supported_callers": ["human_session"],
        "limits": {"intent_ttl_seconds": 300, "provider_spend": False},
        "snapshot_eligible": False})
    operations.sort(key=lambda item: item["id"])
    assert len({item["id"] for item in operations}) == len(operations)
    return {"schema_version": 1, "operations": operations}


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(compile_catalog(), indent=2) + "\n")
