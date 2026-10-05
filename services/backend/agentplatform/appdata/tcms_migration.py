"""Reviewed TCMS state-App definition and lossless legacy snapshot conversion.

The old tables stay in place until the writer and reader cutover is verified.
This module contains no live credentials or generated data.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text


TABLES = ("cases", "test_runs", "results", "coverage_snapshots")
READ = ["owner", "kyle", "agent:coder"]
ACCESS = {"read": READ, "create": ["owner"], "update": ["owner"],
          "delete": ["kyle"]}


def record_id(kind: str, legacy_id: str | int) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"tcms:{kind}:{legacy_id}").hex


def bundle() -> dict:
    cases = {"collection": "cases", "description": "The version-controlled QA case catalogue.",
             "fields": {
                 "key": {"type": "string", "max": 200, "required": True},
                 "suite": {"type": "string", "max": 100, "required": True},
                 "area": {"type": "string", "max": 100, "required": True},
                 "title": {"type": "text", "max": 4000, "required": True},
                 "layer": {"type": "enum", "values": ["unit", "integration", "e2e", "manual"], "required": True},
                 "priority": {"type": "string", "max": 16, "required": True},
                 "preconditions": {"type": "list", "items": {"type": "string", "max": 1000}, "max_items": 50},
                 "steps": {"type": "list", "items": {"type": "string", "max": 1000}, "max_items": 50},
                 "expected": {"type": "text", "max": 4000},
                 "automation": {"type": "list", "items": {"type": "string", "max": 1000}, "max_items": 50},
                 "tags": {"type": "list", "items": {"type": "string", "max": 200}, "max_items": 50},
                 "tickets": {"type": "list", "items": {"type": "string", "max": 200}, "max_items": 50},
                 "status": {"type": "enum", "values": ["active", "retired"], "required": True},
                 "synced_at": {"type": "datetime", "required": True},
                 "source_sha": {"type": "string", "max": 64},
             }, "access": ACCESS, "writers": {"create": ["tool:tcms"],
                                                     "update": ["tool:tcms"]},
             "rules": [{"kind": "unique", "fields": ["key"]}],
             "indexed": ["key", "status"]}
    runs = {"collection": "runs", "description": "One verified test execution and its summary.",
            "fields": {
                "legacy_id": {"type": "int", "min": 0},
                "commit_sha": {"type": "string", "max": 64, "required": True},
                "branch": {"type": "string", "max": 200},
                "run_id": {"type": "string", "max": 64},
                "agent": {"type": "string", "max": 100},
                "started_at": {"type": "datetime", "required": True},
                "finished_at": {"type": "datetime"},
                "verify_ok": {"type": "bool"},
                "suites": {"type": "list", "items": {"type": "object", "fields": {
                    "name": {"type": "string", "max": 100},
                    "exit": {"type": "int", "nullable": True},
                    "seconds": {"type": "number", "min": 0, "max": 86400},
                }}, "max_items": 50},
                "published_at": {"type": "datetime"},
                "ingest_state": {"type": "enum", "values": ["ingesting", "complete"],
                                 "required": True},
                "result_count": {"type": "int", "min": 0, "required": True},
                "pass_count": {"type": "int", "min": 0, "required": True},
                "fail_count": {"type": "int", "min": 0, "required": True},
                "skip_count": {"type": "int", "min": 0, "required": True},
                "flaky_count": {"type": "int", "min": 0, "required": True},
                "error_count": {"type": "int", "min": 0, "required": True},
            }, "access": ACCESS,
            "writers": {"create": ["tool:tcms"], "update": ["tool:tcms"]},
            "rules": [{"kind": "unique", "fields": ["run_id"]}],
            "indexed": ["run_id", "started_at"]}
    results = {"collection": "results", "description": "Individual machine-ingested test results.",
               "fields": {
                   "run": {"type": "ref", "collection": "runs", "required": True},
                   "ordinal": {"type": "int", "min": 0, "required": True},
                   "ref": {"type": "string", "max": 1000, "required": True},
                   "case": {"type": "ref", "collection": "cases", "on_delete": "unlink"},
                   "status": {"type": "enum", "values": ["pass", "fail", "skip", "flaky", "error"], "required": True},
                   "duration_ms": {"type": "int", "min": 0, "required": True},
                   "message": {"type": "text", "max": 16000},
                   "layer": {"type": "string", "max": 16, "required": True},
               }, "write_mode": "immutable", "access": ACCESS,
               "writers": {"create": ["tool:tcms"]},
               "rules": [{"kind": "unique", "fields": ["run", "ordinal"]}],
               "indexed": ["run", "status"]}
    coverage = {"collection": "coverage", "description": "Package coverage from verified reports.",
                "fields": {
                    "run": {"type": "ref", "collection": "runs", "required": True},
                    "ordinal": {"type": "int", "min": 0, "required": True},
                    "package": {"type": "string", "max": 1000, "required": True},
                    "lines_covered": {"type": "int", "min": 0, "required": True},
                    "lines_total": {"type": "int", "min": 0, "required": True},
                    "branch_rate": {"type": "number", "min": 0, "max": 1},
                }, "write_mode": "immutable", "access": ACCESS,
                "writers": {"create": ["tool:tcms"]},
                "rules": [{"kind": "unique", "fields": ["run", "ordinal"]}],
                "indexed": ["run"]}
    views = [
        {"view": "run_count", "collection": "runs",
         "filter": [{"field": "ingest_state", "op": "eq", "value": "complete"}],
         "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "case_count", "collection": "cases", "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "failure_count", "collection": "results",
         "filter": [{"field": "status", "op": "in", "value": ["fail", "error"]}],
         "aggregates": [{"fn": "count", "as": "n"}]},
        {"view": "runs_recent", "collection": "runs",
         "filter": [{"field": "ingest_state", "op": "eq", "value": "complete"}],
         "sort": [{"field": "started_at", "dir": "desc"}], "paging": True, "limit": 100},
        {"view": "run_by_runid", "collection": "runs",
         "params": {"run_id": {"type": "string", "required": True}},
         "filter": [{"field": "run_id", "op": "eq", "value": {"param": "run_id"}}],
         "limit": 1},
        {"view": "cases_recent", "collection": "cases",
         "sort": [{"field": "key", "dir": "asc"}], "paging": True, "limit": 100},
        {"view": "results_all", "collection": "results",
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True, "limit": 200},
        {"view": "coverage_all", "collection": "coverage",
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True, "limit": 200},
        {"view": "failed_results", "collection": "results",
         "filter": [{"field": "status", "op": "in", "value": ["fail", "error", "flaky"]}],
         "sort": [{"field": "created_at", "dir": "desc"}], "paging": True, "limit": 100},
        {"view": "results_for_run", "collection": "results",
         "params": {"run": {"type": "string", "required": True}},
         "filter": [{"field": "run", "op": "eq", "value": {"param": "run"}}],
         "paging": True, "limit": 100},
        {"view": "coverage_for_run", "collection": "coverage",
         "params": {"run": {"type": "string", "required": True}},
         "filter": [{"field": "run", "op": "eq", "value": {"param": "run"}}],
         "paging": True, "limit": 100},
    ]
    pages = [
        {"page": "home", "title": "Test coverage", "blocks": [
            {"kind": "metric", "view": "run_count", "label": "Recorded runs"},
            {"kind": "metric", "view": "case_count", "label": "Cases"},
            {"kind": "metric", "view": "failure_count", "label": "Failures"},
            {"kind": "table", "view": "runs_recent", "title": "Recent runs",
             "columns": [{"field": "commit_sha"}, {"field": "agent"},
                         {"field": "result_count", "format": "number"},
                         {"field": "fail_count", "format": "number"},
                         {"field": "started_at", "format": "date"}],
             "row_link": {"page": "run", "param": "run"}},
            {"kind": "table", "view": "failed_results", "title": "Recent failures",
             "columns": [{"field": "ref"}, {"field": "status"},
                         {"field": "message"}, {"field": "duration_ms", "format": "number"}]},
        ]},
        {"page": "cases", "title": "Test cases", "blocks": [
            {"kind": "table", "view": "cases_recent", "columns": [
                {"field": "key"}, {"field": "title"}, {"field": "layer"},
                {"field": "status"}]}]},
        {"page": "run", "title": "Test run", "params": {"run": {"type": "string", "required": True}},
         "blocks": [
             {"kind": "table", "view": "results_for_run", "params": {"run": {"page_param": "run"}},
              "columns": [{"field": "ref"}, {"field": "status"},
                          {"field": "duration_ms", "format": "number"}, {"field": "message"}]},
             {"kind": "table", "view": "coverage_for_run", "params": {"run": {"page_param": "run"}},
              "columns": [{"field": "package"}, {"field": "lines_covered", "format": "number"},
                          {"field": "lines_total", "format": "number"}]},
         ]},
    ]
    return {"collections": [cases, runs, results, coverage], "views": views,
            "pages": pages, "app_tools": [{"tool": "tcms", "roles": {
                "cases": {"collection": "cases", "verbs": ["read", "create", "update"]},
                "runs": {"collection": "runs", "verbs": ["read", "create", "update"]},
                "results": {"collection": "results", "verbs": ["read", "create"]},
                "coverage": {"collection": "coverage", "verbs": ["read", "create"]},
            }}]}


@dataclass(frozen=True)
class ImportRecord:
    collection: str
    id: str
    doc: dict
    created_at: datetime


def _list(value):
    if isinstance(value, str):
        value = json.loads(value)
    return value or []


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else value


def convert_snapshot(source: dict[str, list[dict]]) -> dict[str, list[ImportRecord]]:
    fallback = datetime(1970, 1, 1, tzinfo=timezone.utc)
    case_ids = {row["key"]: record_id("case", row["key"]) for row in source["cases"]}
    run_ids = {row["id"]: record_id("run", row["id"]) for row in source["test_runs"]}
    counts = {run_id: {name: 0 for name in ("pass", "fail", "skip", "flaky", "error")}
              for run_id in run_ids}
    for row in source["results"]:
        if row["test_run_id"] not in counts:
            raise ValueError("TCMS result points at a missing run")
        counts[row["test_run_id"]][row["status"]] += 1
        if row["case_key"] and row["case_key"] not in case_ids:
            raise ValueError("TCMS result points at a missing case")
    out = {name: [] for name in ("cases", "runs", "results", "coverage")}
    for row in source["cases"]:
        doc = {k: row[k] for k in ("key", "suite", "area", "title", "layer", "priority",
                                   "expected", "status", "source_sha")}
        doc["synced_at"] = _iso(row["synced_at"])
        doc.update({k: _list(row[k]) for k in ("preconditions", "steps", "automation", "tags", "tickets")})
        out["cases"].append(ImportRecord("cases", case_ids[row["key"]], doc,
                                         row["synced_at"] or fallback))
    for row in source["test_runs"]:
        count = counts[row["id"]]
        doc = {k: _iso(row[k]) for k in ("commit_sha", "branch", "run_id", "agent", "started_at",
                                         "finished_at", "verify_ok", "published_at")}
        doc.update(legacy_id=row["id"], suites=_list(row["suites"]), ingest_state="complete",
                   result_count=sum(count.values()), **{k + "_count": v for k, v in count.items()})
        out["runs"].append(ImportRecord("runs", run_ids[row["id"]], doc,
                                        row["started_at"] or fallback))
    result_ordinals: dict[int, int] = {}
    for row in source["results"]:
        doc = {k: row[k] for k in ("ref", "status", "duration_ms", "message", "layer")}
        run_id = row["test_run_id"]
        ordinal = result_ordinals.get(run_id, 0)
        result_ordinals[run_id] = ordinal + 1
        doc.update(run=run_ids[run_id], ordinal=ordinal,
                   case=case_ids.get(row["case_key"]))
        out["results"].append(ImportRecord("results", record_id("result", row["id"]), doc,
                                           fallback))
    coverage_ordinals: dict[int, int] = {}
    for row in source["coverage_snapshots"]:
        doc = {k: row[k] for k in ("package", "lines_covered", "lines_total", "branch_rate")}
        run_id = row["test_run_id"]
        ordinal = coverage_ordinals.get(run_id, 0)
        coverage_ordinals[run_id] = ordinal + 1
        doc.update(run=run_ids[run_id], ordinal=ordinal)
        out["coverage"].append(ImportRecord("coverage", record_id("coverage", row["id"]), doc,
                                            fallback))
    return out


async def read_source(session) -> dict[str, list[dict]]:
    if session.bind.dialect.name != "postgresql":
        raise RuntimeError("legacy TCMS copy requires PostgreSQL")
    await session.execute(text("LOCK TABLE " + ", ".join("app_tcms." + t for t in TABLES) +
                               " IN SHARE MODE"))
    return {name: [dict(row) for row in (await session.execute(
        text(f"SELECT * FROM app_tcms.{name}"))).mappings()] for name in TABLES}
