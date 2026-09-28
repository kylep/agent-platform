"""The simulator: walk the days, move money, log every step.

Per strategy, every day d in the period (the union of the base calendar,
the traded symbols' days and the FX days) runs in this order:

1. splits (logged only) and dividends for shares held entering d;
   reinvested dividends buy at d's close;
2. pending orders fill if their symbol has a bar on d;
3. on a base-calendar event day: money arrives, the allocator decides from
   bars strictly before d, and the resulting trades fill at d's close for
   every symbol that has a bar on d (the rest wait for their next bar);
4. on a base-calendar day: the portfolio is valued in base currency.

Only `_Market` reads prices, and it only knows `today`: decisions get bars
before today, fills and valuations get today's close. That is what makes
the lookahead property test hold.

Money: base-currency cash in cents, shares to 8 dp, both ROUND_HALF_EVEN.
Buying a USD symbol with CAD divides by CAD=X (CAD per USD); selling
multiplies. fx_bps is charged in base currency on every converted amount.
"""
from dataclasses import dataclass, field
from decimal import ROUND_FLOOR, Decimal, localcontext

from . import allocate, calendar
from .data import FX_SYMBOL, ONE_DAY, DataIntegrityError, EngineError, check_total_return
from .result import Event, Exclusion, SeriesPoint, StrategyResult
from .spec import CASH_Q, DECIMAL_CONTEXT, SHARES_Q, Assumption, Fixed, Rank, When, _walk

BPS = Decimal(10000)
WEIGHT_Q = Decimal("0.000001")


@dataclass(frozen=True)
class StrategyRun:
    result: StrategyResult   # series + events; metrics stay {} for T5
    max_weight: dict | None  # {weight, symbol, day}: largest single-name share of value
    # {symbol: {weight, day}}: each symbol's own peak, so caveats can tell a
    # broad index fund at 100% from a single stock at 45%.
    max_weights: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Simulation:
    calendar: tuple          # base-calendar days in the period
    calendar_source: str
    assumed: tuple           # spec.Assumption the engine adds (the calendar used)
    strategies: tuple        # StrategyRun, in spec order
    exclusions: tuple        # result.Exclusion across all strategies


def simulate(spec, dataset):
    """Run every strategy of a validated Spec on a Dataset."""
    with localcontext(DECIMAL_CONTEXT):
        traded = _check_inputs(spec, dataset)
        days, source = calendar.base_calendar(spec, dataset)
        money = calendar.money_in(spec, days)
        fx_needed = any(dataset.currency(s) != spec.base_currency for s in traded)
        watch = set(spec.symbols()) | ({FX_SYMBOL} if fx_needed else set())
        all_days = sorted({d for s in watch for d in dataset.days(s)} | set(days))
        all_days = [d for d in all_days if spec.period.start <= d <= spec.period.end]

        runs, exclusions = [], []
        for strat in spec.strategies:
            book = _Book(spec, strat, dataset, days, money)
            for d in all_days:
                book.step(d)
            book.finish(all_days[-1])
            runs.append(StrategyRun(
                StrategyResult(strat.id, strat.label, tuple(book.series), tuple(book.events)),
                book.max_weight, book.max_weights))
            exclusions += book.exclusions
        return Simulation(days, source, (Assumption("calendar", source),), tuple(runs),
                          tuple(exclusions))


def _traded(alloc):
    out = set()
    if isinstance(alloc, Fixed):
        out |= {s for s, _ in alloc.weights}
    elif isinstance(alloc, Rank):
        out |= set(alloc.universe)
    elif isinstance(alloc, When):
        out |= _traded(alloc.then)
        if alloc.else_ is not None:
            out |= _traded(alloc.else_)
    return out


