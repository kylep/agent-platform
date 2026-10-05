"""The market copy must preserve sparse fields and binary pinned datasets."""
import base64
from datetime import datetime, timezone

import pytest

from agentplatform.appdata.access import Access, Caller, RecordError
from agentplatform.appdata.definitions import validate_app
from agentplatform.appdata.stockmarket_migration import bundle, convert


def test_market_bundle_is_publishable():
    parsed = validate_app(bundle())
    assert len(parsed.collections) == 10
    assert {"home", "backtests", "backtest"} == set(parsed.pages)
    assert parsed.app_tools["backtest"].roles["bars"].verbs == ["read"]


def test_pai_can_read_delivery_briefs_without_market_write_access():
    parsed = validate_app(bundle())
    pai = Caller("agent:pai")
    briefs = Access(parsed.collections["briefs"], pai, "agent:stockmarket-data")
    briefs.require_rows()
    assert all(briefs.can_read(field) for field in ("day", "body", "indexes_json",
                                                   "source_created_at"))
    for verb in ("create", "update", "delete"):
        with pytest.raises(RecordError, match="AD-FORBIDDEN"):
            briefs.require_verb(verb)
    for name, collection in parsed.collections.items():
        if name != "briefs":
            with pytest.raises(RecordError, match="AD-FORBIDDEN"):
                Access(collection, pai, "agent:stockmarket-data").require_rows()


def test_pinned_dataset_parts_roundtrip_exact_bytes():
    payload = bytes(range(256)) * 200
    rows = convert("backtest_datasets", {
        "sha": "a" * 64, "rows_gz": payload, "symbols": ["SPY"],
        "day_from": "2020-01-01", "day_to": "2021-01-01",
        "created_at": datetime.now(timezone.utc),
    })
    assert len(rows) > 1
    assert [r.doc["part"] for r in rows] == list(range(len(rows)))
    assert base64.b64decode("".join(r.doc["data_b64"] for r in rows)) == payload
    assert all(len(r.doc["data_b64"]) <= 12000 for r in rows)


def test_bar_identity_and_nullable_corporate_actions():
    raw = {"symbol": "SPY", "day": "2024-01-02", "open": 40.1,
           "high": 41.0, "low": 39.0, "close": 40.5, "volume": 1,
           "close_split_adj": 40.5, "adj_close": 40.5,
           "dividend": 0.0, "split_ratio": None}
    one = convert("bars", raw)[0]
    two = convert("bars", dict(raw, close=42.0))[0]
    assert one.id == two.id
    assert one.doc["dividend"] == 0.0
    assert "split_ratio" not in one.doc


def test_large_metrics_keep_every_byte_in_ordered_parts():
    metrics = {"final_value": 123.5, "positions": [f"lot-{n}" for n in range(5000)]}
    converted = convert("backtest_results", {
        "experiment_id": "a" * 32, "strategy_id": "steady",
        "label": "Steady", "metrics": metrics,
    })
    assert converted[0].collection == "results"
    assert len(converted[0].doc["metrics_json"]) < 16000
    parts = converted[1:]
    assert [p.doc["part"] for p in parts] == list(range(len(parts)))
    import json
    assert json.loads("".join(p.doc["data"] for p in parts)) == metrics
