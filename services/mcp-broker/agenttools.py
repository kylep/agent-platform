"""`agents_edit` / `agents_grant` — the broker tools that write agent
definitions (docs/design/15).

WHY THESE LIVE IN THE BROKER AND NOT IN `tools/<name>/`
-------------------------------------------------------
A custom tool runs in the tool-executor, which by design never receives the
caller's credentials — only the identity the broker verified. It could reach
the platform API only with a shared key of its own, and then
`agent_versions.changed_by` would name that key instead of the agent, and
every holder would inherit the key's authority rather than its own grant.
A broker tool forwards the RUN's own bearer, so `agent_write_scope` sees the
agent itself: attribution and authorization are both the auth chain's answer,
never an argument. That is the whole point of the split these two tools
implement, so broker-resident is the only correct home for them.

They are still NOT in `agentspec.PLATFORM_MCP_TOOLS`: that list is the
annotator rung of the design-12 ladder, and a definition-writing grant must not
silently widen the rest of the API. The holder stays on `tools`, and
`agent_read_access` is what lets it read back what it may write.

SHAPE OF THE MODULE
-------------------
Everything here is transport-free: each entry point takes a
`call(method, path, json=None, params=None)` coroutine returning an
httpx-shaped response, and returns the plain string the model sees. `broker.py`
supplies the real `call` (bearer forwarded) and wraps these in `@mcp.tool`;
the tests drive them against the real ASGI app with a real agent bearer, in
`services/backend/tests/test_agent_write_tools.py` — the one place where the
API and these clients can be exercised as the single mechanism they are.

Both writers are READ-MODIFY-WRITE. `PUT /api/agents/{name}` replaces the
whole definition, so sending only the fields you care about would reset every
other one — and, worse, would read as a grant change and be refused. Reading
first means `agents_edit` writes back the grants exactly as they stood (no
grant change, no 403) and `agents_grant` writes back the prose exactly as it
stood (no edit change, no 403). Each tool's own half is the only thing that
ever differs.
"""
from __future__ import annotations

import json

# Mirrors of the API's field split (`api/agents.py`). Two services, so they are
# spelled out twice; a backend test asserts the two copies stay identical, on
# the theory that a silent drift here would either block legal edits or send
# doomed payloads.
GRANT_LIST_FIELDS: tuple[str, ...] = ("harness_tools", "platform_tools",
                                      "skills", "secrets")
GRANT_FIELDS: tuple[str, ...] = GRANT_LIST_FIELDS + ("can_invoke", "role")
API_EDIT_FIELDS: tuple[str, ...] = (
    "prompt", "description", "model", "system", "concurrency",
    "timeout_seconds", "result_topic", "transcript_retention_days",
    "entrypoints", "enabled",
)
# `system` is enforced admin-only by the API (it protects an agent from
# deletion and gets it platform credentials injected), so no tool call can ever
# land it. Refusing it here by name — rather than forwarding it into a 403 the
# model did not see coming — keeps the tool's advertised surface and its real
# surface the same thing.
ADMIN_ONLY_FIELDS: tuple[str, ...] = ("system",)
EDITABLE_FIELDS: tuple[str, ...] = tuple(f for f in API_EDIT_FIELDS
                                         if f not in ADMIN_ONLY_FIELDS)

# The compact projection `action="list"` returns. A listing of full definitions
# is mostly prompts — kilobytes of context to answer "which agents exist?" —
# so the listing names them and `action="get"` fetches the one you want.
_LIST_ALWAYS = ("name", "description", "role", "enabled")
# Carried only when set, because "nothing wrong with it" is the common case and
# an empty key per agent per row is noise. `enabled: false` is NOT one of these
# — a switched-off agent is exactly what a caller is looking for.
_LIST_IF_SET = ("system", "quarantined", "error", "blocked_reason", "schedule")

EDIT_ACTIONS = ("list", "get", "create", "update", "delete")
GRANT_ACTIONS = ("get", "set_grants", "add_grant", "remove_grant")


class ToolError(Exception):
    """A message for the model. Raised for anything the caller can fix by
    calling differently; the caller renders it as `error: ...`."""


# --- the broker-side gate -----------------------------------------------------

def guard(ident: dict | None, tool: str) -> tuple[str | None, str]:
    """`(error for the model or None, audit decision)` for one call.

    The platform API is the REAL authorization — it re-derives the grant from
    the presented token on every write, so a broker bug cannot hand anyone a
    definition they may not write. This gate exists so an ungranted call is
    refused where it was made, with an answer the model can act on, and so the
    attempt is audited as a denial instead of vanishing into an API 403.

    `ident["tools"]` is /api/whoami's answer: the frozen run-JWT grant set if
    the run has one, else the agent's current row. `None` means the caller is
    not an agent at all (an admin session driving the broker), which the
    per-endpoint authorization already covers.
    """
    if ident is None:
        return "unauthenticated (no valid platform token)", "deny:unauthenticated"
    declared = ident.get("tools")
    if declared is not None and f"mcp__platform__{tool}" not in declared:
        return f"your agent does not declare the {tool} tool", "deny:undeclared"
    return None, "allow"


