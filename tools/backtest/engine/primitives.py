"""The v1 primitive registry: the single source of the spec grammar.

`describe_primitives()` (the grammar the model writes against), the parser's
defaults and "unknown primitive" errors, and the sentence templates behind
`describe()` all read from here, so the three cannot drift apart. A new
primitive lands as a registry entry plus its parser branch, engine function,
golden test, and a use in one of `EXAMPLES` (a test enforces the last).
"""
from dataclasses import dataclass
from decimal import Decimal

SPEC_LANGUAGE = "backtest-spec/v1"

MAX_SYMBOLS = 25
MAX_STRATEGIES = 8
MAX_PERIOD_YEARS = 30
MAX_DAILY_PERIOD_YEARS = 5
MAX_LOOKBACK_DAYS = 400
MAX_AMOUNT = Decimal(10) ** 9
MAX_WHEN_DEPTH = 3

# Lookbacks are counted in the symbol's own trading days, so "1mo" means the
# same number of bars on every market and every date.
LOOKBACK_UNITS = {"d": 1, "w": 5, "mo": 21, "y": 252}

FREQUENCIES = ("day", "week", "month", "quarter", "year")
ANCHORS = ("first_trading_day", "last_trading_day")
OPS = (">", ">=", "<", "<=")
CURRENCIES = ("CAD", "USD")


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    doc: str
    required: bool = True
    default: object = None
    minimum: object = None
    maximum: object = None
    choices: tuple = ()

    def to_dict(self):
        d = {"name": self.name, "type": self.type, "doc": self.doc,
             "required": self.required}
        for key in ("default", "minimum", "maximum"):
            val = getattr(self, key)
            if val is not None:
                d[key] = str(val) if isinstance(val, Decimal) else val
        if self.choices:
            d["choices"] = list(self.choices)
        return d


@dataclass(frozen=True)
class Primitive:
    name: str
    family: str
    doc: str
    params: tuple
    template: str
    example: dict
    unit: str | None = None  # signals: "fraction" (printed as %) or "price"

    def param(self, name):
        return next(p for p in self.params if p.name == name)

    def to_dict(self):
        d = {"name": self.name, "family": self.family, "doc": self.doc,
             "params": [p.to_dict() for p in self.params],
             "template": self.template, "example": self.example}
        if self.unit:
            d["unit"] = self.unit
        return d


def _window(name, doc, minimum=1):
    return Param(name, "int", doc, minimum=minimum, maximum=MAX_LOOKBACK_DAYS)


_AMOUNT_DOC = f"money in base_currency, > 0 and <= {MAX_AMOUNT}, at most 2 decimals"

