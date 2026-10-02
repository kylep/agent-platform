"""Run a reviewed App tool view in the isolated views executor as its viewer."""
from __future__ import annotations

import json
import logging
import uuid

import httpx
import jsonschema

from agentplatform.appdata import credentials, viewcache
from agentplatform.appdata.access import Caller, RecordError
from agentplatform.appdata.definitions import ToolViewDef
from agentplatform.appdata.models import AppDataToolCall
from agentplatform.appdata.views import _as_of, resolve_params
from agentplatform.db import utcnow
from agentplatform.operation_catalog import view_action

log = logging.getLogger("appdata.toolviews")


def _binding(ctx, view: ToolViewDef, app_state):
    approved = view_action(view.tool, view.action)
    info = app_state.tool_registry.get(view.tool)
    manifest = info.manifest if info else None
    declaration = manifest.view_actions.get(view.action) if manifest else None
    fact = ctx.bundle.app_tools.get(view.tool)
    if approved is None or declaration is None or fact is None:
        raise RecordError("AD-TOOL-VIEW-DISABLED", "this tool view's reviewed action is unavailable", 503)
    if (declaration.output_schema != approved["output_schema"]
            or declaration.max_rows != approved["limits"]["max_rows"]
            or declaration.max_bytes != approved["limits"]["max_output_bytes"]
            or set(declaration.sources) != set(approved["target_scope"])):
        raise RecordError("AD-TOOL-VIEW-DISABLED", "the tool view action has changed since review", 503)
    sources = {}
    for role in view.sources:
        binding = fact.roles.get(role)
        if (role not in declaration.sources or binding is None
                or "read" not in binding.verbs):
            raise RecordError("AD-TOOL-VIEW-DISABLED", "a source binding is unavailable", 503)
        ctx.collection(binding.collection)
        sources[role] = binding.collection
    return approved, manifest, sources


async def execute(session, ctx, caller: Caller, view: ToolViewDef, params: dict | None,
                  app_state, *, limit: int | None = None, cursor: str | None = None,
                  use_cache: bool = True, materializing: bool = False,
                  capture_fields: list[str] | None = None) -> dict:
    if cursor is not None:
        raise RecordError("AD-CURSOR", "tool views do not page", 422)
    if limit is not None and not 1 <= limit <= 200:
        raise RecordError("AD-LIMIT", "limit must be 1-200", 422)
    if ctx.status != "active":
        raise RecordError("AD-APP-RETIRED", "retired Apps do not run tool views", 409)
    approved, manifest, sources = _binding(ctx, view, app_state)
    if view.materialize is not None and not materializing:
        from agentplatform.appdata.materialized import read
        return await read(session, ctx, caller, view, params, sources)
    for collection in sources.values():
        ctx.access(ctx.collection(collection), caller).require_rows()
    arguments = resolve_params(view, params)
    try:
        jsonschema.validate(arguments, approved["input_schema"])
    except jsonschema.ValidationError as exc:
        raise RecordError("AD-PARAM-TYPE", f"tool view parameters: {exc.message}", 422) from None
    cache_key = None
    if use_cache and view.cache != "none" and view.materialize is None:
        cache_key = await viewcache.key(session, ctx, caller, view,
                                        {**arguments, "__limit": limit}, sources)
        cached = await viewcache.get(session, cache_key)
        if cached is not None:
            return cached
    call_id = uuid.uuid4().hex
    keys = await credentials.keypair(app_state)
    token, claim = credentials.mint_view_exec(
        keys["private_key"], call_id=call_id, principal=caller.principal,
        app_id=ctx.app_id, tool=view.tool, action=view.action, sources=sources,
        cnf_sa=app_state.settings.tool_executor_views_service_account,
        ttl_seconds=manifest.timeout_seconds + credentials.EXP_SLACK_SECONDS)
    await credentials.record(session, claim)
    await session.commit()
    payload = {"tool": view.tool, "args": {"action": view.action, **arguments},
               "credential": {"token": token, "call_id": call_id}}
    try:
        try:
            async with httpx.AsyncClient(
                    base_url=app_state.settings.tool_executor_views_url,
                    timeout=manifest.timeout_seconds + 5) as client:
                response = await client.post("/run", json=payload)
        except httpx.HTTPError as exc:
            log.warning("tool view %s.%s executor error: %s", view.tool, view.action, exc)
            raise RecordError("AD-TOOL-VIEW-UNAVAILABLE", "view executor is unavailable", 503) from None
        if response.status_code != 200:
            raise RecordError("AD-TOOL-VIEW-UNAVAILABLE", "view executor refused the call", 503)
        try:
            envelope = response.json()
        except ValueError:
            raise RecordError("AD-TOOL-VIEW-INVALID", "view executor returned invalid JSON", 502) from None
        if not isinstance(envelope, dict):
            raise RecordError("AD-TOOL-VIEW-INVALID", "view executor returned no result", 502)
        if not envelope.get("ok"):
            log.warning("tool view %s.%s failed: %s", view.tool, view.action,
                        str(envelope.get("error"))[:300])
            raise RecordError("AD-TOOL-VIEW-FAILED", "the tool view failed", 502)
        output = envelope.get("output")
        maximum = approved["limits"]["max_output_bytes"]
        if not isinstance(output, str) or len(output.encode()) > maximum:
            raise RecordError("AD-TOOL-VIEW-SIZE", "tool view output exceeds its byte limit", 502)
        try:
            parsed = json.loads(output)
            jsonschema.validate(parsed, approved["output_schema"])
        except (ValueError, jsonschema.ValidationError):
            raise RecordError("AD-TOOL-VIEW-INVALID", "tool view output fails its reviewed schema", 502) from None
        rows = parsed.get("rows")
        if rows is not None:
            if not isinstance(rows, list) or len(rows) > approved["limits"]["max_rows"]:
                raise RecordError("AD-TOOL-VIEW-ROWS", "tool view output exceeds its row limit", 502)
            result = {"rows": [{"values": row, "restricted": []} for row in rows[:limit]],
                      "next_cursor": None, "as_of": _as_of(utcnow()), "stale": False}
        elif "count" in parsed:
            result = {"count": parsed["count"], "as_of": _as_of(utcnow()), "stale": False}
        else:
            raise RecordError("AD-TOOL-VIEW-INVALID", "tool view output has no rows or count", 502)
        if cache_key is not None:
            await viewcache.put(session, cache_key, ctx, caller, view, result,
                                max_bytes=app_state.settings.app_data_view_cache_max_bytes)
        if capture_fields is not None:
            call = await session.get(AppDataToolCall, claim["jti"])
            await session.refresh(call)
            capture_fields.extend(call.scan_fields or [])
        return result
    finally:
        # A copy of the credential is dead as soon as the executor answers,
        # even on invalid output or a network failure.
        await credentials.revoke(session, claim["jti"])
        await session.commit()
