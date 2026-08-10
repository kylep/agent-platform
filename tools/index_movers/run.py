"""index_movers tool: attribute an index ETF's last session to its holdings.

The arithmetic here is the whole point of the tool. Asking a model to multiply
ten weights by ten returns and rank the results produces confident wrong
numbers, and those numbers are the entire claim of a market brief ("NVDA drove
the drop"). So the tool does the math and the model only narrates it.

Contribution is weight x return, expressed in basis points: a name at 7.1% of
the fund that fell 3.1% contributed 7.1 * -3.1 = -22bp of the index's move.
Summing the top ten gives `explained_bps`, which the caller compares against
the index's `actual_bps` to tell a name-driven session from a broad one.

Executor contract: JSON args on stdin, JSON result on stdout, non-zero exit +
stderr message on failure. No secrets, no database — read-only Yahoo access.
"""
import json
import math
import re
import sys

SYMBOL_RE = re.compile(r"^[A-Z0-9.^-]{1,12}$")
# Yahoo publishes only the top ten holdings; that is ~50% of QQQ, ~38% of SPY
# and ~45% of XIU, which is enough to explain most single-name-driven days.
LOOKBACK = "5d"


def clean_symbol(raw: str) -> str:
    sym = str(raw).strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"invalid ticker {raw!r}")
    return sym


def series_closes(df) -> dict[str, float]:
    """A history frame → {iso_day: close}, dropping empty bars."""
    out = {}
    for ts, row in df.iterrows():
        v = row.get("Close")
        if v is None or (isinstance(v, float) and math.isnan(v)) or float(v) <= 0:
            continue
        out[str(ts.date())] = round(float(v), 4)
    return out


def session_return(closes: dict[str, float], day: str | None = None):
    """(day, return_pct) for `day` (default: the latest close) against the
    close before it. None when there aren't two comparable bars."""
    days = sorted(closes)
    if len(days) < 2:
        return None
    if day is None:
        day = days[-1]
    if day not in closes:
        return None
    i = days.index(day)
    if i == 0:
        return None
    prev = closes[days[i - 1]]
    if prev <= 0:
        return None
    return day, round((closes[day] / prev - 1) * 100, 4)


def normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    """Yahoo gives holding weights as fractions (0.071); some responses give
    percents (7.1). Decide by magnitude rather than trusting either."""
    if not raw:
        return {}
    return ({k: round(v * 100, 4) for k, v in raw.items()}
            if max(raw.values()) <= 1.5 else
            {k: round(v, 4) for k, v in raw.items()})


def read_holdings(ticker) -> tuple[dict[str, float], dict[str, str], dict[str, float], str]:
    """(weights_pct, names, sector_weightings_pct, note). Holdings data is
    scraped and regularly missing for non-US funds, so every failure here is
    reported as a note and an empty result — never an exception. A brief that
    says "the index fell, broadly" beats a run that crashed."""
    try:
        funds = ticker.funds_data
    except Exception as e:
        return {}, {}, {}, f"holdings unavailable from Yahoo ({type(e).__name__})"
    weights, names = {}, {}
    try:
        top = funds.top_holdings
        for sym, row in top.iterrows():
            s = str(sym).strip().upper()
            if not SYMBOL_RE.match(s):
                continue
            pct = row.get("Holding Percent")
            if pct is None or (isinstance(pct, float) and math.isnan(pct)):
                continue
            weights[s] = float(pct)
            names[s] = str(row.get("Name") or "")[:80]
    except Exception as e:
        return {}, {}, {}, f"top holdings unavailable from Yahoo ({type(e).__name__})"
    sectors = {}
    try:
        sectors = normalize_weights({str(k): float(v) for k, v in
                                     (funds.sector_weightings or {}).items()})
    except Exception:
        pass                      # sectors are a nice-to-have, not the answer
    note = "" if weights else "Yahoo returned no holdings for this fund"
    return normalize_weights(weights), names, sectors, note


def holding_closes(symbols: list[str], index: str) -> dict[str, dict[str, float]]:
    """One batched download for every holding. The index ticker rides along so
    the frame is always multi-ticker shaped, which spares us yfinance's
    single-ticker column layout."""
    import yfinance
    df = yfinance.download(symbols + [index], period=LOOKBACK, auto_adjust=True,
                           group_by="ticker", progress=False, threads=True)
    out = {}
    for sym in symbols:
        try:
            out[sym] = series_closes(df[sym])
        except Exception:
            out[sym] = {}
    return out


def build(index: str) -> dict:
    import yfinance
    ticker = yfinance.Ticker(index)
    idx_closes = series_closes(ticker.history(period=LOOKBACK, auto_adjust=True))
    idx = session_return(idx_closes)
    if idx is None:
        raise LookupError(
            f"no recent price data for {index!r} — check the ticker (Yahoo "
            f"conventions; TSX tickers end in .TO)")
    day, idx_ret = idx

    weights, names, sectors, note = read_holdings(ticker)
    holdings, missing = [], []
    if weights:
        closes = holding_closes(sorted(weights), index)
        for sym, weight in weights.items():
            # Attribute on the index's session, not each holding's own latest
            # bar — a stale or halted name must not masquerade as a mover.
            r = session_return(closes.get(sym, {}), day)
            if r is None:
                missing.append(sym)
                continue
            ret = r[1]
            holdings.append({
                "symbol": sym, "name": names.get(sym, ""), "weight_pct": weight,
                "return_pct": round(ret, 2),
                "contrib_bps": round(weight * ret, 1),
            })
        holdings.sort(key=lambda h: abs(h["contrib_bps"]), reverse=True)
    if missing:
        note = (note + "; " if note else "") + \
            f"no {day} bar for {', '.join(sorted(missing))}"

    result = {
        "index": index, "day": day,
        "return_pct": round(idx_ret, 2),
        "actual_bps": round(idx_ret * 100, 1),
        "explained_bps": round(sum(h["contrib_bps"] for h in holdings), 1),
        "coverage_pct": round(sum(h["weight_pct"] for h in holdings), 2),
        "holdings": holdings,
        "sectors": sectors,
    }
    if note:
        result["note"] = note
    return result


def main() -> int:
    args = json.load(sys.stdin)
    try:
        index = clean_symbol(args["index"])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    try:
        print(json.dumps(build(index)))
    except LookupError as e:
        print(str(e), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
