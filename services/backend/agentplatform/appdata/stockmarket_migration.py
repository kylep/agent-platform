"""Stockmarket state-App definition and lossless legacy-row conversion.

The migration keeps each daily bar and backtest event addressable. Large
datasets are represented by bounded base64 chunks, since an App record has a
16 KiB text-field ceiling. The original schema stays available for rollback.
"""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

TABLES = ("symbols", "bars", "watchlist", "briefs", "backtest_datasets",
          "backtest_experiments", "backtest_results", "backtest_series",
          "backtest_events")
READ = ["owner", "kyle", "agent:stockmarket-data", "agent:stockmarket"]
ACCESS = {"read": READ, "create": ["owner"], "update": ["owner"],
          "delete": ["kyle"]}


def record_id(collection: str, key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"stockmarket:{collection}:{key}").hex


def field(kind: str, *, required: bool = False, max: int | None = None) -> dict:
    out = {"type": kind}
    if required:
        out["required"] = True
    if max is not None:
        out["max"] = max
    return out


def _collection(name: str, fields: dict, unique: list[str], indexes: list[str],
                writers: list[str]) -> dict:
    access = dict(ACCESS)
    if "tool:stockmarket" in writers:
        access.update(create=["owner", "agent:stockmarket"],
                      update=["owner", "agent:stockmarket"])
    writer_rule = {"create": writers, "update": writers}
    if name == "watchlist":
        access["delete"] = ["owner", "agent:stockmarket"]
        writer_rule["delete"] = ["tool:stockmarket"]
    return {"collection": name, "description": f"Stockmarket {name.replace('_', ' ')}.",
            "fields": fields, "access": access,
            "writers": writer_rule,
            "rules": [{"kind": "unique", "fields": unique}], "indexed": indexes}


