"""Per-strategy metrics from a simulated series and event log.

Conventions (printed with the report, fixed by test_metrics.py):
- Cash flows are the daily deltas of `contributed` (money in, negative) on
  the day they land, plus the final value (positive) on the last day.
- XIRR uses a 365-day year, the spreadsheet convention, so a result can be
  checked with `=XIRR(...)`. It never guesses: no sign change or no
  convergence is `None` plus a reason the caveats print.
- TWR chains daily returns with that day's deposit taken out, so money in
  never looks like performance; it is annualized over 365.25-day years from
  the first deposit to the last day.
- Drawdown, volatility and Sharpe use that same TWR index (a drawdown on
  the raw value would be masked by deposits). Volatility is the sample std
  of daily returns x sqrt(252); Sharpe is (mean daily return - rf/252) /
  std x sqrt(252), with rf from the spec.
- Ratios are quantized to 8 dp and money to cents, half-even.
"""
from decimal import Decimal, localcontext

from .caveats import is_individual_stock
from .spec import CASH_Q, DECIMAL_CONTEXT

RATE_Q = Decimal("0.00000001")
XIRR_YEAR = Decimal(365)
TWR_YEAR = Decimal("365.25")
TRADING_DAYS = Decimal(252)
_NEWTON_STEPS = 100
_BISECT_STEPS = 400
_TOL = Decimal("1E-15")
# Just above the -100% pole, where (1 + r) ** -t is still finite.
_LOW = Decimal("-0.9999999999")
_HIGH_LIMIT = Decimal(10) ** 6


def _q(x):
    return x.quantize(RATE_Q, context=DECIMAL_CONTEXT)


def _q_cash(x):
    return x.quantize(CASH_Q, context=DECIMAL_CONTEXT)


# ---- cash flows + XIRR -------------------------------------------------------

def flows(series):
    """[(day, amount)]: -deposit on its day, +final value on the last day
    (netted when a deposit lands that same day). [] when nothing went in."""
    out, prev = [], Decimal(0)
    for p in series:
        if p.contributed != prev:
            out.append((p.day, prev - p.contributed))
            prev = p.contributed
    if not out:
        return []
    last = series[-1]
    if out[-1][0] == last.day:
        out[-1] = (last.day, out[-1][1] + last.value)
    else:
        out.append((last.day, last.value))
    return out


def _npv(r, flows_t):
    """(f(r), f'(r)) for f(r) = sum(c * (1 + r) ** -t)."""
    base = 1 + r
    f = df = Decimal(0)
    for t, c in flows_t:
        v = c * base ** -t
        f += v
        df -= t * v / base
    return f, df


def _newton(flows_t):
    r = Decimal("0.1")
    scale = sum((abs(c) for _, c in flows_t), Decimal(0))
    for _ in range(_NEWTON_STEPS):
        f, df = _npv(r, flows_t)
        if df == 0:
            return None
        nr = r - f / df
        if nr <= -1:
            return None
        if abs(nr - r) <= _TOL:
            return nr if abs(_npv(nr, flows_t)[0]) <= scale * Decimal("1E-12") else None
        r = nr
    return None


def _bisect(flows_t):
    lo, hi = _LOW, Decimal(1)
    f_lo = _npv(lo, flows_t)[0]
    f_hi = _npv(hi, flows_t)[0]
    while (f_lo > 0) == (f_hi > 0) and hi < _HIGH_LIMIT:
        hi *= 10
        f_hi = _npv(hi, flows_t)[0]
    if (f_lo > 0) == (f_hi > 0):
        return None
    for _ in range(_BISECT_STEPS):
        if hi - lo <= _TOL:
            break
        mid = (lo + hi) / 2
        f_mid = _npv(mid, flows_t)[0]
        if f_mid == 0:
            return mid
        if (f_mid > 0) == (f_lo > 0):
            lo, f_lo = mid, f_mid
        else:
            hi = mid
    return (lo + hi) / 2


