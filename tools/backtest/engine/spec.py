"""Spec model, validation, canonical form and ids.

`validate(raw)` turns an untrusted JSON dict into frozen dataclasses with
every default filled in, recording each filled default in `assumed`.
Problems come back as a list of {path, message}, never as one exception
string, so the model can fix them all in one round.
"""
import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation

from . import primitives as P

# Engine-wide number rules (T4/T5 use these, never the ambient context).
DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
SHARES_Q = Decimal("0.00000001")
CASH_Q = Decimal("0.01")

# Same alphabet as tools/prices SYMBOL_RE, but upper case is required here:
# silently upper-casing would make the hash depend on a rewrite.
SYMBOL_RE = re.compile(r"^[A-Z0-9.^=-]{1,12}$")
STRATEGY_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LOOKBACK_RE = re.compile(r"^([1-9][0-9]{0,3})(d|w|mo|y)$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


# ---- the model ---------------------------------------------------------------

@dataclass(frozen=True)
class Period:
    start: date
    end: date


@dataclass(frozen=True)
class Anchor:
    kind: str            # first_trading_day | last_trading_day | day_n
    n: int | None = None  # day_n: calendar day (weekday 1-5 for every: week)

    def to_json(self):
        return {"day_n": self.n} if self.kind == "day_n" else self.kind


@dataclass(frozen=True)
class Contributions:
    amount: Decimal
    every: str
    on: Anchor


@dataclass(frozen=True)
class LumpSum:
    amount: Decimal
    on: date


@dataclass(frozen=True)
class Execution:
    decide: str
    fill: str
    whole_shares: bool


@dataclass(frozen=True)
class Dividends:
    mode: str  # reinvest | cash
    withholding_pct: Decimal


@dataclass(frozen=True)
class Costs:
    commission: Decimal
    slippage_bps: Decimal
    fx_bps: Decimal


@dataclass(frozen=True)
class Signal:
    name: str
    params: tuple        # ((param, value), ...) as written, for the canonical form
    window: int          # bars of history before the event day it needs

    def to_json(self):
        return {self.name: dict(self.params)}


@dataclass(frozen=True)
class Fixed:
    weights: tuple  # ((symbol, Decimal), ...) sorted by symbol

    def to_json(self):
        return {"fixed": dict(self.weights)}


@dataclass(frozen=True)
class Rank:
    universe: tuple  # sorted symbols
    signal: Signal
    pick: str        # top | bottom
    n: int

    def to_json(self):
        return {"rank": {"universe": list(self.universe), "signal": self.signal.to_json(),
                         "pick": {self.pick: self.n}}}


@dataclass(frozen=True)
class When:
    symbol: str
    signal: Signal
    op: str
    threshold: Decimal
    then: "Fixed | Rank | When"
    else_: "Fixed | Rank | When | None"  # None = hold cash

    def to_json(self):
        return {"when": {"symbol": self.symbol, "signal": self.signal.to_json(),
                         "op": self.op, "threshold": self.threshold,
                         "then": self.then.to_json(),
                         "else": "cash" if self.else_ is None else self.else_.to_json()}}


@dataclass(frozen=True)
class Holdings:
    mode: str                  # keep | rotate | rebalance
    every: str | None = None   # rebalance only
    to: tuple | None = None    # rebalance only; None = the strategy's allocation

    def to_json(self):
        if self.mode != "rebalance":
            return self.mode
        to = "allocation" if self.to is None else dict(self.to)
        return {"rebalance": {"every": self.every, "to": to}}


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    allocate: "Fixed | Rank | When"
    holdings: Holdings


@dataclass(frozen=True)
class Spec:
    name: str
    period: Period
    base_currency: str
    contributions: Contributions | None
    lump_sum: LumpSum | None
    execution: Execution
    dividends: Dividends
    costs: Costs
    risk_free: Decimal
    ties: str
    strategies: tuple
    benchmark: str

    def to_dict(self):
        """The normalized spec, every default explicit; JSON-ready."""
        c, ls = self.contributions, self.lump_sum
        return jsonable({
            "name": self.name,
            "period": {"start": self.period.start, "end": self.period.end},
            "base_currency": self.base_currency,
            "contributions": None if c is None else {
                "amount": c.amount, "every": c.every, "on": c.on.to_json()},
            "lump_sum": None if ls is None else {"amount": ls.amount, "on": ls.on},
            "execution": dataclasses.asdict(self.execution),
            "dividends": dataclasses.asdict(self.dividends),
            "costs": dataclasses.asdict(self.costs),
            "risk_free": self.risk_free,
            "ties": self.ties,
            "strategies": [{"id": s.id, "label": s.label, "allocate": s.allocate.to_json(),
                            "holdings": s.holdings.to_json()} for s in self.strategies],
            "benchmark": self.benchmark,
        })

    def symbols(self):
        """Every symbol the engine needs bars for (traded or signal-only), sorted."""
        out = set()
        for s in self.strategies:
            out |= _alloc_symbols(s.allocate)
            if s.holdings.to:
                out |= {sym for sym, _ in s.holdings.to}
        return tuple(sorted(out))

    def max_window(self):
        """Longest signal history (trading days before the first event) needed."""
        return max((sig.window for s in self.strategies for sig in _signals(s.allocate)),
                   default=0)

    def primitives_used(self):
        used = {"costs", self.dividends.mode}
        if self.contributions:
            used.add("contributions")
        if self.lump_sum:
            used.add("lump_sum")
        for s in self.strategies:
            used.add(s.holdings.mode)
            used |= _alloc_names(s.allocate)
        return used


def _walk(alloc):
    yield alloc
    if isinstance(alloc, When):
        yield from _walk(alloc.then)
        if alloc.else_ is not None:
            yield from _walk(alloc.else_)


def _signals(alloc):
    for a in _walk(alloc):
        if isinstance(a, (Rank, When)):
            yield a.signal


def _alloc_symbols(alloc):
    out = set()
    for a in _walk(alloc):
        if isinstance(a, Fixed):
            out |= {sym for sym, _ in a.weights}
        elif isinstance(a, Rank):
            out |= set(a.universe)
        else:
            out.add(a.symbol)
    return out


def _alloc_names(alloc):
    names = set()
    for a in _walk(alloc):
        names.add({Fixed: "fixed", Rank: "rank", When: "when"}[type(a)])
    names |= {sig.name for sig in _signals(alloc)}
    return names


# ---- canonical form ------------------------------------------------------------

def decimal_str(d):
    """One spelling per value: 1000, 1000.0 and 1E+3 all become "1000"."""
    if not d.is_finite():
        raise ValueError(f"non-finite decimal {d}")
    if d == 0:
        return "0"
    return format(d.normalize(DECIMAL_CONTEXT), "f")


def jsonable(obj):
    """Plain JSON types: Decimals as canonical strings, dates ISO, no floats."""
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in canonical output; use Decimal")
    if isinstance(obj, Decimal):
        return decimal_str(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonable(v) for v in obj]
    if hasattr(obj, "to_dict"):
        return jsonable(obj.to_dict())
    raise TypeError(f"not canonicalizable: {type(obj).__name__}")


def canonical_json(obj):
    return json.dumps(jsonable(obj), sort_keys=True, separators=(",", ":"))


def spec_hash(spec):
    return hashlib.sha256(canonical_json(spec).encode()).hexdigest()


def experiment_id(spec, dataset_sha, engine_version):
    payload = canonical_json({"spec": spec, "dataset_sha": dataset_sha,
                              "engine_version": engine_version})
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


# ---- validation ------------------------------------------------------------------

@dataclass(frozen=True)
class Issue:
    path: str
    message: str

    def to_dict(self):
        return {"path": self.path, "message": self.message}


@dataclass(frozen=True)
class Assumption:
    path: str
    value: object  # canonical JSON value

    def to_dict(self):
        return {"path": self.path, "value": self.value}


@dataclass(frozen=True)
class Validation:
    spec: Spec | None
    errors: tuple
    assumed: tuple

    @property
    def ok(self):
        return not self.errors

    def to_dict(self):
        return {"spec": None if self.spec is None else self.spec.to_dict(),
                "errors": [e.to_dict() for e in self.errors],
                "assumed": [a.to_dict() for a in self.assumed]}


class SpecError(ValueError):
    def __init__(self, errors):
        self.errors = tuple(errors)
        super().__init__("; ".join(f"{e.path}: {e.message}" for e in self.errors))


def validate(raw):
    return _Parser().parse(raw)


def parse_spec(raw):
    v = validate(raw)
    if not v.ok:
        raise SpecError(v.errors)
    return v.spec


def _join(path, key):
    return f"{path}.{key}" if path else key


def _add_years(d, years):
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28)