def bundle() -> dict:
    f = field
    collections = [
        _collection("symbols", {
            "symbol": f("string", required=True, max=12),
            "label": f("string", max=128), "kind": f("string", max=16),
            "status": f("string", max=16), "error": f("text", max=1000),
            "currency": f("string", max=3), "last_synced_at": f("datetime"),
            "added_at": f("datetime"),
        }, ["symbol"], ["symbol", "kind"], ["tool:prices", "tool:stockmarket"]),
        _collection("bars", {
            "symbol": f("string", required=True, max=12),
            "day": f("date", required=True),
            "open": f("number"), "high": f("number"), "low": f("number"),
            "close": f("number", required=True), "volume": f("int"),
            "close_split_adj": f("number"), "adj_close": f("number"),
            "dividend": f("number"), "split_ratio": f("number"),
        }, ["symbol", "day"], ["symbol", "day"], ["tool:prices"]),
        _collection("watchlist", {
            "user": f("string", required=True, max=128),
            "symbol": f("string", required=True, max=12),
            "added_at": f("datetime"),
        }, ["user", "symbol"], ["user", "symbol"], ["tool:stockmarket"]),
        _collection("briefs", {
            "day": f("date", required=True), "body": f("text", max=16000),
            "tags_json": f("text", max=16000),
            "indexes_json": f("text", max=16000),
            "movers_json": f("text", max=16000),
            "run_id": f("string", max=32), "source_created_at": f("datetime"),
        }, ["day"], ["day"], ["tool:stockmarket"]),
        _collection("datasets", {
            "sha": f("string", required=True, max=64),
            "part": f("int", required=True),
            "data_b64": f("text", required=True, max=16000),
            "symbols_json": f("text", max=16000),
            "day_from": f("date"), "day_to": f("date"),
            "source_created_at": f("datetime"),
        }, ["sha", "part"], ["sha", "part"], ["tool:backtest"]),
        _collection("experiments", {
            "experiment_id": f("string", required=True, max=32),
            "name": f("string", max=200), "spec_json": f("text", max=16000),
            "description": f("text", max=16000),
            "assumed_json": f("text", max=16000),
            "caveats_json": f("text", max=16000),
            "exclusions_json": f("text", max=16000),
            "dataset_sha": f("string", required=True, max=64),
            "engine_version": f("string", max=20),
            "caller": f("string", max=128), "run_id": f("string", max=32),
            "report_id": f("string", max=64), "source_created_at": f("datetime"),
        }, ["experiment_id"], ["experiment_id", "source_created_at"], ["tool:backtest"]),
        _collection("results", {
            "experiment_id": f("string", required=True, max=32),
            "strategy_id": f("string", required=True, max=64),
            "label": f("string", max=200), "metrics_json": f("text", max=16000),
        }, ["experiment_id", "strategy_id"], ["experiment_id", "strategy_id"],
            ["tool:backtest"]),
        _collection("metrics_parts", {
            "experiment_id": f("string", required=True, max=32),
            "strategy_id": f("string", required=True, max=64),
            "part": f("int", required=True),
            "data": f("text", required=True, max=16000),
        }, ["experiment_id", "strategy_id", "part"],
            ["experiment_id", "strategy_id"], ["tool:backtest"]),
        _collection("series", {
            "experiment_id": f("string", required=True, max=32),
            "strategy_id": f("string", required=True, max=64),
            "day": f("date", required=True), "value": f("number", required=True),
            "contributed": f("number"),
        }, ["experiment_id", "strategy_id", "day"], ["experiment_id", "day"],
            ["tool:backtest"]),
        _collection("events", {
            "legacy_id": f("int", required=True),
            "experiment_id": f("string", required=True, max=32),
            "strategy_id": f("string", required=True, max=64),
            "day": f("date", required=True), "kind": f("string", required=True, max=32),
            "symbol": f("string", max=12), "detail_json": f("text", max=16000),
        }, ["legacy_id"], ["experiment_id", "day"], ["tool:backtest"]),
    ]
    views = [
        {"view": "symbol_count", "collection": "symbols",
         "filter": [{"field": "kind", "op": "in", "value": ["index", "watch"]}],
         "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "bar_count", "collection": "bars",
         "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "experiment_count", "collection": "experiments",
         "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "symbols", "collection": "symbols",
         "filter": [{"field": "kind", "op": "in", "value": ["index", "watch"]}],
         "sort": [{"field": "symbol", "dir": "asc"}], "paging": True, "limit": 100},
        {"view": "all_symbols", "collection": "symbols",
         "sort": [{"field": "symbol", "dir": "asc"}], "paging": True, "limit": 100},
        {"view": "watchlist", "collection": "watchlist",
         "params": {"user": {"type": "string", "required": True}},
         "filter": [{"field": "user", "op": "eq", "value": {"param": "user"}}],
         "paging": True, "limit": 100},
        {"view": "bars_for_symbol", "collection": "bars",
         "params": {"symbol": {"type": "string", "required": True}},
         "filter": [{"field": "symbol", "op": "eq", "value": {"param": "symbol"}}],
         "sort": [{"field": "day", "dir": "asc"}], "paging": True, "limit": 200},
        {"view": "bar_on_day", "collection": "bars",
         "params": {"symbol": {"type": "string", "required": True},
                    "day": {"type": "date", "required": True}},
         "filter": [{"field": "symbol", "op": "eq", "value": {"param": "symbol"}},
                    {"field": "day", "op": "eq", "value": {"param": "day"}}],
         "limit": 1},
        {"view": "bars_before_day", "collection": "bars",
         "params": {"symbol": {"type": "string", "required": True},
                    "day": {"type": "date", "required": True}},
         "filter": [{"field": "symbol", "op": "eq", "value": {"param": "symbol"}},
                    {"field": "day", "op": "lt", "value": {"param": "day"}}],
         "limit": 1},
        {"view": "briefs_recent", "collection": "briefs",
         "sort": [{"field": "day", "dir": "desc"}], "paging": True, "limit": 100},
        {"view": "experiments_recent", "collection": "experiments",
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True, "limit": 100},
        {"view": "experiment_by_id", "collection": "experiments",
         "params": {"experiment_id": {"type": "string", "required": True}},
         "filter": [{"field": "experiment_id", "op": "eq",
                     "value": {"param": "experiment_id"}}], "limit": 1},
        {"view": "results_for_experiment", "collection": "results",
         "params": {"experiment_id": {"type": "string", "required": True}},
         "filter": [{"field": "experiment_id", "op": "eq",
                     "value": {"param": "experiment_id"}}],
         "paging": True, "limit": 100},
        {"view": "metrics_for_result", "collection": "metrics_parts",
         "params": {"experiment_id": {"type": "string", "required": True},
                    "strategy_id": {"type": "string", "required": True}},
         "filter": [{"field": "experiment_id", "op": "eq",
                     "value": {"param": "experiment_id"}},
                    {"field": "strategy_id", "op": "eq",
                     "value": {"param": "strategy_id"}}],
         "sort": [{"field": "part", "dir": "asc"}], "paging": True,
         "limit": 100},
        {"view": "series_for_experiment", "collection": "series",
         "params": {"experiment_id": {"type": "string", "required": True}},
         "filter": [{"field": "experiment_id", "op": "eq",
                     "value": {"param": "experiment_id"}}],
         "sort": [{"field": "day", "dir": "asc"}], "paging": True, "limit": 200},
        {"view": "events_for_experiment", "collection": "events",
         "params": {"experiment_id": {"type": "string", "required": True}},
         "filter": [{"field": "experiment_id", "op": "eq",
                     "value": {"param": "experiment_id"}}],
         "sort": [{"field": "day", "dir": "desc"}], "paging": True, "limit": 100},
        {"view": "datasets_for_sha", "collection": "datasets",
         "params": {"sha": {"type": "string", "required": True}},
         "filter": [{"field": "sha", "op": "eq", "value": {"param": "sha"}}],
         "sort": [{"field": "part", "dir": "asc"}], "paging": True, "limit": 100},
    ]
    pages = [
        {"page": "home", "title": "Markets", "blocks": [
            {"kind": "metric", "view": "symbol_count", "label": "Tracked symbols"},
            {"kind": "metric", "view": "bar_count", "label": "Daily bars"},
            {"kind": "metric", "view": "experiment_count", "label": "Backtests"},
            {"kind": "table", "view": "symbols", "title": "Watch and indexes",
             "columns": [{"field": "symbol"}, {"field": "label"},
                         {"field": "status"}, {"field": "currency"}]},
            {"kind": "table", "view": "briefs_recent", "title": "Market briefs",
             "columns": [{"field": "day", "format": "date"}, {"field": "body"}]},
        ]},
        {"page": "backtests", "title": "Backtests", "blocks": [
            {"kind": "table", "view": "experiments_recent", "columns": [
                {"field": "name"}, {"field": "engine_version"},
                {"field": "created_at", "format": "date"}],
             "row_link": {"page": "backtest", "param": "experiment_id",
                          "field": "experiment_id"}}]},
        {"page": "backtest", "title": "Backtest", "params": {
            "experiment_id": {"type": "string", "required": True}}, "blocks": [
            {"kind": "table", "view": "results_for_experiment", "params": {
                "experiment_id": {"page_param": "experiment_id"}},
             "columns": [{"field": "strategy_id"}, {"field": "label"},
                         {"field": "metrics_json"}]},
            {"kind": "chart", "view": "series_for_experiment", "params": {
                "experiment_id": {"page_param": "experiment_id"}},
             "title": "Portfolio value", "x": "day", "y": "value"},
            {"kind": "table", "view": "events_for_experiment", "params": {
                "experiment_id": {"page_param": "experiment_id"}},
             "columns": [{"field": "day", "format": "date"},
                         {"field": "kind"}, {"field": "symbol"}]},
        ]},
    ]
    roles = {c["collection"]: {"collection": c["collection"],
              "verbs": ["read", "create", "update"]} for c in collections}
    tools = [{"tool": name, "roles": {
                k: v for k, v in roles.items() if name in (
                    ("prices", "stockmarket") if k == "symbols" else
                    ("prices",) if k == "bars" else
                    ("stockmarket",) if k in ("watchlist", "briefs") else
                    ("backtest",))}} for name in ("prices", "stockmarket", "backtest")]
    tools[1]["roles"]["watchlist"]["verbs"].append("delete")
    tools[2]["roles"]["symbols"] = {"collection": "symbols", "verbs": ["read"]}
    tools[2]["roles"]["bars"] = {"collection": "bars", "verbs": ["read"]}
    return {"collections": collections, "views": views, "pages": pages,
            "app_tools": tools}


