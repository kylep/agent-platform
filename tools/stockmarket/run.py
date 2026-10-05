"""Small, bounded maintenance operations for the Stockmarket state App."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from brief import clean_brief

APP = "stockmarket"
SYMBOL = re.compile(r"^[A-Z0-9.^=-]{1,12}$")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("Stockmarket App-data proxy redirected")


def _base(args):
    raw = (args.get("_app_data") or {}).get("url", "")
    parsed = urlsplit(raw)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.path.endswith("/api/app-data")):
        raise RuntimeError("Stockmarket App-data proxy unavailable")
    return raw.rstrip("/") + "/agent/records/"


def _call(root, action, **body):
    if not action.replace("/", "").replace("_", "").isalpha():
        raise ValueError("invalid App-data operation")
    req = Request(root + action, data=json.dumps({"app": APP, **body},
                  separators=(",", ":")).encode(), method="POST",
                  headers={"Content-Type": "application/json"})
    try:
        with build_opener(NoRedirect()).open(req, timeout=45) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
    except HTTPError as exc:
        payload = exc.read(4096)
        try:
            detail = json.loads(payload).get("detail", {})
            message = detail.get("message") if isinstance(detail, dict) else str(detail)
        except ValueError:
            message = ""
        raise RuntimeError(message or f"App data returned {exc.code}") from None
    if len(raw) > 2 * 1024 * 1024:
        raise RuntimeError("App-data response exceeded 2 MiB")
    return json.loads(raw)


def _query(root, view, params=None):
    out = _call(root, "query", view=view, params=params or {}, limit=100)
    if out.get("next_cursor"):
        raise RuntimeError(f"{view} exceeded its bounded first page")
    return [{"id": row["id"], "version": row["values"]["version"],
             **(row.get("values") or {})} for row in out.get("rows", [])]


def _upsert(root, collection, values):
    opened = _call(root, "staging/open", collections=[collection])
    set_id = opened["set_id"]
    try:
        _call(root, "staging/stage", set_id=set_id, collection=collection,
              mode="upsert", records=[values])
        return _call(root, "staging/commit", set_id=set_id)
    except Exception:
        try:
            _call(root, "staging/abandon", set_id=set_id)
        except Exception:  # noqa: BLE001, S110 - preserve the primary staging failure
            pass
        raise


def run(args):
    root = _base(args)
    action = args.get("action")
    if action == "brief":
        raw = args.get("brief")
        if not isinstance(raw, dict):
            raise ValueError("brief must be an object")
        clean = clean_brief(raw)
        if clean is None:
            raise ValueError("brief needs a session date and a body or index numbers")
        doc = {"day": clean["day"], "body": clean["body"],
               "tags_json": json.dumps(clean["tags"], separators=(",", ":")),
               "indexes_json": json.dumps(clean["indexes"], separators=(",", ":")),
               "movers_json": json.dumps(clean["movers"], separators=(",", ":")),
               "run_id": str(args.get("_run_id") or "")[:32],
               "source_created_at": datetime.now(timezone.utc).isoformat()}
        _upsert(root, "briefs", doc)
        return {"stored": True, "day": clean["day"], "indexes": len(clean["indexes"]),
                "movers": len(clean["movers"])}
    if action == "latest":
        rows = _query(root, "briefs_recent")
        if not rows:
            return {"brief": None}
        row = rows[0]
        return {"brief": {"day": row["day"], "body": row.get("body", ""),
                          "tags": json.loads(row.get("tags_json") or "[]"),
                          "indexes": json.loads(row.get("indexes_json") or "[]"),
                          "movers": json.loads(row.get("movers_json") or "[]")}}
    if action not in ("add_symbol", "remove_symbol"):
        raise ValueError("action must be brief, latest, add_symbol or remove_symbol")
    symbol = str(args.get("symbol") or "").strip().upper()
    if not SYMBOL.fullmatch(symbol):
        raise ValueError("invalid Yahoo ticker")
    symbols = {row["symbol"]: row for row in _query(root, "all_symbols")}
    watch = {row["symbol"]: row for row in _query(root, "watchlist", {"user": "kyle"})}
    if action == "remove_symbol":
        row = watch.get(symbol)
        if row is None:
            return {"symbol": symbol, "removed": False}
        _call(root, "delete", request_id=f"stockmarket-unwatch:{row['id']}:{row['version']}",
              collection="watchlist", id=row["id"], expected_version=row["version"])
        return {"symbol": symbol, "removed": True}
    if symbol in ("QQQ", "SPY", "XIU.TO"):
        return {"symbol": symbol, "watched": True, "pinned_index": True}
    now = datetime.now(timezone.utc).isoformat()
    old = symbols.get(symbol)
    if old is None:
        _upsert(root, "symbols", {"symbol": symbol,
                "label": str(args.get("label") or "")[:128], "kind": "watch",
                "status": "pending", "error": "", "added_at": now})
    elif old.get("kind") in ("research", "fx"):
        _call(root, "update", request_id=f"stockmarket-watch-kind:{old['id']}:{old['version']}",
              collection="symbols", id=old["id"], expected_version=old["version"],
              values={"kind": "watch", "label": str(args.get("label") or old.get("label") or "")[:128]})
    _upsert(root, "watchlist", {"user": "kyle", "symbol": symbol, "added_at": now})
    return {"symbol": symbol, "watched": True, "backfill": old is None or old.get("status") == "pending"}


def main():
    try:
        args = json.load(sys.stdin)
        if not isinstance(args, dict):
            raise TypeError("arguments must be an object")
        result = run(args)
    except Exception as exc:  # noqa: BLE001 - CLI contract is a short error, not a traceback
        print(f"Stockmarket tool failed: {str(exc)[:500]}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
