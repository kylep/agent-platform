"""stocks tool: Yahoo Finance daily history + summary for one symbol.

Ported from kytrade's providers/yahoo.py (multi/apps/kytrade) — same
normalization: a JSON-safe, date-keyed dict, prices rounded to 4 decimals to
shed float32 noise, auto-adjusted history.

Executor contract: JSON args on stdin, JSON result on stdout, non-zero exit +
stderr message on failure. Env is minimal — no secrets needed here.
"""
import json
import math
import sys

PRICE_FIELDS = {"Open": "open", "High": "high", "Low": "low",
                "Close": "close", "Volume": "volume"}
# Full OHLCV for short windows; closes-only beyond that (output stays small
# enough for a model to actually read, and for the executor's 256KiB cap).
FULL_DETAIL_RANGES = {"5d", "1mo"}


def history_to_dict(df) -> dict:
    """yfinance history frame → {iso_date: {open,high,low,close,volume}}."""
    out = {}
    for ts, row in df.iterrows():
        day = {}
        for col, field in PRICE_FIELDS.items():
            v = row.get(col)
            day[field] = None if v is None or (isinstance(v, float) and math.isnan(v)) \
                else round(float(v), 4)
        if all(v is None for v in day.values()):
            continue
        if day["volume"] is not None:
            day["volume"] = int(day["volume"])
        out[str(ts.date())] = day
    return out


def summarize(history: dict) -> dict:
    """Window summary over the (already sorted-keyed) history dict."""
    closes = [(d, r["close"]) for d, r in sorted(history.items())
              if r.get("close") is not None and r["close"] > 0]
    if len(closes) < 2:
        return {"days": len(closes)}
    highs = [r["high"] for r in history.values() if r.get("high") is not None]
    lows = [r["low"] for r in history.values() if r.get("low") is not None]
    vols = [r["volume"] for r in history.values() if r.get("volume")]
    return {
        "days": len(closes),
        "start_date": closes[0][0], "end_date": closes[-1][0],
        "start_close": closes[0][1], "latest_close": closes[-1][1],
        "return_pct": round((closes[-1][1] / closes[0][1] - 1) * 100, 2),
        "high": max(highs) if highs else max(c for _, c in closes),
        "low": min(lows) if lows else min(c for _, c in closes),
        "avg_volume": int(sum(vols) / len(vols)) if vols else 0,
    }


def shape_output(symbol: str, rng: str, history: dict) -> dict:
    result = {"symbol": symbol, "range": rng, "summary": summarize(history)}
    if rng in FULL_DETAIL_RANGES:
        result["history"] = dict(sorted(history.items()))
    else:
        result["closes"] = {d: r["close"] for d, r in sorted(history.items())
                            if r.get("close") is not None}
    return result


def main() -> int:
    args = json.load(sys.stdin)
    symbol = args["symbol"].strip().upper()
    rng = args.get("range") or "1mo"

    import yfinance
    ticker = yfinance.Ticker(symbol)
    df = ticker.history(period=rng, auto_adjust=True)
    history = history_to_dict(df)
    if not history:
        print(f"no price data for symbol {symbol!r} — check the ticker "
              f"(Yahoo conventions; TSX tickers end in .TO)", file=sys.stderr)
        return 2
    print(json.dumps(shape_output(symbol, rng, history)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
