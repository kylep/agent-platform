"""Backtest engine I/O over the Stockmarket state App.

The simulation remains the same pure engine. A staging set publishes its
dataset, experiment, metrics, series and events atomically after computation.
"""
from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import engine
import legacy
from legacy import Coverage, ToolError

APP = "stockmarket"
legacy.URL = "/apps/state/stockmarket/pages/backtest?experiment_id={}"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise ToolError("Stockmarket App-data proxy redirected")


def base(args):
    raw = (args.get("_app_data") or {}).get("url", "")
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise ToolError("Stockmarket App-data proxy unavailable", 2)
    return raw.rstrip("/") + "/agent/records/"


def call(root, action, **body):
    if not action.replace("/", "").replace("_", "").isalpha():
        raise ToolError("invalid App-data operation")
    req = Request(root + action, data=json.dumps({"app": APP, **body},
                  separators=(",", ":")).encode(), method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=120) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except HTTPError as exc:
        payload = exc.read(4096)
        try:
            detail = json.loads(payload).get("detail", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = ""
        raise ToolError(message or f"Stockmarket App data returned {exc.code}") from None
    if len(raw) > 16 * 1024 * 1024:
        raise ToolError("Stockmarket App-data response exceeds 16 MiB")
    return json.loads(raw)


def rows(root, view, params=None, *, cap=200_000):
    out, cursor = [], None
    while True:
        response = call(root, "query", view=view, params=params or {}, limit=200,
                        **({"cursor": cursor} if cursor else {}))
        out.extend({"id": row["id"], "version": row["values"]["version"],
                    **(row.get("values") or {})} for row in response.get("rows", []))
        if len(out) > cap:
            raise ToolError(f"{view} exceeded {cap} records")
        cursor = response.get("next_cursor")
        if not cursor:
            return out


def gather(root, spec):
    symbols = {r["symbol"]: r for r in rows(root, "all_symbols", cap=1000)}
    candidates = sorted(set(spec.symbols()) | set(legacy.CALENDAR_SYMBOL.values())
                        | {legacy.FX_SYMBOL})
    info = {}
    for symbol in candidates:
        row = symbols.get(symbol)
        if row:
            synced = row.get("last_synced_at")
            info[symbol] = {"currency": row.get("currency"),
                            "last_synced_at": datetime.fromisoformat(
                                synced.replace("Z", "+00:00")) if synced else None}
    load, _ = Coverage.plan(spec, {s: i["currency"] for s, i in info.items()})
    need = spec.max_window() + 1
    lo = spec.period.start - legacy.timedelta(days=need * 2 + 30)
    bars = {}
    for symbol in load:
        points = rows(root, "bars_for_symbol", {"symbol": symbol}, cap=20_000)
        bars[symbol] = [(p["day"], p.get("close_split_adj"), p.get("adj_close"),
                         p.get("dividend"), p.get("split_ratio"))
                        for p in points if lo.isoformat() <= p["day"]
                        <= spec.period.end.isoformat()]
    return Coverage(spec, info, bars, legacy._today())


def stage(root, result, gz, dataset):
    eid = result.experiment_id
    if rows(root, "experiment_by_id", {"experiment_id": eid}, cap=1):
        return False
    sha = result.dataset_sha
    have_dataset = bool(rows(root, "datasets_for_sha", {"sha": sha}, cap=10000))
    now = datetime.now(timezone.utc).isoformat()
    days = [bar[0] for symbol in dataset.values() for bar in symbol["bars"]]
    batches = []
    if not have_dataset:
        encoded = base64.b64encode(gz).decode("ascii")
        batches.append(("datasets", "insert", [{
            "sha": sha, "part": i // 12000, "data_b64": encoded[i:i + 12000],
            "symbols_json": legacy._j(sorted(dataset)),
            "day_from": min(days), "day_to": max(days), "source_created_at": now,
        } for i in range(0, len(encoded), 12000)]))
    batches.append(("experiments", "insert", [{
        "experiment_id": eid, "name": result.spec["name"],
        "spec_json": legacy._j(result.spec), "description": result.description,
        "assumed_json": legacy._j(result.assumed),
        "caveats_json": legacy._j(result.caveats),
        "exclusions_json": legacy._j(result.exclusions),
        "dataset_sha": sha, "engine_version": result.engine_version,
        "caller": legacy._caller(), "run_id": legacy._run_id(),
        "source_created_at": now,
    }]))
    result_rows, metric_parts = [], []
    for strategy in result.strategies:
        full = legacy._j(strategy.metrics)
        preview = full
        if len(full) > 16000:
            preview = legacy._j({k: v for k, v in strategy.metrics.items()
                                 if isinstance(v, (str, int, float, bool, type(None)))})
            if len(preview) > 16000:
                raise ToolError("backtest metrics preview exceeds 16 KiB")
            metric_parts.extend({
                "experiment_id": eid, "strategy_id": strategy.strategy_id,
                "part": i // 12000, "data": full[i:i + 12000],
            } for i in range(0, len(full), 12000))
        result_rows.append({"experiment_id": eid,
                            "strategy_id": strategy.strategy_id,
                            "label": strategy.label, "metrics_json": preview})
    batches.append(("results", "insert", result_rows))
    if metric_parts:
        batches.append(("metrics_parts", "insert", metric_parts))
    batches.append(("series", "insert", [{
        "experiment_id": eid, "strategy_id": s.strategy_id,
        "day": point.day.isoformat(), "value": float(point.value),
        "contributed": float(point.contributed),
    } for s in result.strategies for point in s.series]))
    batches.append(("events", "insert", [{
        "legacy_id": int(hashlib.sha256(
            f"{eid}:{s.strategy_id}:{ordinal}".encode()).hexdigest()[:15], 16),
        "experiment_id": eid, "strategy_id": s.strategy_id,
        "day": event.day.isoformat(), "kind": event.kind,
        "symbol": event.symbol, "detail_json": legacy._j(event.detail),
    } for s in result.strategies for ordinal, event in enumerate(s.events)]))
    opened = call(root, "staging/open", collections=[name for name, _, _ in batches])
    set_id = opened["set_id"]
    try:
        for collection, mode, records in batches:
            for start in range(0, len(records), 400):
                call(root, "staging/stage", set_id=set_id, collection=collection,
                     mode=mode, records=records[start:start + 400])
        call(root, "staging/commit", set_id=set_id)
    except Exception:
        try:
            call(root, "staging/abandon", set_id=set_id)
        except Exception:  # noqa: BLE001, S110 - preserve the primary staging failure
            pass
        raise
    return True


def execute(root, raw_spec, sha, gz, dataset, fetched):
    try:
        result = engine.run_backtest(raw_spec, dataset, sha, fetched)
    except engine.SpecError as exc:
        raise ToolError(f"invalid spec: {exc}") from None
    except legacy.EngineError as exc:
        raise ToolError(f"backtest cannot run: {exc}") from None
    stored = stage(root, result, gz, dataset)
    return result, stored


def do_run(root, raw_spec, **extra):
    spec = legacy._validated(raw_spec)
    cov = gather(root, spec)
    if cov.blocking:
        raise ToolError("data missing; load it with prices first: "
                        + json.dumps(cov.missing) + " ("
                        + legacy._short("; ".join(cov.notes), 800) + ")")
    _, sha, gz, dataset, fetched = legacy.pin(cov)
    result, stored = execute(root, raw_spec, sha, gz, dataset, fetched)
    if cov.missing:
        extra["missing"] = cov.missing
    return legacy.summary(result, stored, **extra)


def rerun(root, args):
    eid = args.get("experiment_id")
    if not isinstance(eid, str) or not legacy.EXPERIMENT_ID_RE.fullmatch(eid):
        raise ToolError("experiment_id must be 32 lowercase hex characters", 2)
    match = rows(root, "experiment_by_id", {"experiment_id": eid}, cap=1)
    if not match:
        raise ToolError(f"no experiment {eid}")
    raw_spec = json.loads(match[0]["spec_json"])
    old_sha = match[0]["dataset_sha"]
    rerun_of = {"experiment_id": eid, "dataset_sha": old_sha}
    if args.get("refresh"):
        return do_run(root, raw_spec, rerun_of=rerun_of)
    chunks = rows(root, "datasets_for_sha", {"sha": old_sha}, cap=10000)
    if not chunks:
        raise ToolError(f"pinned dataset {old_sha} is gone; rerun with refresh: true")
    chunks.sort(key=lambda r: r["part"])
    if [c["part"] for c in chunks] != list(range(len(chunks))):
        raise ToolError(f"pinned dataset {old_sha} has missing parts")
    sha, gz, dataset, fetched = legacy.unpin(base64.b64decode(
        "".join(c["data_b64"] for c in chunks), validate=True), old_sha)
    result, stored = execute(root, raw_spec, sha, gz, dataset, fetched)
    return legacy.summary(result, stored, reproduced=result.experiment_id == eid,
                          rerun_of=rerun_of)


def run(args):
    action = args.get("action")
    if action not in legacy.ACTIONS:
        raise ToolError(f"action must be one of {', '.join(legacy.ACTIONS)}", 2)
    if action == "describe_primitives":
        return json.loads(engine.canonical_json(engine.describe_primitives()))
    root = base(args)
    if action == "validate":
        v = engine.validate(args.get("spec"))
        out = {"spec": None, "description": None,
               "assumed": [a.to_dict() for a in v.assumed],
               "errors": [e.to_dict() for e in v.errors], "missing": [], "notes": []}
        if v.ok:
            cov = gather(root, v.spec)
            out.update(spec=v.spec.to_dict(), description=engine.describe(v.spec),
                       missing=cov.missing, notes=cov.notes)
        return json.loads(engine.canonical_json(out))
    if action == "run":
        return do_run(root, args.get("spec"))
    return rerun(root, args)