def _check_inputs(spec, dataset):
    missing = [s for s in spec.symbols() if not dataset.has(s)]
    if missing:
        raise EngineError(f"symbols missing from the dataset: {', '.join(missing)}")
    traded = set()
    for s in spec.strategies:
        traded |= _traded(s.allocate)
        if s.holdings.to:
            traded |= {sym for sym, _ in s.holdings.to}
    traded = sorted(traded)
    for sym in traded:
        if dataset.currency(sym) is None:
            raise DataIntegrityError(sym, None, "currency unknown; refetch it with prices")
    if any(dataset.currency(s) != spec.base_currency for s in traded) \
            and not dataset.has(FX_SYMBOL):
        raise EngineError(f"{FX_SYMBOL} is needed to convert between CAD and USD but is "
                          "not in the dataset")
    # A `when` symbol that is a real security gates trades with its closes, so
    # it gets the same integrity check as a traded one (FX pairs and indexes
    # have no dividends to check).
    gates = {a.symbol for s in spec.strategies for a in _walk(s.allocate)
             if isinstance(a, When) and "=" not in a.symbol and not a.symbol.startswith("^")}
    for sym in sorted(set(traded) | gates):
        check_total_return(dataset, sym, spec.period.start, spec.period.end)
    return traded


def q_cash(x):
    return x.quantize(CASH_Q, context=DECIMAL_CONTEXT)


def q_shares(x):
    return x.quantize(SHARES_Q, context=DECIMAL_CONTEXT)


def convert(amount, frm, to, rate):
    """rate = CAD=X = CAD per USD."""
    if frm == to:
        return amount
    return amount * rate if frm == "USD" else amount / rate


def split_amount(total, weights):
    """Cents per (key, weight); when the weights sum to 1 the last key takes
    the remainder so the parts add up to total exactly."""
    out, used = [], Decimal(0)
    full = sum((w for _, w in weights), Decimal(0)) == 1
    for i, (key, w) in enumerate(weights):
        amt = total - used if full and i == len(weights) - 1 else q_cash(total * w)
        out.append((key, amt))
        used += amt
    return out


class _Market:
    """The single gate to prices. It only knows `today`."""

    def __init__(self, dataset):
        self._ds = dataset
        self.today = None

    def decision(self, sym):
        return self._ds.before(sym, self.today)

    def last(self, sym):
        """The last bar on or before today (today's close if it traded)."""
        return self._ds.before(sym, self.today + ONE_DAY).last()

    def bar_today(self, sym):
        bar = self.last(sym)
        return bar if bar is not None and bar.day == self.today else None


@dataclass
class _Order:
    decided: object
    symbol: str
    budget: Decimal


@dataclass
class _Batch:
    decided: object
    decision: allocate.Decision
    kind: str  # rotate | rebalance