def xirr(cash_flows):
    """(rate | None, reason | None) for [(day, amount)] (money in negative)."""
    if not any(c < 0 for _, c in cash_flows):
        return None, "no money was contributed"
    day0 = cash_flows[0][0]
    if all(day == day0 for day, _ in cash_flows):
        return None, "all cash flows fall on one day"
    if cash_flows[-1][1] == 0 and all(c < 0 for _, c in cash_flows[:-1]):
        return Decimal(-1), None   # total loss: the -100% limit, exactly
    if not any(c > 0 for _, c in cash_flows):
        return None, "the cash flows never change sign"
    with localcontext(DECIMAL_CONTEXT):
        flows_t = [(Decimal((day - day0).days) / XIRR_YEAR, c) for day, c in cash_flows]
        r = _newton(flows_t)
        if r is None:
            r = _bisect(flows_t)
        if r is None:
            return None, "the solver found no rate that zeroes the cash flows"
        return _q(r), None


# ---- TWR index and what is read off it ------------------------------------------

def daily_returns(series):
    """[(day, r)]: r = (value - that day's deposit) / previous value - 1, only
    once there was money to earn a return on."""
    out = []
    with localcontext(DECIMAL_CONTEXT):
        for prev, p in zip(series, series[1:]):
            if prev.value > 0:
                out.append((p.day, (p.value - (p.contributed - prev.contributed))
                            / prev.value - 1))
    return out


def _drawdown(index):
    peak_val, peak_day = None, None
    depth, peak, trough = Decimal(0), None, None
    for day, v in index:
        if peak_val is None or v > peak_val:
            peak_val, peak_day = v, day
            continue
        dd = (peak_val - v) / peak_val
        if dd > depth:
            depth, peak, trough = dd, peak_day, day
    if depth == 0:
        return {"depth": Decimal(0), "peak": None, "trough": None, "recovery": None}
    top = next(v for day, v in index if day == peak)
    recovery = next((day for day, v in index if day > trough and v >= top), None)
    return {"depth": _q(depth), "peak": peak, "trough": trough, "recovery": recovery}


def series_metrics(series, risk_free):
    """twr, twr_annualized, max_drawdown, volatility, sharpe (None = not computable)."""
    none = {"twr": None, "twr_annualized": None, "max_drawdown": None,
            "volatility": None, "sharpe": None}
    start = next((p.day for p in series if p.value > 0), None)
    if start is None:
        return none
    with localcontext(DECIMAL_CONTEXT):
        rets = daily_returns(series)
        idx, index = Decimal(1), [(start, Decimal(1))]
        for day, r in rets:
            idx *= 1 + r
            index.append((day, idx))
        out = dict(none, twr=_q(idx - 1), max_drawdown=_drawdown(index))
        first_flow = next(p.day for p in series if p.contributed > 0)
        span = (series[-1].day - first_flow).days
        if span > 0:
            out["twr_annualized"] = (Decimal(-1) if idx <= 0 else
                                     _q(idx ** (TWR_YEAR / span) - 1))
        rs = [r for _, r in rets]
        if len(rs) >= 2:
            mean = sum(rs, Decimal(0)) / len(rs)
            std = (sum(((r - mean) ** 2 for r in rs), Decimal(0)) / (len(rs) - 1)).sqrt()
            root = TRADING_DAYS.sqrt()
            out["volatility"] = _q(std * root)
            if std > 0:
                out["sharpe"] = _q((mean - risk_free / TRADING_DAYS) / std * root)
        return out


# ---- the event log ----------------------------------------------------------------

def _convert(amount, frm, to, rate):
    if frm == to:
        return amount
    return amount * rate if frm == "USD" else amount / rate


