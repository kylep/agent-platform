"""One-off: publish the ENG-8/ENG-10 Judgment bundle onto the live App.

The Judgment state App already exists (cut over 2026-10-04). This records
`judgment_migration.bundle()` as its next approved version, approved by Kyle
(2026-11-22, tickets ENG-8 and ENG-10): tool-only deletes, the Kyle-only
`kyle_confirmed` rule, `feedback.shared_as`, the add-feedback page action,
and the retrieval and review views. Every change is additive or narrowing.
No field is renamed or dropped, and the publish is refused if a stored
record would no longer fit. A dry run is the default. From the repo root,
after the API image carrying this bundle is live:

    F=services/backend/agentplatform/appdata/judgment_upgrade.py
    ssh pai 'sudo k3s kubectl exec -i -n agent-platform deploy/ap-api -c api -- python -' < $F
    ssh pai 'sudo k3s kubectl exec -i -n agent-platform deploy/ap-api -c api -- python - --apply' < $F

A second `--apply` finds nothing to change. Delete this file once it has run.
"""
from __future__ import annotations

import argparse
import asyncio
import json

from agentplatform.appdata import lifecycle as L
from agentplatform.appdata.judgment_migration import bundle
from agentplatform.appdata.models import AppDataApp
from agentplatform.appdata.page_tool_actions import REVIEWED

APP_ID = "94ebba1ddacb4b5da98d071650b4f265"
KINDS = (("collection", "collections"), ("view", "views"), ("page", "pages"),
         ("tool", "app_tools"))


def _target() -> dict[tuple[str, str], dict]:
    definitions = bundle()
    return {(kind, body[kind]): body for kind, plural in KINDS for body in definitions[plural]}


def _diff(key: tuple[str, str], old: dict | None, new: dict) -> dict:
    entry = {"kind": key[0], "name": key[1], "change": "added" if old is None else "changed"}
    if old is not None and key[0] == "collection":
        fields_old, fields_new = old.get("fields", {}), new.get("fields", {})
        entry["fields_added"] = sorted(set(fields_new) - set(fields_old))
        entry["keys_changed"] = sorted(k for k in set(old) | set(new)
                                       if k != "fields" and old.get(k) != new.get(k))
    return entry


async def upgrade(session, *, apply: bool = False, app_id: str = APP_ID) -> dict:
    """Check, then (with `apply`) publish. Doesn't commit."""
    if "judgment_add_feedback" not in REVIEWED:
        raise RuntimeError("this API image predates the ENG-10 page actions; deploy it first")
    app = await session.get(AppDataApp, app_id)
    if (app is None or app.name != "judgment" or app.owner_id != "kai"
            or app.status != "active"):
        raise RuntimeError(f"no active Kai-owned Judgment App {app_id}")
    rows = await L._rows(session, app.id)
    current = L._bodies(L._approved(rows, app))
    target = _target()
    dropped = sorted(set(current) - set(target))
    if dropped:
        raise RuntimeError(f"the live App has definitions this bundle would drop: {dropped}")
    async with L._consistency_lock(session, app.id, current, target):
        a = await L._assess(session, app, current, target)
        if a.errors or a.record_issues or a.data_dropping:
            raise RuntimeError(json.dumps({"errors": a.errors, "record_issues": a.record_issues,
                                           "data_dropping": a.data_dropping}, default=str))
        changes = {key: body for key, body in target.items()
                   if current.get(key) != L._settled_body(a, key, body)}
        report = {"app_id": app.id, "approved_version": app.approved_version,
                  "changes": [_diff(key, current.get(key), L._settled_body(a, key, body))
                              for key, body in sorted(changes.items())],
                  "authority_added": a.delta["added"],
                  "authority_removed": a.delta["removed"], "reindex": a.reindex}
        if not changes:
            return {**report, "up_to_date": True}
        if not apply:
            return {**report, "dry_run": True}
        version, entries = await L._publish_core(
            session, app, a, expected=app.approved_version, changes=changes,
            author="recipe:judgment-upgrade", run_id=None, approved_by="kyle",
            reason="Kyle approved ENG-8 and ENG-10 on 2026-11-22")
        return {**report, "published_version": version, "published": entries}


async def main(apply: bool) -> dict:
    from agentplatform.config import Settings
    from agentplatform.db import make_engine, make_session_factory

    engine = make_engine(Settings().db_url)
    try:
        async with make_session_factory(engine)() as session:
            result = await upgrade(session, apply=apply)
            await (session.commit() if apply else session.rollback())
            return result
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="publish the new version")
    print(json.dumps(asyncio.run(main(parser.parse_args().apply)), indent=1,
                     sort_keys=True, default=str))