class _Book:
    def __init__(self, spec, strat, dataset, base_days, money):
        self.spec, self.strat, self.ds = spec, strat, dataset
        self.base = spec.base_currency
        self.costs = spec.costs
        self.market = _Market(dataset)
        self.base_days = frozenset(base_days)
        self.money = {}
        for m in money:
            self.money.setdefault(m.day, []).append(m)
        h = strat.holdings
        self.rebalance_days = (calendar.period_starts(base_days, h.every)
                               if h.mode == "rebalance" else frozenset())
        self.cash = Decimal(0)       # free, investable
        self.reserved = Decimal(0)   # committed to pending keep orders
        self.div_cash = Decimal(0)   # dividends: cash mode, never invested
        self.contributed = Decimal(0)
        self.shares = {}
        self.orders, self.batches = [], []
        self.events, self.series, self.exclusions = [], [], []
        self.max_weight = None
        self.max_weights = {}

    # -- logging -----------------------------------------------------------------
    def log(self, kind, symbol=None, **detail):
        self.events.append(Event(self.market.today, kind, symbol, detail))

    # -- the day -----------------------------------------------------------------
    def step(self, d):
        self.market.today = d
        self._corporate_actions()
        self._fill_orders()
        self._run_batches()
        rebalance = d in self.rebalance_days
        if d in self.money or rebalance:
            self._event(self.money.get(d, ()), rebalance)
        if d in self.base_days:
            self._value()

    def finish(self, last_day):
        self.market.today = last_day
        for o in self.orders:
            self.log("cash", o.symbol, reason="unfilled", amount=o.budget, decided=o.decided)
        for b in self.batches:
            self.log("cash", reason="unfilled", kind=b.kind, decided=b.decided)

    def _corporate_actions(self):
        div = self.spec.dividends
        for sym in sorted(self.shares):
            bar = self.market.bar_today(sym)
            if bar is None:
                continue
            held = self.shares[sym]
            if bar.split_ratio is not None and bar.split_ratio not in (0, 1):
                # Prices are split-adjusted at the source: the count must not change.
                self.log("split", sym, ratio=bar.split_ratio, shares=held)
            if bar.dividend <= 0:
                continue
            gross = q_cash(held * bar.dividend)
            withheld = q_cash(gross * div.withholding_pct / 100)
            net = gross - withheld
            if div.mode == "cash":
                net_base = self._to_base(sym, net)
                self.log("dividend", sym, per_share=bar.dividend, shares=held, gross=gross,
                         withheld=withheld, net=net, net_base=net_base, mode=div.mode)
                self.div_cash += net_base
            else:
                # net_base here is informational (valued at today's rate, no fee):
                # the dividend never leaves the symbol's currency.
                net_base = q_cash(self._at_rate(sym, net))
                self.log("dividend", sym, per_share=bar.dividend, shares=held, gross=gross,
                         withheld=withheld, net=net, net_base=net_base, mode=div.mode)
                self._drip(sym, bar, net)

    def _fill_orders(self):
        waiting = []
        for o in self.orders:
            bar = self.market.bar_today(o.symbol)
            if bar is None:
                waiting.append(o)
                continue
            self.reserved -= o.budget
            self.cash += o.budget - self._buy(o.symbol, bar, o.budget, "allocation", o.decided)
        self.orders = waiting

    def _run_batches(self):
        # FIFO: a later batch never overtakes one still waiting for a market.
        while self.batches:
            b = self.batches[0]
            syms = set(self.shares) | {s for s, _ in b.decision.weights}
            if any(self.market.bar_today(s) is None for s in syms):
                return
            self.batches.pop(0)
            self._trade_to(b)

    # -- decisions -------------------------------------------------------------
    def _event(self, deposits, rebalance):
        for m in deposits:
            self.cash += m.amount
            self.contributed += m.amount
            self.log("contribution", amount=m.amount, source=m.source)
        h = self.strat.holdings
        if rebalance:
            alloc = self.strat.allocate if h.to is None else Fixed(h.to)
            self.log("rebalance", to="allocation" if h.to is None else dict(h.to))
            self.batches.append(_Batch(self.market.today, self._decide(alloc), "rebalance"))
            self._run_batches()
        elif h.mode == "rotate":
            self.batches.append(_Batch(self.market.today, self._decide(self.strat.allocate),
                                       "rotate"))
            self._run_batches()
        else:
            self._invest_new_money(self._decide(self.strat.allocate))

    def _decide(self, alloc):
        dec = allocate.evaluate(alloc, self.market.decision)
        for sym, reason, detail in dec.exclusions:
            self.log("exclusion", sym, reason=reason, **detail)
            self.exclusions.append(Exclusion(self.strat.id, self.market.today, sym, reason))
        return dec

    def _invest_new_money(self, dec):
        """keep: all free cash (this event's deposits plus money held back
        earlier as cash) follows the allocation; nothing is sold."""
        budget = self.cash
        if budget <= 0:
            return
        legs = split_amount(budget, dec.weights)
        held_back = budget - sum((a for _, a in legs), Decimal(0))
        if held_back > 0:
            self.log("cash", reason=dec.cash_reason, amount=held_back)
        for sym, amount in legs:
            if amount <= 0:
                continue
            self.cash -= amount
            self.reserved += amount
            self.orders.append(_Order(self.market.today, sym, amount))
        self._fill_orders()

    def _trade_to(self, batch):
        """Trade the whole portfolio to the batch's weights at today's close.
        Only the difference is traded, so a symbol that stays in the
        allocation is not sold and bought back (that would only burn costs)."""
        dec = batch.decision
        targets = dict(dec.weights)
        values = {s: self._value_of(s, self.market.bar_today(s).close) for s in self.shares}
        total = self.cash + sum(values.values(), Decimal(0))
        goal = {s: q_cash(total * w) for s, w in targets.items()}
        for sym in sorted(values):
            tgt = goal.get(sym, Decimal(0))
            if values[sym] <= tgt:
                continue
            held = self.shares[sym]
            if tgt == 0:
                qty = held
            else:
                qty = held * (values[sym] - tgt) / values[sym]
                qty = (qty.to_integral_value(ROUND_FLOOR) if self.spec.execution.whole_shares
                       else q_shares(qty))
            if qty > 0:
                self._sell(sym, self.market.bar_today(sym), qty, batch.kind)
        keep_cash = q_cash(total * dec.cash)
        if dec.cash > 0:
            self.log("cash", reason=dec.cash_reason, amount=keep_cash)
        need = []
        for sym in sorted(goal):
            have = (self._value_of(sym, self.market.bar_today(sym).close)
                    if sym in self.shares else Decimal(0))
            if goal[sym] > have:
                need.append((sym, goal[sym] - have))
        pool = self.cash - keep_cash
        want = sum((n for _, n in need), Decimal(0))
        if pool <= 0 or want <= 0:
            return
        legs = need if want <= pool else split_amount(pool, [(s, n / want) for s, n in need])
        for sym, amount in legs:
            if amount > 0:
                self.cash -= self._buy(sym, self.market.bar_today(sym), amount, batch.kind,
                                       batch.decided)

    # -- fills -----------------------------------------------------------------
    def _rate(self):
        bar = self.market.last(FX_SYMBOL)
        if bar is None:
            raise DataIntegrityError(FX_SYMBOL, self.market.today,
                                     "no FX rate on or before this day")
        return bar

    def _fx_log(self, frm, to, amount, converted, fee, rate_bar):
        extra = {} if rate_bar.day == self.market.today else {"fx_fallback_day": rate_bar.day}
        self.log("fx", **{"from": frm, "to": to}, rate=rate_bar.close, amount=amount,
                 converted=converted, fee=fee, **extra)

    def _value_of(self, sym, close):
        """Base-currency value of a position at `close`, no costs."""
        ccy = self.ds.currency(sym)
        value = self.shares[sym] * close
        if ccy != self.base:
            value = convert(value, ccy, self.base, self._rate().close)
        return q_cash(value)

    def _to_base(self, sym, amount):
        """Money received in the symbol's currency, converted (fee charged)."""
        ccy = self.ds.currency(sym)
        if ccy == self.base:
            return amount
        rate = self._rate()
        gross = q_cash(convert(amount, ccy, self.base, rate.close))
        fee = q_cash(gross * self.costs.fx_bps / BPS)
        self._fx_log(ccy, self.base, amount, gross, fee, rate)
        return gross - fee

    def _at_rate(self, sym, amount):
        """Base-currency value of an amount in the symbol's currency, no fee."""
        ccy = self.ds.currency(sym)
        return amount if ccy == self.base else convert(amount, ccy, self.base,
                                                       self._rate().close)

    def _drip(self, sym, bar, net):
        """Reinvest a net dividend in the same symbol at the ex-date close, in
        the symbol's own currency: no conversion, commission or slippage, so a
        dividend smaller than a commission is still reinvested. Only a
        whole-shares leftover is converted (fx_bps applies to it) to cash."""
        if self.spec.execution.whole_shares:
            qty = (net / bar.close).to_integral_value(ROUND_FLOOR)
        else:
            qty = q_shares(net / bar.close)
        spent = net if not self.spec.execution.whole_shares else q_cash(qty * bar.close)
        if qty > 0:
            self.log("buy", sym, shares=qty, price=bar.close, amount=spent,
                     currency=self.ds.currency(sym), cost_base=Decimal(0),
                     commission=Decimal(0), slippage=Decimal(0), source="dividend")
            self.shares[sym] += qty
        else:
            spent = Decimal(0)
        if net > spent:
            left = self._to_base(sym, net - spent)
            self.cash += left
            self.log("cash", reason="dividend_remainder", amount=left)

    def _buy(self, sym, bar, budget, source, decided=None):
        """Spend up to `budget` (base) on `sym` at today's close. Returns the
        base amount actually spent; the caller keeps the rest as cash."""
        c = self.costs
        if budget <= c.commission:
            self.log("cash", sym, reason="below_commission", amount=budget)
            return Decimal(0)
        net = budget - c.commission
        ccy = self.ds.currency(sym)
        rate = fee = None
        if ccy != self.base:
            rate = self._rate()
            fee = q_cash(net * c.fx_bps / BPS)
            local = q_cash(convert(net - fee, self.base, ccy, rate.close))
        else:
            local = net
        price = bar.close * (1 + c.slippage_bps / BPS)
        if self.spec.execution.whole_shares:
            qty = (local / price).to_integral_value(ROUND_FLOOR)
        else:
            qty = q_shares(local / price)
        if qty <= 0:
            self.log("cash", sym, reason="below_one_share", amount=budget)
            return Decimal(0)
        spent_local, spent_net = local, net
        if self.spec.execution.whole_shares:
            # Only the part that bought whole shares is spent (and converted);
            # the remainder never leaves base currency.
            frac = qty * price / local
            spent_local, spent_net = q_cash(qty * price), q_cash(net * frac)
            if fee is not None:
                fee = q_cash(fee * frac)
        if rate is not None:
            self._fx_log(self.base, ccy, spent_net - fee, spent_local, fee, rate)
        detail = {"shares": qty, "price": price, "amount": spent_local, "currency": ccy,
                  "cost_base": spent_net + c.commission, "commission": c.commission,
                  "slippage": q_cash(qty * bar.close * c.slippage_bps / BPS),
                  "source": source}
        if decided is not None and decided != self.market.today:
            detail["decided"] = decided
        self.log("buy", sym, **detail)
        self.shares[sym] = self.shares.get(sym, Decimal(0)) + qty
        return spent_net + c.commission

    def _sell(self, sym, bar, qty, source):
        c = self.costs
        price = bar.close * (1 - c.slippage_bps / BPS)
        gross = q_cash(qty * price)
        proceeds = self._to_base(sym, gross)
        net = proceeds - c.commission
        self.log("sell", sym, shares=qty, price=price, amount=gross,
                 currency=self.ds.currency(sym), proceeds_base=net,
                 commission=c.commission,
                 slippage=q_cash(qty * bar.close * c.slippage_bps / BPS), source=source)
        left = self.shares[sym] - qty
        if left > 0:
            self.shares[sym] = left
        else:
            del self.shares[sym]
        self.cash += net

    # -- valuation ---------------------------------------------------------------
    def _value(self):
        positions = {s: self._value_of(s, self.market.last(s).close) for s in self.shares}
        value = self.cash + self.reserved + self.div_cash + sum(positions.values(), Decimal(0))
        self.series.append(SeriesPoint(self.market.today, value, self.contributed))
        if value > 0:
            for sym in sorted(positions):
                w = (positions[sym] / value).quantize(WEIGHT_Q, context=DECIMAL_CONTEXT)
                if self.max_weight is None or w > self.max_weight["weight"]:
                    self.max_weight = {"weight": w, "symbol": sym, "day": self.market.today}
                if sym not in self.max_weights or w > self.max_weights[sym]["weight"]:
                    self.max_weights[sym] = {"weight": w, "day": self.market.today}