def event_metrics(events, base):
    """Trade count and costs in base currency. Slippage is logged in the
    symbol's currency and converted at that day's rate (every foreign trade
    logs an fx event the same day, at the same rate)."""
    rates = {e.day: e.detail["rate"] for e in events if e.kind == "fx"}
    out = {"trades": 0, "commissions": Decimal(0), "slippage": Decimal(0),
           "costs": Decimal(0), "fx_paid": Decimal(0), "dividends": Decimal(0),
           "sold": Decimal(0)}
    with localcontext(DECIMAL_CONTEXT):
        for e in events:
            det = e.detail
            if e.kind in ("buy", "sell"):
                out["trades"] += 1
                out["commissions"] += det["commission"]
                # A DRIP buy has zero slippage and no same-day fx event, so
                # there is no rate to convert with (and nothing to convert).
                if det["slippage"]:
                    out["slippage"] += _convert(det["slippage"], det["currency"], base,
                                                rates.get(e.day))
                if e.kind == "sell":
                    out["sold"] += det["proceeds_base"] + det["commission"]
            elif e.kind == "fx":
                out["fx_paid"] += det["fee"]
            elif e.kind == "dividend":
                out["dividends"] += det["net_base"]
        out["slippage"] = _q_cash(out["slippage"])
        out["costs"] = out["commissions"] + out["slippage"]
    return out


def pick_timeline(events):
    """[{day, bought, held}] on each trading day, logged only when it differs
    from the previous entry. `bought` is what that day's allocation bought
    (for keep, the pick; rotate and rebalance trade only the difference, so
    `held` is what the strategy then owned). Dividend reinvestment is not a
    pick and never changes what is held."""
    shares, per_day = {}, {}   # per_day: day -> (bought, held after that day's trades)
    for e in events:           # events are in day order
        if e.kind not in ("buy", "sell"):
            continue
        sign = 1 if e.kind == "buy" else -1
        shares[e.symbol] = shares.get(e.symbol, Decimal(0)) + sign * e.detail["shares"]
        if e.detail.get("source") == "dividend":
            continue  # only tops up a symbol already held
        bought = per_day.get(e.day, (set(), None))[0]
        if e.kind == "buy":
            bought.add(e.symbol)
        per_day[e.day] = (bought, sorted(s for s, q in shares.items() if q > 0))
    out, last = [], None
    for day in sorted(per_day):
        entry = (sorted(per_day[day][0]), per_day[day][1])
        if entry != last:
            out.append({"day": day, "bought": entry[0], "held": entry[1]})
            last = entry
    return out


def turnover(sold, series):
    """Annual turnover: base-currency sales / average value / years held."""
    if sold == 0:
        return Decimal(0)
    values = [p.value for p in series if p.value > 0]
    first_flow = next((p.day for p in series if p.contributed > 0), None)
    if not values or first_flow is None:
        return None
    span = (series[-1].day - first_flow).days
    if span <= 0:
        return None
    with localcontext(DECIMAL_CONTEXT):
        mean = sum(values, Decimal(0)) / len(values)
        return _q(sold / mean / (Decimal(span) / TWR_YEAR))


def _max_single_stock(max_weights):
    best = None
    for sym in sorted(max_weights):
        w = max_weights[sym]
        if is_individual_stock(sym) and (best is None or w["weight"] > best["weight"]):
            best = {"weight": w["weight"], "symbol": sym, "day": w["day"]}
    return best


def strategy_metrics(spec, run):
    """(metrics, xirr_reason | None) for one simulate.StrategyRun."""
    s = run.result.series
    final = s[-1].value if s else Decimal(0)
    contributed = s[-1].contributed if s else Decimal(0)
    rate, reason = xirr(flows(s))
    agg = event_metrics(run.result.events, spec.base_currency)
    sold = agg.pop("sold")
    m = {"final_value": final, "contributed": contributed, "profit": final - contributed,
         "xirr": rate, "risk_free": spec.risk_free, "turnover": turnover(sold, s),
         "max_weight": run.max_weight, "max_single_stock_weight": _max_single_stock(
             run.max_weights),
         "pick_timeline": pick_timeline(run.result.events)}
    m.update(series_metrics(s, spec.risk_free))
    m.update(agg)
    return m, reason