# --- talking to the platform API ---------------------------------------------

def _detail(resp) -> str:
    try:
        body = resp.json()
    except Exception:
        return (resp.text or "")[:500]
    if isinstance(body, dict) and "detail" in body:
        d = body["detail"]
        return d if isinstance(d, str) else json.dumps(d)[:500]
    return (resp.text or "")[:500]


def _raise_for_status(resp, what: str) -> None:
    """Turn a refusal into the model's error text. The API's own detail is
    passed through verbatim — a 403 from `agent_write_scope` names the exact
    fields that needed the other tool, which is the single most useful thing
    the model can be told, so nothing is paraphrased over it."""
    if resp.status_code < 400:
        return
    if resp.status_code in (401, 403):
        raise ToolError(f"the platform API refused this ({resp.status_code}): "
                        f"{_detail(resp)}")
    if resp.status_code == 404:
        raise ToolError(f"{what}: not found ({_detail(resp)})")
    raise ToolError(f"the platform API returned {resp.status_code} for {what}: "
                    f"{_detail(resp)}")


async def _get_def(call, name: str) -> dict:
    resp = await call("GET", f"/api/agents/{name}")
    _raise_for_status(resp, f"reading agent {name!r}")
    return resp.json()


async def _put_def(call, name: str, overlay_for, *, what: str) -> tuple[dict, bool]:
    """Read the definition, lay an overlay over it, and PUT the whole thing.
    Returns `(definition, wrote)`.

    `overlay_for(current) -> dict | None` is recomputed FROM EVERY READ, and
    `None` means "nothing to do" (no PUT, no change-log version). Passing a
    function rather than a finished overlay is the whole point: `add_grant` and
    `remove_grant` derive their new list from the list they read, and a
    derived overlay computed once and replayed is a lost update waiting to
    happen — the retry below would PUT a list assembled before the winning
    writer's grant existed, deleting it and logging the deletion as deliberate.

    A 409 is a lost write race (two writers, one version number — see the API's
    `_conflict_as_409`), so it is retried ONCE from a fresh read: the retry
    re-reads *and re-derives*, because the definition being merged into is
    exactly the thing that moved. A second 409 is reported rather than looped
    on.
    """
    for attempt in (1, 2):
        current = await _get_def(call, name)
        overlay = overlay_for(current)
        if overlay is None:
            return current, False
        resp = await call("PUT", f"/api/agents/{name}", json={**current, **overlay})
        if resp.status_code == 409 and attempt == 1:
            continue
        _raise_for_status(resp, what)
        return resp.json(), True
    raise AssertionError("unreachable")


def _constant(overlay: dict):
    """An overlay that does not depend on what was read — the plain
    "set these fields" write."""
    return lambda _current: overlay


# --- argument handling --------------------------------------------------------

def _need_name(args: dict, action: str) -> str:
    name = (args.get("name") or "").strip()
    if not name:
        raise ToolError(f"action={action!r} requires the agent's name")
    return name


def _definition(args: dict, *, action: str) -> dict:
    """The caller's editorial fields, validated against what `agents_edit` may
    write. Grant fields are refused BY NAME rather than dropped: silently
    stripping them would let the model believe it had granted something."""
    raw = args.get("definition")
    if raw is None:
        raise ToolError(f"action={action!r} requires a definition object")
    if not isinstance(raw, dict):
        raise ToolError("definition must be an object of definition fields")
    offered = [f for f in GRANT_FIELDS if f in raw]
    if offered:
        raise ToolError(
            f"agents_edit cannot change grants ({', '.join(offered)}) — those "
            "are what an agent may DO, and changing them needs the "
            "agents_grant tool. Remove them and edit the rest.")
    reserved = [f for f in ADMIN_ONLY_FIELDS if f in raw]
    if reserved:
        raise ToolError(
            f"{', '.join(reserved)} is admin-only and no tool can set it — "
            "the system flag protects an agent from deletion and gets it "
            "platform credentials injected. Remove it and edit the rest.")
    unknown = [k for k in raw if k not in EDITABLE_FIELDS and k != "name"]
    if unknown:
        raise ToolError(
            f"not agent-definition fields: {', '.join(sorted(unknown))}. "
            f"agents_edit writes: {', '.join(EDITABLE_FIELDS)}. (Attribution is "
            "not an argument — the change log records the calling agent, from "
            "its verified token.)")
    # `name` is set by the path/create argument, never by the payload — the API
    # ignores it on PUT, and letting it through on create would allow the body
    # to disagree with the argument the audit trail recorded.
    return {k: v for k, v in raw.items() if k != "name"}


