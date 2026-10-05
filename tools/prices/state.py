"""Yahoo connector writer over the caller-scoped Stockmarket App-data proxy."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import legacy

APP = "stockmarket"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("Stockmarket App-data proxy redirected")


def base(args):
    raw = (args.get("_app_data") or {}).get("url", "")
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise RuntimeError("Stockmarket App-data proxy unavailable")
    return raw.rstrip("/") + "/agent/records/"


def call(root, action, **body):
    if not action.replace("/", "").replace("_", "").isalpha():
        raise ValueError("invalid App-data operation")
    req = Request(root + action, data=json.dumps({"app": APP, **body},
                  separators=(",", ":")).encode(), method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=90) as response:
            raw = response.read(16 * 1024 * 1024 + 1)
    except HTTPError as exc:
        payload = exc.read(4096)
        try:
            detail = json.loads(payload).get("detail", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = ""
        raise RuntimeError(message or f"Stockmarket App data returned {exc.code}") from None
    if len(raw) > 16 * 1024 * 1024:
        raise RuntimeError("Stockmarket App-data response exceeds 16 MiB")
    return json.loads(raw)


def rows(root, view, params=None, *, cap=100_000):
    result, cursor = [], None
    while True:
        out = call(root, "query", view=view, params=params or {}, limit=200,
                   **({"cursor": cursor} if cursor else {}))
        result.extend({"id": row["id"], "version": row["values"]["version"],
                       **(row.get("values") or {})} for row in out.get("rows", []))
        if len(result) > cap:
            raise RuntimeError(f"{view} exceeded {cap} rows")
        cursor = out.get("next_cursor")
        if not cursor:
            return result


def stage(root, symbol_doc, bars):
    opened = call(root, "staging/open", collections=["symbols", "bars"])
    set_id = opened["set_id"]
    try:
        call(root, "staging/stage", set_id=set_id, collection="symbols",
             mode="upsert", records=[symbol_doc])
        for start in range(0, len(bars), 400):
            call(root, "staging/stage", set_id=set_id, collection="bars",
                 mode="upsert", records=bars[start:start + 400])
        return call(root, "staging/commit", set_id=set_id)
    except Exception:
        try:
            call(root, "staging/abandon", set_id=set_id)
        except Exception:  # noqa: BLE001, S110 - preserve the primary staging failure
            pass
        raise


def needs_refetch(root, symbol, fetched):
    events = [item for item in fetched if item["dividend"] or item["split_ratio"] is not None]
    if not events:
        return False
    stored = {}
    for item in events:
        match = rows(root, "bar_on_day", {"symbol": symbol, "day": item["day"]}, cap=1)
        if match:
            stored[item["day"]] = (match[0].get("dividend"), match[0].get("split_ratio"))
    if not legacy.new_events(events, stored):
        return False
    return bool(rows(root, "bars_before_day", {"symbol": symbol,
                                                "day": fetched[0]["day"]}, cap=1))


def run(args):
    root = base(args)
    kind = legacy.resolve_kind(args)
    existing = {row["symbol"]: row for row in rows(root, "all_symbols", cap=1000)}
    targets = legacy.plan_targets(args.get("symbols"),
                                  {s: row.get("status", "pending") for s, row in existing.items()},
                                  args.get("range") or "5d")
    if not targets:
        return {"written": 0, "symbols": [], "errors": [], "refetched": [],
                "note": "no symbols tracked yet"}
    done, errors, refetched = [], [], []
    for symbol, rng in targets:
        try:
            bars, currency = legacy.fetch_bars(symbol, rng)
            did_refetch = bool(bars and rng != "max" and needs_refetch(root, symbol, bars))
            if did_refetch:
                bars, full_currency = legacy.fetch_bars(symbol, "max")
                currency = full_currency or currency
                if not bars:
                    raise RuntimeError("full-history refetch returned no data")
            now = datetime.now(timezone.utc).isoformat()
            previous = existing.get(symbol)
            doc = {k: v for k, v in (previous or {}).items() if k not in (
                "id", "version", "created_at", "updated_at", "author", "via",
                "collection_version", "restricted")}
            doc.update(symbol=symbol, label=doc.get("label") or "",
                       kind=doc.get("kind") or kind,
                       status="ok" if bars else "invalid",
                       error="" if bars else "no price data from Yahoo — check the ticker",
                       last_synced_at=now)
            if currency:
                doc["currency"] = currency
            if not previous:
                doc["added_at"] = now
            stage(root, doc, bars)
            if not bars:
                errors.append({"symbol": symbol, "error": doc["error"]})
            else:
                done.append({"symbol": symbol, "rows": len(bars),
                             "first_day": bars[0]["day"], "last_day": bars[-1]["day"]})
                if did_refetch:
                    refetched.append(symbol)
        except Exception as exc:  # noqa: BLE001 - one bad ticker must not sink the sync
            errors.append({"symbol": symbol, "error": str(exc)[:200]})
    if errors and not done:
        raise RuntimeError("; ".join(f"{e['symbol']}: {e['error']}" for e in errors))
    return {"written": sum(item["rows"] for item in done),
            "symbols": done, "errors": errors, "refetched": refetched}