REGISTRY = (
    # -- money in -------------------------------------------------------------
    Primitive(
        "contributions", "money_in",
        "Recurring deposits from period.start to period.end. `on` picks the day "
        "inside each period: first_trading_day, last_trading_day, or {day_n: N} "
        "= the first trading day on or after calendar day N (week: weekday, "
        "1=Monday..5=Friday). every: day deposits on every trading day and takes "
        f"no `on`; it is allowed only for periods up to {MAX_DAILY_PERIOD_YEARS} years.",
        (Param("amount", "decimal", _AMOUNT_DOC),
         Param("every", "enum", "deposit frequency", choices=FREQUENCIES),
         Param("on", "anchor", "day inside each period", required=False,
               default="first_trading_day")),
        "Contribute {amount} {currency} {schedule}.",
        {"contributions": {"amount": 1000, "every": "month", "on": "first_trading_day"}}),
    Primitive(
        "lump_sum", "money_in",
        "One deposit, invested on the first trading day on or after `on` "
        "(default period.start). Combines with contributions.",
        (Param("amount", "decimal", _AMOUNT_DOC),
         Param("on", "date", "YYYY-MM-DD inside the period", required=False,
               default="period.start")),
        "Invest a lump sum of {amount} {currency} on the first trading day on or after {on}.",
        {"lump_sum": {"amount": 50000}}),
    # -- signals: computed per symbol from split-adjusted closes strictly
    # before the event day; `window` bars of history are required ------------
    Primitive(
        "trailing_return", "signal",
        "Price return over the last `lookback` trading days: last close before "
        "the event day / the close `lookback` bars earlier - 1. Lookback is N "
        "followed by d (trading days), w (5), mo (21) or y (252).",
        (Param("lookback", "lookback", "e.g. 21d, 1mo, 3mo, 1y",
               maximum=MAX_LOOKBACK_DAYS),),
        "trailing {lookback} return",
        {"trailing_return": {"lookback": "1mo"}}, unit="fraction"),
    Primitive(
        "sma", "signal",
        "Simple moving average of the last n closes, in the symbol's currency.",
        (_window("n", "number of trading days"),),
        "{n}-day simple moving average",
        {"sma": {"n": 50}}, unit="price"),
    Primitive(
        "price_vs_sma", "signal",
        "Last close / the n-day simple moving average - 1 (0.05 = 5% above it).",
        (_window("n", "number of trading days", minimum=2),),
        "price vs its {n}-day simple moving average",
        {"price_vs_sma": {"n": 200}}, unit="fraction"),
    Primitive(
        "volatility", "signal",
        "Sample standard deviation of the last n daily price returns, x sqrt(252).",
        (_window("n", "number of daily returns", minimum=2),),
        "{n}-day annualized volatility",
        {"volatility": {"n": 60}}, unit="fraction"),
    Primitive(
        "drawdown_from_high", "signal",
        "Last close / the highest of the last n closes - 1 (always <= 0).",
        (_window("n", "number of trading days"),),
        "drawdown from its {n}-day high",
        {"drawdown_from_high": {"n": 252}}, unit="fraction"),
    # -- allocators: where the money of an event goes -----------------------
    Primitive(
        "fixed", "allocator",
        "Fixed weights: the body is a map of symbol to weight; each weight > 0 "
        "and the weights sum to exactly 1.",
        (Param("weights", "weights", "{SYMBOL: weight, ...}"),),
        "allocate to {weights}",
        {"fixed": {"SPY": "0.6", "XIU.TO": "0.4"}}),
    Primitive(
        "rank", "allocator",
        "Rank the universe by a signal at each event and split equally across "
        "the top (highest) or bottom (lowest) N. Symbols without enough history "
        "for the signal are excluded (logged); if none qualify the money is held "
        "as cash (logged). Ties break alphabetically.",
        (Param("universe", "symbols", f"1..{MAX_SYMBOLS} symbols"),
         Param("signal", "signal", "a signal primitive, e.g. {sma: {n: 50}}"),
         Param("pick", "pick", "{top: N} or {bottom: N}, 1 <= N <= len(universe)")),
        "allocate equally to the {count} with the {direction} {signal} among {universe}",
        {"rank": {"universe": ["QQQ", "SPY", "XIU.TO"],
                  "signal": {"trailing_return": {"lookback": "1mo"}},
                  "pick": {"top": 1}}}),
    Primitive(
        "when", "allocator",
        "Evaluate `signal` on `symbol` at each event; if `signal op threshold` "
        "holds use `then`, otherwise `else` (an allocator, or \"cash\" = hold "
        "the money as cash). Fraction signals take fractions (0.05 = 5%). "
        f"Nesting depth at most {MAX_WHEN_DEPTH}.",
        (Param("symbol", "symbol", "the symbol the signal is computed on"),
         Param("signal", "signal", "a signal primitive"),
         Param("op", "enum", "comparison", choices=OPS),
         Param("threshold", "decimal", "number compared against the signal"),
         Param("then", "allocator", "allocator used while the condition holds"),
         Param("else", "allocator_or_cash", "allocator or \"cash\"", required=False,
               default="cash")),
        "{then} while {symbol}'s {signal} is {op} {threshold}, otherwise {else}",
        {"when": {"symbol": "SPY", "signal": {"price_vs_sma": {"n": 200}},
                  "op": ">", "threshold": 0, "then": {"fixed": {"SPY": 1}},
                  "else": "cash"}}),
    # -- holdings: what happens to money already invested -------------------
    Primitive(
        "keep", "holdings",
        "Never sell: only new money follows the allocation (the default).",
        (), "existing holdings are kept, so only new money follows the allocation",
        {"holdings": "keep"}),
    Primitive(
        "rotate", "holdings",
        "At every money-in event, sell everything and buy the allocation with "
        "the proceeds plus the new money.",
        (), "at every event all holdings are sold into the allocation",
        {"holdings": "rotate"}),
    Primitive(
        "rebalance", "holdings",
        "On the first trading day of every `every` period, trade the whole "
        "portfolio back to `to`: \"allocation\" (the strategy's allocate, "
        "evaluated that day) or a fixed weights map.",
        (Param("every", "enum", "rebalance frequency", choices=FREQUENCIES),
         Param("to", "weights_or_allocation", "\"allocation\" or {SYMBOL: weight}",
               required=False, default="allocation")),
        "holdings are rebalanced to {to} {schedule}",
        {"holdings": {"rebalance": {"every": "quarter", "to": "allocation"}}}),
    # -- costs ---------------------------------------------------------------
    Primitive(
        "costs", "costs",
        "Trading costs: a flat commission per trade in base_currency, slippage "
        "added to (buys) or taken from (sells) every fill price, and a spread on "
        "every currency conversion.",
        (Param("commission", "decimal", "per trade, base_currency", required=False,
               default=Decimal(0), minimum=Decimal(0), maximum=Decimal(10000)),
         Param("slippage_bps", "decimal", "basis points", required=False,
               default=Decimal(0), minimum=Decimal(0), maximum=Decimal(1000)),
         Param("fx_bps", "decimal", "basis points", required=False,
               default=Decimal(0), minimum=Decimal(0), maximum=Decimal(1000))),
        "Costs: {commission} {currency} commission per trade, {slippage_bps} bps "
        "slippage on every fill and {fx_bps} bps on every currency conversion.",
        {"costs": {"commission": 0, "slippage_bps": 5, "fx_bps": 25}}),
    # -- dividends -------------------------------------------------------------
    Primitive(
        "reinvest", "dividends",
        "Dividends are paid per share held on the ex-date, converted to base "
        "currency if needed, and reinvested in the same symbol at that close. "
        "Written dividends: {mode: reinvest, withholding_pct: N} or just \"reinvest\".",
        (Param("withholding_pct", "decimal", "percent withheld (0-100)",
               required=False, default=Decimal(0), minimum=Decimal(0),
               maximum=Decimal(100)),),
        "Dividends{withholding} are reinvested at the ex-date close.",
        {"dividends": {"mode": "reinvest", "withholding_pct": 0}}),
    Primitive(
        "cash", "dividends",
        "Dividends are paid on the ex-date and held as cash (never invested).",
        (Param("withholding_pct", "decimal", "percent withheld (0-100)",
               required=False, default=Decimal(0), minimum=Decimal(0),
               maximum=Decimal(100)),),
        "Dividends{withholding} are held as cash.",
        {"dividends": {"mode": "cash", "withholding_pct": 15}}),
)

