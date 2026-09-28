"""Caveats printed in every report (design 35), generated from the spec,
the dataset and the run; none can be turned off.

Order is fixed: data, taxes, hindsight, then per strategy (spec order)
concentration and any metric that could not be computed.
"""
from decimal import Decimal

from .result import Caveat
from .spec import Fixed, Rank, _walk

# Broad index funds: holding one is not a bet on a single company, so it
# neither triggers the hindsight caveat nor counts as a single stock for the
# 40% concentration rule. Anything not listed is treated as an individual
# stock, so an unknown ETF over-warns rather than under-warns.
BROAD_INDEX_FUNDS = frozenset({
    "SPY", "VOO", "IVV", "VTI", "ITOT", "SCHB", "QQQ", "QQQM", "DIA", "IWM", "IWB", "RSP",
    "VT", "ACWI", "VXUS", "VEA", "VWO", "EFA", "EEM", "AGG", "BND",
    "XIU.TO", "XIC.TO", "VCN.TO", "ZCN.TO", "HXT.TO", "VFV.TO", "ZSP.TO", "XUS.TO",
    "XSP.TO", "VUN.TO", "XUU.TO", "ZQQ.TO", "XQQ.TO", "XEQT.TO", "VEQT.TO", "VGRO.TO",
    "XGRO.TO", "ZEQT.TO", "XAW.TO", "VXC.TO", "XBB.TO", "ZAG.TO", "VAB.TO",
})
SINGLE_STOCK_LIMIT = Decimal("0.4")
PICK_LIMIT = 2

HINDSIGHT = ("universe chosen with today's knowledge; winners picked in hindsight inflate "
             "results. There is no point-in-time index membership; individual stocks in "
             "this spec: {symbols}.")
TAXES_NONE = ("Taxes are ignored: no dividend withholding, capital-gains or income tax, and "
              "account type (TFSA, RRSP or taxable) is not modelled.")
TAXES_WITHHOLDING = ("Dividends are reduced by {pct}% withholding tax; no other tax is "
                     "modelled, and account type (TFSA, RRSP or taxable) is not modelled.")
DATA = ("Yahoo Finance data through yfinance, unaudited. Dataset sha256 {sha}, bars {first} "
        "to {last}, {fetched}.")
CONCENTRATION_PICK = ('Strategy "{label}" picks only the {pick} {n}; its largest '
                      "single-holding weight was {pct}% ({symbol} on {day}). See its pick "
                      "timeline beside the returns.")
CONCENTRATION_STOCK = ('Strategy "{label}" held a single stock above 40%; its largest '
                       "single-stock weight was {pct}% ({symbol} on {day}). See its pick "
                       "timeline beside the returns.")
XIRR = 'Strategy "{label}": the money-weighted return (XIRR) is not shown: {reason}.'


def is_individual_stock(symbol):
    return symbol not in BROAD_INDEX_FUNDS and "=" not in symbol and not symbol.startswith("^")


def _traded(strategy):
    out = set()
    for a in _walk(strategy.allocate):
        if isinstance(a, Fixed):
            out |= {s for s, _ in a.weights}
        elif isinstance(a, Rank):
            out |= set(a.universe)
    if strategy.holdings.to:
        out |= {s for s, _ in strategy.holdings.to}
    return out


def _pct(weight):
    return format((weight * 100).quantize(Decimal("0.1")), "f")


def _fmt_day(day):
    return day if isinstance(day, str) else day.isoformat()


def _concentration(strategy, run):
    ranks = [a for a in _walk(strategy.allocate) if isinstance(a, Rank) and a.n <= PICK_LIMIT]
    stocks = {s: w for s, w in run.max_weights.items() if is_individual_stock(s)}
    if ranks:
        r = min(ranks, key=lambda a: (a.n, a.pick))
        top = run.max_weight
        if top is None:
            return None
        return CONCENTRATION_PICK.format(label=strategy.label, pick=r.pick, n=r.n,
                                         pct=_pct(top["weight"]), symbol=top["symbol"],
                                         day=_fmt_day(top["day"]))
    best = None
    for sym in sorted(stocks):
        if best is None or stocks[sym]["weight"] > stocks[best]["weight"]:
            best = sym
    if best is None or stocks[best]["weight"] <= SINGLE_STOCK_LIMIT:
        return None
    return CONCENTRATION_STOCK.format(label=strategy.label, pct=_pct(stocks[best]["weight"]),
                                      symbol=best, day=_fmt_day(stocks[best]["day"]))


def _data_span(dataset):
    days = [dataset.days(s) for s in dataset.symbols() if dataset.days(s)]
    if not days:
        return "none", "none"
    return (min(d[0] for d in days).isoformat(), max(d[-1] for d in days).isoformat())


def build(spec, dataset, dataset_sha, runs, xirr_reasons, fetched=None):
    """Caveats for a run. `runs` are simulate.StrategyRun in spec order;
    `xirr_reasons` maps strategy id -> why XIRR is missing; `fetched` is the
    pinned dataset's (first, last) fetch date, or None when not recorded."""
    first, last = _data_span(dataset)
    when = ("fetched " + " to ".join(_fmt_day(x) for x in fetched) if fetched
            else "fetch dates not recorded")
    out = [Caveat("data", DATA.format(sha=dataset_sha, first=first, last=last, fetched=when))]
    pct = spec.dividends.withholding_pct
    out.append(Caveat("taxes", TAXES_WITHHOLDING.format(pct=format(pct.normalize(), "f"))
                      if pct else TAXES_NONE))
    stocks = sorted({s for st in spec.strategies for s in _traded(st)
                     if is_individual_stock(s)})
    if stocks:
        out.append(Caveat("hindsight", HINDSIGHT.format(symbols=", ".join(stocks))))
    for strategy, run in zip(spec.strategies, runs):
        text = _concentration(strategy, run)
        if text:
            out.append(Caveat("concentration", text))
        if strategy.id in xirr_reasons:
            out.append(Caveat("xirr", XIRR.format(label=strategy.label,
                                                  reason=xirr_reasons[strategy.id])))
    return tuple(out)