@dataclass(frozen=True)
class ImportRecord:
    collection: str
    id: str
    doc: dict
    created_at: datetime


def _iso(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _json(value):
    return json.dumps(value if value is not None else [], separators=(",", ":"),
                      sort_keys=True, default=str)


def convert(table: str, row: dict) -> list[ImportRecord]:
    fallback = datetime(1970, 1, 1, tzinfo=timezone.utc)
    timestamp = row.get("created_at") or row.get("added_at") or fallback
    if table == "backtest_datasets":
        encoded = base64.b64encode(row["rows_gz"]).decode("ascii")
        chunks = [encoded[i:i + 12000] for i in range(0, len(encoded), 12000)]
        if not chunks:
            raise ValueError("empty backtest dataset")
        return [ImportRecord("datasets", record_id("datasets", f"{row['sha']}:{part}"), {
            "sha": row["sha"], "part": part, "data_b64": chunk,
            "symbols_json": _json(row["symbols"]),
            "day_from": _iso(row["day_from"]), "day_to": _iso(row["day_to"]),
            "source_created_at": _iso(row.get("created_at")),
        }, timestamp) for part, chunk in enumerate(chunks)]
    mapping = {
        "symbols": ("symbols", lambda r: r["symbol"],
                    ("symbol", "label", "kind", "status", "error", "currency",
                     "last_synced_at", "added_at"), ()),
        "bars": ("bars", lambda r: f"{r['symbol']}:{r['day']}",
                 ("symbol", "day", "open", "high", "low", "close", "volume",
                  "close_split_adj", "adj_close", "dividend", "split_ratio"), ()),
        "watchlist": ("watchlist", lambda r: f"{r['user']}:{r['symbol']}",
                      ("user", "symbol", "added_at"), ()),
        "briefs": ("briefs", lambda r: r["day"],
                   ("day", "body", "run_id", "created_at"),
                   ("tags", "indexes", "movers")),
        "backtest_experiments": ("experiments", lambda r: r["id"],
                                 ("name", "description", "dataset_sha", "engine_version",
                                  "caller", "run_id", "report_id", "created_at"),
                                 ("spec", "assumed", "caveats", "exclusions")),
        "backtest_results": ("results", lambda r: f"{r['experiment_id']}:{r['strategy_id']}",
                             ("experiment_id", "strategy_id", "label"), ("metrics",)),
        "backtest_series": ("series", lambda r: f"{r['experiment_id']}:{r['strategy_id']}:{r['day']}",
                            ("experiment_id", "strategy_id", "day", "value", "contributed"), ()),
        "backtest_events": ("events", lambda r: str(r["id"]),
                            ("experiment_id", "strategy_id", "day", "kind", "symbol"),
                            ("detail",)),
    }
    collection, key, scalars, json_fields = mapping[table]
    doc = {name: _iso(row[name]) for name in scalars if row.get(name) is not None}
    if "created_at" in doc:
        doc["source_created_at"] = doc.pop("created_at")
    doc.update({name + "_json": _json(row[name]) for name in json_fields})
    parts = []
    if table == "backtest_results" and len(doc["metrics_json"]) > 16000:
        full = doc["metrics_json"]
        preview = {k: v for k, v in row["metrics"].items()
                   if isinstance(v, (str, int, float, bool, type(None)))}
        doc["metrics_json"] = _json(preview)
        if len(doc["metrics_json"]) > 16000:
            raise ValueError("backtest metrics preview exceeds 16 KiB")
        for part, start in enumerate(range(0, len(full), 12000)):
            parts.append(ImportRecord("metrics_parts", record_id(
                "metrics_parts", f"{row['experiment_id']}:{row['strategy_id']}:{part}"), {
                    "experiment_id": row["experiment_id"],
                    "strategy_id": row["strategy_id"], "part": part,
                    "data": full[start:start + 12000]}, timestamp))
    if table == "backtest_experiments":
        doc["experiment_id"] = row["id"]
    elif table == "backtest_events":
        doc["legacy_id"] = row["id"]
    for name, value in doc.items():
        if isinstance(value, str) and len(value) > 16000:
            raise ValueError(f"{table}.{name} exceeds a state field's 16 KiB limit")
    return [ImportRecord(collection, record_id(collection, str(key(row))), doc, timestamp),
            *parts]
