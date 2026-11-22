"""Executor entrypoint for the stock-bets Tool.

Stdin: the JSON arguments, with `_app_data.url` (this call's scoped App-data
endpoint) added by the executor. Env: TOOL_CALLER_AGENT, the broker-verified
caller. Daily bars come from Yahoo Finance through yfinance here, in trusted
code; no price ever arrives from a model.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import bets

PAGE = 200
MAX_ROWS = 5000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise bets.ToolError("the App-data endpoint redirected")


class AppStore:
    """The `stock-bets` App through this call's scoped endpoint."""

    def __init__(self, raw_url: str):
        parsed = urlsplit(raw_url or "")
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or not parsed.path.endswith("/api/app-data")):
            raise bets.ToolError("stock-bets App data is unavailable to this call")
        self.root = raw_url.rstrip("/") + "/agent/records/"

    def _post(self, action: str, body: dict) -> dict:
        req = Request(self.root + action, method="POST",
                      data=json.dumps({"app": bets.APP, **body}).encode(),
                      headers={"Content-Type": "application/json"})
        try:
            with build_opener(_NoRedirect()).open(req, timeout=30) as resp:
                raw = resp.read(4 * 1024 * 1024 + 1)
        except HTTPError as exc:
            try:
                detail = json.loads(exc.read(4096)).get("detail", {})
                msg = detail.get("message") if isinstance(detail, dict) else str(detail)
                code = detail.get("code") if isinstance(detail, dict) else ""
            except ValueError:
                msg, code = "", ""
            if code == "AD-UNIQUE":
                raise bets.ToolError("duplicate_session: a bet for this session "
                                     "already exists") from None
            raise bets.ToolError(f"App data {action} failed ({exc.code}): "
                                 f"{code} {msg}".strip()) from None
        if len(raw) > 4 * 1024 * 1024:
            raise bets.ToolError("App-data answer is too large")
        return json.loads(raw)

    def query(self, view, params=None):
        out, cursor = [], None
        while True:
            body = {"view": view, "params": params or {}, "limit": PAGE}
            if cursor:
                body["cursor"] = cursor
            data = self._post("query", body)
            for row in data.get("rows", []):
                out.append({**(row.get("values") or {}), "id": row["id"]})
            if len(out) > MAX_ROWS:
                raise bets.ToolError(f"{view} exceeds {MAX_ROWS} records")
            nxt = data.get("next_cursor")
            if not nxt or nxt == cursor:
                return out
            cursor = nxt

    def create(self, collection, values, request_id):
        return self._post("create", {"collection": collection, "values": values,
                                     "request_id": request_id})

    def update(self, collection, record_id, version, values, request_id):
        return self._post("update", {"collection": collection, "id": record_id,
                                     "expected_version": version, "values": values,
                                     "request_id": request_id})


def yahoo_bars(symbols: list[str], start: date, end: date) -> dict:
    """Daily OHLC per symbol and session date, start..end inclusive."""
    import yfinance as yf

    frame = yf.download(symbols, start=start.isoformat(),
                        end=(end + timedelta(days=1)).isoformat(), interval="1d",
                        auto_adjust=False, actions=False, group_by="ticker",
                        progress=False, threads=True)
    out: dict = {}
    if frame is None or frame.empty:
        return out
    for sym in symbols:
        try:
            sub = frame[sym]
        except KeyError:
            continue
        for idx, row in sub.iterrows():
            vals = [row.get(k) for k in ("Open", "High", "Low", "Close")]
            if any(v is None or v != v or v <= 0 for v in vals):
                continue  # NaN or empty: treat as missing, never guess
            day = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            out.setdefault(sym, {})[day] = dict(zip(("open", "high", "low", "close"),
                                                    (float(v) for v in vals)))
    return out


def main() -> int:
    try:
        args = json.load(sys.stdin)
        ctx = bets.Ctx(store=AppStore((args.get("_app_data") or {}).get("url", "")),
                       bars=yahoo_bars,
                       caller=os.environ.get("TOOL_CALLER_AGENT", "")[:128],
                       now=bets.utc_now())
        print(json.dumps(bets.execute(ctx, args), separators=(",", ":")))
        return 0
    except (bets.ToolError, ValueError, KeyError, TypeError, OSError) as exc:
        print(str(exc) or type(exc).__name__, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