def _values(args: dict) -> list[str]:
    vals = args.get("values")
    if isinstance(vals, str):
        vals = [vals]
    if not isinstance(vals, list) or not vals or not all(isinstance(v, str) for v in vals):
        raise ToolError("values must be a non-empty list of names")
    return vals


def _grant_field(args: dict) -> str:
    field = args.get("field")
    if field not in GRANT_LIST_FIELDS:
        raise ToolError(f"field must be one of {', '.join(GRANT_LIST_FIELDS)}")
    return field


# --- agents_edit --------------------------------------------------------------

async def agents_edit(call, args: dict) -> str:
    """Dispatch one `agents_edit` call. Returns the model-visible string."""
    try:
        return await _agents_edit(call, args)
    except ToolError as e:
        return f"error: {e}"


async def _agents_edit(call, args: dict) -> str:
    action = args.get("action") or ""
    if action == "list":
        resp = await call("GET", "/api/agents")
        _raise_for_status(resp, "listing agents")
        rows = []
        for a in resp.json():
            row = {f: a.get(f) for f in _LIST_ALWAYS}
            row.update({f: a[f] for f in _LIST_IF_SET if a.get(f)})
            rows.append(row)
        return json.dumps(rows)

    if action == "get":
        return json.dumps(await _get_def(call, _need_name(args, action)))

    if action == "create":
        name = _need_name(args, action)
        body = {**_definition(args, action=action), "name": name}
        resp = await call("POST", "/api/agents", json=body)
        _raise_for_status(resp, f"creating agent {name!r}")
        return json.dumps(resp.json())

    if action == "update":
        name = _need_name(args, action)
        overlay = _definition(args, action=action)
        if not overlay:
            raise ToolError("definition is empty — nothing to update")
        out, _ = await _put_def(call, name, _constant(overlay),
                                what=f"updating agent {name!r}")
        return json.dumps(out)

    if action == "delete":
        name = _need_name(args, action)
        resp = await call("DELETE", f"/api/agents/{name}")
        _raise_for_status(resp, f"deleting agent {name!r}")
        return json.dumps({"deleted": name, "definition": resp.json()})

    raise ToolError(f"action must be one of {'|'.join(EDIT_ACTIONS)}")


# --- agents_grant -------------------------------------------------------------

async def agents_grant(call, args: dict) -> str:
    try:
        return await _agents_grant(call, args)
    except ToolError as e:
        return f"error: {e}"


def _grants_of(definition: dict) -> dict:
    return {f: definition.get(f, [] if f in GRANT_LIST_FIELDS else False)
            for f in (*GRANT_LIST_FIELDS, "can_invoke")}


async def _agents_grant(call, args: dict) -> str:
    action = args.get("action") or ""
    name = _need_name(args, action) if action in GRANT_ACTIONS else ""

    if action == "get":
        return json.dumps({"name": name,
                           **_grants_of(await _get_def(call, name))})

    if action == "set_grants":
        overlay = {}
        for f in GRANT_LIST_FIELDS:
            if args.get(f) is not None:
                v = args[f]
                if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                    raise ToolError(f"{f} must be a list of names")
                overlay[f] = v
        if args.get("can_invoke") is not None:
            overlay["can_invoke"] = bool(args["can_invoke"])
        if not overlay:
            raise ToolError(
                "set_grants needs at least one of "
                f"{', '.join((*GRANT_LIST_FIELDS, 'can_invoke'))}. Omitted "
                "lists are left alone, so this is a no-op as written.")
        out, _ = await _put_def(call, name, _constant(overlay),
                                what=f"setting grants on {name!r}")
        return json.dumps(_grants_of(out))

    if action in ("add_grant", "remove_grant"):
        field, values = _grant_field(args), _values(args)

        def overlay_for(current: dict) -> dict | None:
            have = list(current.get(field) or [])
            if action == "add_grant":
                # Order-preserving union: the row is a list, and churning its
                # order would file a version that changed nothing that matters.
                merged = have + [v for v in values if v not in have]
            else:
                merged = [v for v in have if v not in values]
            # Re-derived on the retry's read too, so a concurrent grant made
            # between the two attempts is added to, never overwritten.
            return None if merged == have else {field: merged}

        out, wrote = await _put_def(call, name, overlay_for,
                                    what=f"{action} on {name!r}")
        return json.dumps({"name": name, field: out.get(field), "changed": wrote})

    raise ToolError(f"action must be one of {'|'.join(GRANT_ACTIONS)}")
