"""Immutable, reviewed Live App admission contracts.

The checked-in JSON is generated from core broker Tools, custom tool manifests
and the small set of human adapters. It describes unavailable operations too:
new branches default to unknown effects and cannot be published in a page.
"""
from __future__ import annotations

import json
from pathlib import Path

_FILE = Path(__file__).with_name("live_operation_catalog.json")
_DATA = json.loads(_FILE.read_text())
assert _DATA["schema_version"] == 1
OPERATIONS = {item["id"]: item for item in _DATA["operations"]}


def admitted(operation_id: str) -> bool:
    item = OPERATIONS.get(operation_id)
    return bool(item and item["view_eligible"] and "unknown" not in item["effects"])


def listed(*, eligible_only: bool = False) -> list[dict]:
    rows = [dict(item) for item in _DATA["operations"]]
    if eligible_only:
        rows = [item for item in rows if admitted(item["id"])]
    return rows
