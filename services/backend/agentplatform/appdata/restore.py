"""Read-only reconciliation of restored App bindings against running code."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from agentplatform.appdata.models import AppDataApp
from agentplatform.appdata.records import published_rows, state_at
from agentplatform.db import ScheduledTask, utcnow
from agentplatform.operation_catalog import view_action


def disabled_bindings(rows: dict, registry) -> list[dict]:
    """Describe bindings that cannot execute; never rewrite approved facts."""
    disabled = []
    facts = {name: row.body for (kind, name), row in rows.items() if kind == "tool"}
    for tool, fact in sorted(facts.items()):
        info = registry.get(tool)
        manifest = info.manifest if info else None
        access = manifest.app_access if manifest else None
        if access is None:
            disabled.append({"kind": "tool", "name": tool, "reason": "tool unavailable"})
            continue
        for role, binding in sorted(fact.get("roles", {}).items()):
            if role not in access.roles or not set(binding.get("verbs", [])) <= set(access.verbs):
                disabled.append({"kind": "tool", "name": f"{tool}.{role}",
                                 "reason": "role or verb no longer declared"})
    for (kind, name), row in sorted(rows.items()):
        if kind != "view" or "tool" not in row.body:
            continue
        view = row.body
        tool = view["tool"]
        action = view["action"]
        info = registry.get(tool)
        manifest = info.manifest if info else None
        declaration = manifest.view_actions.get(action) if manifest else None
        catalog = view_action(tool, action)
        reason = None
        if catalog is None or declaration is None:
            reason = "reviewed action unavailable"
        elif (declaration.output_schema != catalog["output_schema"]
              or declaration.max_rows != catalog["limits"]["max_rows"]
              or declaration.max_bytes != catalog["limits"]["max_output_bytes"]):
            reason = "action differs from reviewed catalog"
        elif any(role not in (facts.get(tool) or {}).get("roles", {})
                 for role in view.get("sources", [])):
            reason = "approved source binding unavailable"
        if reason:
            disabled.append({"kind": "view", "name": name, "reason": reason})
    return disabled


async def for_app(session, app: AppDataApp, registry) -> list[dict]:
    rows = state_at(await published_rows(session, app.id), app.approved_version)
    return disabled_bindings(rows, registry)


async def report(session, registry, *, now: datetime | None = None) -> dict:
    now = now or utcnow()
    apps = (await session.execute(select(AppDataApp).order_by(AppDataApp.name,
                                                              AppDataApp.id))).scalars().all()
    entries = []
    for app in apps:
        entries.append({"app_id": app.id, "name": app.name, "status": app.status,
                        "disabled_bindings": await for_app(session, app, registry)})
    tasks = (await session.execute(select(ScheduledTask).order_by(ScheduledTask.agent,
                                                                   ScheduledTask.id))).scalars().all()
    watermarks = {}
    for task in tasks:
        item = watermarks.setdefault(task.agent, {"agent": task.agent,
                                                  "last_fired_at": None,
                                                  "overdue_scheduled": 0})
        if task.fired_at is not None:
            fired = task.fired_at if task.fired_at.tzinfo else task.fired_at.replace(
                tzinfo=timezone.utc)
            stamp = fired.isoformat()
            if item["last_fired_at"] is None or stamp > item["last_fired_at"]:
                item["last_fired_at"] = stamp
        run_at = task.run_at if task.run_at.tzinfo else task.run_at.replace(tzinfo=timezone.utc)
        if task.status == "scheduled" and run_at <= now:
            item["overdue_scheduled"] += 1
    return {"generated_at": now.isoformat(), "apps": entries,
            "disabled_count": sum(len(item["disabled_bindings"]) for item in entries),
            "task_watermarks": list(watermarks.values()),
            "outbox": {"status": "not_available", "message": "App outbox reconciliation is pending"}}