BY_NAME = {p.name: p for p in REGISTRY}


def names(family):
    return sorted(p.name for p in REGISTRY if p.family == family)


# Top-level and per-strategy fields that are not primitives themselves.
SPEC_FIELDS = (
    Param("name", "string", "1-80 characters, shown in the UI"),
    Param("period", "period", "{start: YYYY-MM-DD, end: YYYY-MM-DD}; "
          f"end after start, at most {MAX_PERIOD_YEARS} years"),
    Param("base_currency", "enum", "currency money arrives in and values are "
          "reported in; CAD=X converts", required=False, default="CAD",
          choices=CURRENCIES),
    Param("contributions", "contributions", "see money_in; at least one of "
          "contributions and lump_sum", required=False),
    Param("lump_sum", "lump_sum", "see money_in", required=False),
    Param("execution", "execution", "{decide: prior_close, fill: close, "
          "whole_shares: false}; decide/fill have one v1 value each; whole_shares "
          "true buys whole shares and leaves the remainder as cash",
          required=False, default="{decide: prior_close, fill: close, whole_shares: false}"),
    Param("dividends", "dividends", "see dividends", required=False,
          default="{mode: reinvest, withholding_pct: 0}"),
    Param("costs", "costs", "see costs", required=False,
          default="{commission: 0, slippage_bps: 0, fx_bps: 0}"),
    Param("risk_free", "decimal", "annual risk-free rate as a fraction for "
          "Sharpe (0.03 = 3%)", required=False, default=Decimal(0),
          minimum=Decimal(-1), maximum=Decimal(1)),
    Param("ties", "enum", "how equal signal values are ordered", required=False,
          default="alphabetical", choices=("alphabetical",)),
    Param("strategies", "strategies", f"1-{MAX_STRATEGIES} of {{id, label, "
          "allocate, holdings}}: id matches [a-z0-9][a-z0-9_-]{0,31}; label "
          "defaults to id; allocate is an allocator; holdings defaults to keep"),
    Param("benchmark", "string", "a strategy id; defaults to the first strategy",
          required=False, default="strategies[0].id"),
)