_MISSING = object()


class _Parser:
    def __init__(self):
        self.errors = []
        self.assumed = []
        self.symbols = set()
        self.daily_rebalance = []

    def err(self, path, message):
        self.errors.append(Issue(path, message))
        return None

    def assume(self, path, value):
        self.assumed.append(Assumption(path, jsonable(value)))
        return value

    # -- generic helpers -------------------------------------------------------

    def obj(self, v, path, allowed):
        if not isinstance(v, dict):
            return self.err(path, "must be an object")
        for k in v:
            if k not in allowed:
                self.err(_join(path, str(k)),
                         f"unknown field {str(k)!r}; allowed: {', '.join(allowed)}")
        return v

    def get(self, d, key, path, default=_MISSING):
        """d[key], or the default (recorded as assumed); _MISSING if absent and required."""
        if key in d and d[key] is not None:
            return d[key]
        if default is _MISSING:
            self.err(_join(path, key), "is required")
            return _MISSING
        return self.assume(_join(path, key), default)

    def text(self, v, path, max_len):
        if not isinstance(v, str) or not v.strip() or len(v) > max_len:
            return self.err(path, f"must be a non-empty string of at most {max_len} characters")
        if _CONTROL_RE.search(v):
            return self.err(path, "must not contain control characters")
        return v

    def enum(self, v, path, choices):
        if v not in choices:
            return self.err(path, f"must be one of: {', '.join(choices)}")
        return v

    def integer(self, v, path, lo, hi):
        if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
            return self.err(path, f"must be an integer from {lo} to {hi}")
        return v

    def decimal(self, v, path, lo=None, hi=None, *, positive=False, places=None):
        if isinstance(v, bool):
            return self.err(path, "must be a number")
        try:
            if isinstance(v, (int, Decimal)):
                d = Decimal(v)
            elif isinstance(v, float):
                d = Decimal(repr(v))
            elif isinstance(v, str):
                d = Decimal(v.strip())
            else:
                return self.err(path, "must be a number")
        except InvalidOperation:
            return self.err(path, "must be a number")
        if not d.is_finite():
            return self.err(path, "must be a finite number")
        if positive and d <= 0:
            return self.err(path, "must be greater than 0")
        if lo is not None and d < lo:
            return self.err(path, f"must be at least {decimal_str(lo)}")
        if hi is not None and d > hi:
            return self.err(path, f"must be at most {decimal_str(hi)}")
        if places is not None and d != d.quantize(Decimal(1).scaleb(-places)):
            return self.err(path, f"must have at most {places} decimal places")
        return d

    def amount(self, v, path):
        return self.decimal(v, path, hi=P.MAX_AMOUNT, positive=True, places=2)

    def day(self, v, path):
        if not isinstance(v, str) or not DATE_RE.match(v):
            return self.err(path, "must be a date YYYY-MM-DD")
        try:
            return date.fromisoformat(v)
        except ValueError:
            return self.err(path, "must be a real calendar date")

    def symbol(self, v, path, tradable=True):
        if not isinstance(v, str) or not SYMBOL_RE.match(v):
            return self.err(path, "must be an upper-case Yahoo symbol "
                                  "(letters, digits, . - ^ =; at most 12)")
        if tradable and ("=" in v or v.startswith("^")):
            return self.err(path, f"{v} is not tradable (FX pairs and indexes may "
                                  "only be `when` symbols)")
        self.symbols.add(v)
        return v

    def weights(self, v, path):
        if not isinstance(v, dict) or not v:
            return self.err(path, "must be a non-empty map of symbol to weight")
        out, ok = [], True
        for sym, w in v.items():
            s = self.symbol(sym, _join(path, str(sym)))
            d = self.decimal(w, _join(path, str(sym)), hi=Decimal(1), positive=True)
            if s is None or d is None:
                ok = False
            else:
                out.append((s, d))
        if not ok:
            return None
        if sum(d for _, d in out) != 1:
            return self.err(path, "weights must sum to 1 exactly")
        return tuple(sorted(out))

    def primitive(self, v, path, family, label):
        """A {name: body} or bare-name primitive -> (Primitive, body) or None."""
        if isinstance(v, str):
            name, body = v, {}
        elif isinstance(v, dict) and len(v) == 1:
            (name, body), = v.items()
        else:
            return self.err(path, f"must be a {label} primitive: {{name: {{params}}}}")
        prim = P.BY_NAME.get(name)
        if prim is None or prim.family != family:
            return self.err(path, f"unknown {label} {name!r}; the registry has: "
                                  f"{', '.join(P.names(family))} (see describe_primitives)")
        return prim, body

    # -- the spec ------------------------------------------------------------

    def parse(self, raw):
        if not isinstance(raw, dict):
            return Validation(None, (Issue("", "a spec must be a JSON object"),), ())
        fields = {f.name: f for f in P.SPEC_FIELDS}
        self.obj(raw, "", tuple(fields))

        name = self.get(raw, "name", "")
        name = None if name is _MISSING else self.text(name, "name", 80)
        period = self.period(raw.get("period"))
        cur = self.enum(self.get(raw, "base_currency", "",
                                 fields["base_currency"].default), "base_currency",
                        P.CURRENCIES)
        contrib = self.contributions(raw.get("contributions"), period)
        lump = self.lump_sum(raw.get("lump_sum"), period)
        if raw.get("contributions") is None and raw.get("lump_sum") is None:
            self.err("contributions", "a spec needs contributions or lump_sum (or both)")
        execution = self.execution(raw.get("execution"))
        dividends = self.dividends(raw.get("dividends"))
        costs = self.costs(raw.get("costs"))
        rf = fields["risk_free"]
        risk_free = self.decimal(self.get(raw, "risk_free", "", rf.default), "risk_free",
                                 rf.minimum, rf.maximum)
        ties = self.enum(self.get(raw, "ties", "", "alphabetical"), "ties", ("alphabetical",))
        strategies = self.strategies(raw.get("strategies"))
        benchmark = None
        if strategies:
            ids = [s.id for s in strategies]
            benchmark = self.get(raw, "benchmark", "", ids[0])
            if benchmark not in ids:
                benchmark = self.err("benchmark", f"must be a strategy id: {', '.join(ids)}")

        if len(self.symbols) > P.MAX_SYMBOLS:
            self.err("strategies", f"uses {len(self.symbols)} distinct symbols; "
                                   f"the limit is {P.MAX_SYMBOLS}")
        if period and self.daily_rebalance and \
                period.end > _add_years(period.start, P.MAX_DAILY_PERIOD_YEARS):
            for path in self.daily_rebalance:
                self.err(path, f"every: day is allowed only for periods up to "
                               f"{P.MAX_DAILY_PERIOD_YEARS} years")

        if self.errors:
            return Validation(None, tuple(self.errors), ())
        spec = Spec(name, period, cur, contrib, lump, execution, dividends, costs,
                    risk_free, ties, tuple(strategies), benchmark)
        return Validation(spec, (), tuple(self.assumed))

    def period(self, v):
        if v is None:
            return self.err("period", "is required")
        if self.obj(v, "period", ("start", "end")) is None:
            return None
        start = self.day(v.get("start"), "period.start")
        end = self.day(v.get("end"), "period.end")
        if start is None or end is None:
            return None
        if end <= start:
            return self.err("period.end", "must be after start")
        if end > _add_years(start, P.MAX_PERIOD_YEARS):
            return self.err("period", f"is longer than {P.MAX_PERIOD_YEARS} years")
        return Period(start, end)

    def contributions(self, v, period):
        if v is None:
            return None
        path = "contributions"
        if self.obj(v, path, ("amount", "every", "on")) is None:
            return None
        amount = self.get(v, "amount", path)
        amount = None if amount is _MISSING else self.amount(amount, "contributions.amount")
        every = self.get(v, "every", path)
        every = None if every is _MISSING else self.enum(every, "contributions.every",
                                                         P.FREQUENCIES)
        if every == "day":
            if v.get("on") not in (None, "first_trading_day"):
                self.err("contributions.on", "every: day deposits on every trading day; "
                                             "leave `on` out")
            on = Anchor("first_trading_day")
            if period and period.end > _add_years(period.start, P.MAX_DAILY_PERIOD_YEARS):
                self.err("contributions.every", f"every: day is allowed only for periods "
                                                f"up to {P.MAX_DAILY_PERIOD_YEARS} years")
        else:
            on = self.anchor(self.get(v, "on", path, "first_trading_day"), every)
        if None in (amount, every, on):
            return None
        return Contributions(amount, every, on)

    def anchor(self, v, every):
        path = "contributions.on"
        if v in P.ANCHORS:
            return Anchor(v)
        if isinstance(v, dict) and set(v) == {"day_n"}:
            hi = 5 if every == "week" else 28
            n = self.integer(v["day_n"], path + ".day_n", 1, hi)
            return None if n is None else Anchor("day_n", n)
        return self.err(path, "must be first_trading_day, last_trading_day or {day_n: N}")

    def lump_sum(self, v, period):
        if v is None:
            return None
        path = "lump_sum"
        if self.obj(v, path, ("amount", "on")) is None:
            return None
        amount = self.get(v, "amount", path)
        amount = None if amount is _MISSING else self.amount(amount, "lump_sum.amount")
        if v.get("on") is None:
            on = self.assume("lump_sum.on", period.start) if period else None
        else:
            on = self.day(v["on"], "lump_sum.on")
            if on and period and not period.start <= on <= period.end:
                on = self.err("lump_sum.on", "must be inside the period")
        return LumpSum(amount, on) if amount is not None and on is not None else None

    def execution(self, v):
        path = "execution"
        v = {} if v is None else self.obj(v, path, ("decide", "fill", "whole_shares"))
        if v is None:
            return None
        decide = self.enum(self.get(v, "decide", path, "prior_close"), "execution.decide",
                           ("prior_close",))
        fill = self.enum(self.get(v, "fill", path, "close"), "execution.fill", ("close",))
        whole = self.get(v, "whole_shares", path, False)
        if not isinstance(whole, bool):
            whole = self.err("execution.whole_shares", "must be true or false")
        if None in (decide, fill, whole):
            return None
        return Execution(decide, fill, whole)

    def dividends(self, v):
        path = "dividends"
        if isinstance(v, str):
            v = {"mode": v, "withholding_pct": None}
        v = {} if v is None else self.obj(v, path, ("mode", "withholding_pct"))
        if v is None:
            return None
        mode = self.get(v, "mode", path, "reinvest")
        if mode not in P.names("dividends"):
            return self.err("dividends.mode", f"unknown dividends {mode!r}; the registry "
                                              f"has: {', '.join(P.names('dividends'))}")
        param = P.BY_NAME[mode].param("withholding_pct")
        pct = self.decimal(self.get(v, "withholding_pct", path, param.default),
                           "dividends.withholding_pct", param.minimum, param.maximum)
        return None if pct is None else Dividends(mode, pct)

    def costs(self, v):
        path = "costs"
        prim = P.BY_NAME["costs"]
        v = {} if v is None else self.obj(v, path, tuple(p.name for p in prim.params))
        if v is None:
            return None
        vals = [self.decimal(self.get(v, p.name, path, p.default), _join(path, p.name),
                             p.minimum, p.maximum) for p in prim.params]
        return None if None in vals else Costs(*vals)

    def strategies(self, v):
        if not isinstance(v, list) or not v:
            return self.err("strategies", "must be a non-empty list")
        if len(v) > P.MAX_STRATEGIES:
            return self.err("strategies", f"has {len(v)} strategies; at most "
                                          f"{P.MAX_STRATEGIES} are allowed")
        out, seen = [], set()
        for i, s in enumerate(v):
            path = f"strategies[{i}]"
            if self.obj(s, path, ("id", "label", "allocate", "holdings")) is None:
                continue
            sid = self.get(s, "id", path)
            if sid is not _MISSING:
                if not isinstance(sid, str) or not STRATEGY_ID_RE.match(sid):
                    sid = self.err(path + ".id", "id must match [a-z0-9][a-z0-9_-]{0,31}")
                elif sid in seen:
                    sid = self.err(path + ".id", f"duplicate strategy id {sid!r}")
                else:
                    seen.add(sid)
            label = s.get("label")
            if label is None:
                label = self.assume(path + ".label", sid) if isinstance(sid, str) else None
            else:
                label = self.text(label, path + ".label", 60)
            alloc = self.get(s, "allocate", path)
            alloc = None if alloc is _MISSING else self.allocator(alloc, path + ".allocate")
            holdings = self.holdings(self.get(s, "holdings", path, "keep"),
                                     path + ".holdings")
            if None not in (sid, label, alloc, holdings) and sid is not _MISSING:
                out.append(Strategy(sid, label, alloc, holdings))
        return out if len(out) == len(v) else None

    def allocator(self, v, path, depth=0):
        found = self.primitive(v, path, "allocator", "allocator")
        if found is None:
            return None
        prim, body = found
        ppath = _join(path, prim.name)
        if prim.name == "fixed":
            w = self.weights(body, ppath)
            return None if w is None else Fixed(w)
        if prim.name == "rank":
            return self.rank(body, ppath)
        if depth >= P.MAX_WHEN_DEPTH:
            return self.err(path, f"`when` may be nested at most {P.MAX_WHEN_DEPTH} deep")
        return self.when(body, ppath, depth)

    def rank(self, body, path):
        if self.obj(body, path, ("universe", "signal", "pick")) is None:
            return None
        uni = self.get(body, "universe", path)
        universe = None
        if uni is not _MISSING:
            if not isinstance(uni, list) or not uni:
                self.err(path + ".universe", "must be a non-empty list of symbols")
            elif len(set(map(str, uni))) != len(uni):
                self.err(path + ".universe", "lists a symbol twice")
            else:
                syms = [self.symbol(s, f"{path}.universe[{i}]") for i, s in enumerate(uni)]
                universe = None if None in syms else tuple(sorted(syms))
        sig = self.get(body, "signal", path)
        signal = None if sig is _MISSING else self.signal(sig, path + ".signal")
        pk = self.get(body, "pick", path)
        pick = n = None
        if pk is not _MISSING:
            if isinstance(pk, dict) and len(pk) == 1 and next(iter(pk)) in ("top", "bottom"):
                (pick, raw_n), = pk.items()
                n = self.integer(raw_n, f"{path}.pick.{pick}", 1,
                                 len(universe) if universe else P.MAX_SYMBOLS)
            else:
                self.err(path + ".pick", "must be {top: N} or {bottom: N}")
        if None in (universe, signal, pick, n):
            return None
        return Rank(universe, signal, pick, n)

    def when(self, body, path, depth):
        allowed = tuple(p.name for p in P.BY_NAME["when"].params)
        if self.obj(body, path, allowed) is None:
            return None
        sym = self.get(body, "symbol", path)
        sym = None if sym is _MISSING else self.symbol(sym, path + ".symbol", tradable=False)
        sig = self.get(body, "signal", path)
        signal = None if sig is _MISSING else self.signal(sig, path + ".signal")
        op = self.get(body, "op", path)
        op = None if op is _MISSING else self.enum(op, path + ".op", P.OPS)
        th = self.get(body, "threshold", path)
        threshold = None if th is _MISSING else self.decimal(
            th, path + ".threshold", -P.MAX_AMOUNT, P.MAX_AMOUNT)
        then = self.get(body, "then", path)
        then = None if then is _MISSING else self.allocator(then, path + ".then", depth + 1)
        else_raw = self.get(body, "else", path, "cash")
        else_ok, else_ = True, None
        if else_raw != "cash":
            else_ = self.allocator(else_raw, path + ".else", depth + 1)
            else_ok = else_ is not None
        if None in (sym, signal, op, threshold, then) or not else_ok:
            return None
        return When(sym, signal, op, threshold, then, else_)

    def signal(self, v, path):
        found = self.primitive(v, path, "signal", "signal")
        if found is None:
            return None
        prim, body = found
        ppath = _join(path, prim.name)
        if self.obj(body, ppath, tuple(p.name for p in prim.params)) is None:
            return None
        params, window = [], 0
        for p in prim.params:
            raw = self.get(body, p.name, ppath)
            if raw is _MISSING:
                return None
            if p.type == "lookback":
                m = LOOKBACK_RE.match(raw) if isinstance(raw, str) else None
                if m is None:
                    return self.err(_join(ppath, p.name), "must be N followed by d, w, mo "
                                                          "or y (e.g. 21d, 1mo, 1y)")
                days = int(m.group(1)) * P.LOOKBACK_UNITS[m.group(2)]
                val = raw
            else:
                if isinstance(raw, int) and not isinstance(raw, bool) \
                        and raw > P.MAX_LOOKBACK_DAYS:
                    return self.err(_join(ppath, p.name), f"exceeds the "
                                    f"{P.MAX_LOOKBACK_DAYS} trading days lookback limit")
                val = self.integer(raw, _join(ppath, p.name), p.minimum, p.maximum)
                if val is None:
                    return None
                days = val
            if days > P.MAX_LOOKBACK_DAYS:
                return self.err(_join(ppath, p.name), f"is {days} trading days; the "
                                f"lookback limit is {P.MAX_LOOKBACK_DAYS} trading days")
            params.append((p.name, val))
            window = max(window, days)
        return Signal(prim.name, tuple(params), window)

    def holdings(self, v, path):
        found = self.primitive(v, path, "holdings", "holdings")
        if found is None:
            return None
        prim, body = found
        if prim.name != "rebalance":
            if body:
                return self.err(path, f"{prim.name} takes no parameters")
            return Holdings(prim.name)
        ppath = path + ".rebalance"
        if self.obj(body, ppath, ("every", "to")) is None:
            return None
        every = self.get(body, "every", ppath)
        every = None if every is _MISSING else self.enum(every, ppath + ".every",
                                                         P.FREQUENCIES)
        if every == "day":
            self.daily_rebalance.append(ppath + ".every")
        to_raw = self.get(body, "to", ppath, "allocation")
        to = None
        if to_raw != "allocation":
            to = self.weights(to_raw, ppath + ".to")
            if to is None:
                return None
        return None if every is None else Holdings("rebalance", every, to)