RULES = (
    "A primitive with parameters is written {name: {param: value}}; keep and "
    "rotate are bare strings; fixed's body is the weights map itself.",
    "Numbers may be JSON numbers or decimal strings; unknown fields are errors.",
    "Every default you leave out is filled and listed in `assumed`.",
    "Symbols are Yahoo tickers in upper case (XIU.TO, BRK-B); FX pairs (=X) "
    "and indexes (^) may be `when` symbols but are never traded.",
    "A decision on day t sees only closes strictly before t; trades fill at "
    "t's close plus slippage. Events fall on the base currency's market "
    "calendar (TSX for CAD, US for USD).",
    "A question that does not compose from these primitives is not supported: "
    "say so instead of approximating it.",
)

EXAMPLES = (
    {"name": "qqq-dca-vs-1m-winner",
     "period": {"start": "2016-10-01", "end": "2026-09-01"},
     "contributions": {"amount": 1000, "every": "month", "on": "first_trading_day"},
     "costs": {"commission": 0, "slippage_bps": 5, "fx_bps": 25},
     "strategies": [
         {"id": "qqq", "allocate": {"fixed": {"QQQ": 1}}},
         {"id": "winner", "allocate": {"rank": {
             "universe": ["QQQ", "SPY", "XIU.TO"],
             "signal": {"trailing_return": {"lookback": "1mo"}},
             "pick": {"top": 1}}}, "holdings": "keep"}],
     "benchmark": "qqq"},
    {"name": "lump-60-40-rebalanced",
     "period": {"start": "2015-01-02", "end": "2025-01-02"},
     "lump_sum": {"amount": 50000},
     "dividends": {"mode": "reinvest", "withholding_pct": 0},
     "strategies": [
         {"id": "sixty-forty", "allocate": {"fixed": {"SPY": "0.6", "XIU.TO": "0.4"}},
          "holdings": {"rebalance": {"every": "quarter", "to": "allocation"}}},
         {"id": "all-spy", "allocate": {"fixed": {"SPY": 1}}}]},
    {"name": "spy-trend-filter",
     "period": {"start": "2010-01-01", "end": "2025-01-01"},
     "base_currency": "USD",
     "contributions": {"amount": 500, "every": "month"},
     "dividends": {"mode": "cash", "withholding_pct": 15},
     "strategies": [
         {"id": "trend", "allocate": {"when": {
             "symbol": "SPY", "signal": {"price_vs_sma": {"n": 200}},
             "op": ">", "threshold": 0, "then": {"fixed": {"SPY": 1}},
             "else": "cash"}}, "holdings": "rotate"}]},
    {"name": "defensive-rank",
     "period": {"start": "2012-01-01", "end": "2024-01-01"},
     "contributions": {"amount": 250, "every": "week", "on": {"day_n": 1}},
     "strategies": [
         {"id": "low-vol", "allocate": {"rank": {
             "universe": ["QQQ", "SPY", "TLT", "XIU.TO"],
             "signal": {"volatility": {"n": 60}}, "pick": {"bottom": 2}}}},
         {"id": "dip", "allocate": {"when": {
             "symbol": "QQQ", "signal": {"drawdown_from_high": {"n": 252}},
             "op": "<=", "threshold": "-0.1", "then": {"fixed": {"QQQ": 1}},
             "else": {"rank": {"universe": ["SPY", "TLT"],
                               "signal": {"sma": {"n": 50}}, "pick": {"top": 1}}}}}}]},
)


def describe_primitives():
    """The grammar for the model, generated from the registry above."""
    families = {}
    for p in REGISTRY:
        families.setdefault(p.family, []).append(p.to_dict())
    return {
        "language": SPEC_LANGUAGE,
        "rules": list(RULES),
        "fields": [f.to_dict() for f in SPEC_FIELDS],
        "families": families,
        "bounds": {
            "max_symbols": MAX_SYMBOLS,
            "max_strategies": MAX_STRATEGIES,
            "max_period_years": MAX_PERIOD_YEARS,
            "max_period_years_for_every_day": MAX_DAILY_PERIOD_YEARS,
            "max_lookback_trading_days": MAX_LOOKBACK_DAYS,
            "max_amount": str(MAX_AMOUNT),
            "max_when_depth": MAX_WHEN_DEPTH,
        },
        "lookback_units": dict(LOOKBACK_UNITS),
        "examples": [dict(e) for e in EXAMPLES],
    }
