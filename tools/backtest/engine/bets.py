"""The `bet` strategy: one position at a time, long or short, from closes.

Per day d (the same walk as simulate._Book, which this mirrors in shape):

1. the lump sum arrives on its day;
2. an open position is checked against today's close: hold 1d closes it
   (the next trading day's close); hold open closes it at the first close
   where its stop or target is reached or its exit condition holds, in that
   order. The decision reads only closes before d, the fill is d's close;
3. when flat (and not just stopped out today) a new bet is decided from
   closes before d and opened at d's close;
4. on a base-calendar day the account is valued: capital plus realized
   P&L plus the open position marked at its last close.

A bet's money is its `stake`. Long P&L is stake * (exit / entry - 1); short
P&L is stake * (entry - exit) / entry. Both are close-to-close price moves,
rounded to 0.0001 percent half-even, less `cost_pct` percent of the stake:
the stock-bets scoring rules (tools/stock_bets/engine.py `score`), so the two
agree on the same prices. Borrowing a share to short costs nothing, no margin
is held, and dividends are neither received nor owed: paper only.
"""
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from . import allocate, signals
from .result import Event, SeriesPoint
from .spec import CASH_Q, DECIMAL_CONTEXT, SHARES_Q

PCT_Q = Decimal("0.0001")
_ENTRY_KIND = {"long": "buy", "short": "short"}
_EXIT_KIND = {"long": "sell", "short": "cover"}


def _q_cash(x):
    return x.quantize(CASH_Q, context=DECIMAL_CONTEXT)


def _pct(x):
    return x.quantize(PCT_Q, context=DECIMAL_CONTEXT, rounding=ROUND_HALF_EVEN)


def gross_pct(side, entry, exit_):
    """Percent gained on the stake by a close-to-close move (rule 5 of
    stock-bets scoring, long and short)."""
    if side == "long":
        return _pct((exit_ / entry - 1) * 100)
    return _pct((entry - exit_) / entry * 100)


class _Position:
    def __init__(self, symbol, day, entry, shares, spy_entry):
        self.symbol, self.day, self.entry, self.shares = symbol, day, entry, shares
        self.spy_entry = spy_entry


class BetBook:
    """One bet strategy on one dataset; drives like simulate._Book."""

    def __init__(self, spec, strat, market, base_days, money):
        self.spec, self.strat, self.market = spec, strat, market
        self.bet = strat.bet
        self.base_days = frozenset(base_days)
        self.money = {m.day: m for m in money}
        self.cash = Decimal(0)
        self.contributed = Decimal(0)
        self.pos = None
        self.events, self.series, self.exclusions = [], [], []
        self.max_weight, self.max_weights = None, {}

    def log(self, kind, symbol=None, **detail):
        self.events.append(Event(self.market.today, kind, symbol, detail))

    def step(self, d):
        self.market.today = d
        with localcontext(DECIMAL_CONTEXT):
            if d in self.money:
                deposit = self.money[d].amount
                self.cash += deposit
                self.contributed += deposit
                self.log("contribution", amount=deposit, source=self.money[d].source)
            stopped = False
            if self.pos is not None:
                reason = self._exit_reason()
                if reason is not None:
                    self._close(reason)
                    stopped = reason in ("stop", "target")
            if self.pos is None and not stopped and self.contributed > 0:
                self._open()
            if d in self.base_days:
                self._value()

    # -- the position's life ---------------------------------------------------
    def _move_pct(self, close):
        """Percent the open position has gained at `close` (unrounded)."""
        p = self.pos
        sign = 1 if self.bet.side == "long" else -1
        return sign * (close / p.entry - 1) * 100

    def _exit_reason(self):
        p, bet = self.pos, self.bet
        bar = self.market.bar_today(p.symbol)
        if bar is None:
            return None
        if bet.hold == "1d":
            return "close"
        move = self._move_pct(bar.close)
        if bet.stop_pct is not None and move <= -bet.stop_pct:
            return "stop"
        if bet.target_pct is not None and move >= bet.target_pct:
            return "target"
        if bet.exit is not None and allocate.condition_holds(
                bet.exit, self.market.decision(p.symbol)):
            return "exit"
        return None

    def _close(self, reason):
        p, bet, today = self.pos, self.bet, self.market.today
        bar = self.market.bar_today(p.symbol)
        gross = gross_pct(bet.side, p.entry, bar.close)
        net = gross - bet.cost_pct
        pnl = _q_cash(bet.stake * net / 100)
        cost = _q_cash(bet.stake * bet.cost_pct / 100)
        ccy = self.market.currency(p.symbol)
        detail = {"shares": p.shares, "price": bar.close, "amount": _q_cash(p.shares * bar.close),
                  "currency": ccy, "commission": Decimal(0), "slippage": cost,
                  "source": reason}
        if bet.side == "long":
            detail["proceeds_base"] = detail["amount"]
        self.log(_EXIT_KIND[bet.side], p.symbol, **detail)
        spy = self.market.bar_today("SPY")
        spy_pct = None if spy is None or p.spy_entry is None else \
            _pct((spy.close / p.spy_entry - 1) * 100)
        self.log("bet", p.symbol, side=bet.side, entry_day=p.day, entry=p.entry,
                 exit=bar.close, reason=reason, gross_pct=gross, net_pct=net, pnl=pnl,
                 spy_pct=spy_pct, days_held=(today - p.day).days)
        self.cash += pnl
        self.pos = None

    def _open(self):
        bet = self.bet
        symbol = self._choose()
        if symbol is None:
            return
        bar = self.market.bar_today(symbol)
        shares = (bet.stake / bar.close).quantize(SHARES_Q, context=DECIMAL_CONTEXT)
        spy = self.market.bar_today("SPY")
        self.pos = _Position(symbol, self.market.today, bar.close, shares,
                             None if spy is None else spy.close)
        self.log(_ENTRY_KIND[bet.side], symbol, shares=shares, price=bar.close,
                 amount=_q_cash(shares * bar.close), currency=self.market.currency(symbol),
                 cost_base=bet.stake, commission=Decimal(0), slippage=Decimal(0),
                 source="bet")

    def _choose(self):
        """The symbol to bet on today, or None. Only symbols with a bar today
        can fill; ties between picks break alphabetically."""
        bet = self.bet
        tradable = [s for s in bet.universe if self.market.bar_today(s) is not None]
        if bet.pick is not None:
            scored = []
            for sym in tradable:
                value = signals.compute(bet.pick_signal, self.market.decision(sym))
                if value is not None:
                    scored.append((sym, value))
            if not scored:
                return None
            sign = -1 if bet.pick == "top" else 1
            tradable = [min(scored, key=lambda sv: (sign * sv[1], sv[0]))[0]]
        if not tradable:
            return None
        symbol = tradable[0]
        if bet.enter is not None and not allocate.condition_holds(
                bet.enter, self.market.decision(symbol)):
            return None
        return symbol

    # -- valuation -------------------------------------------------------------
    def _value(self):
        value = self.cash
        if self.pos is not None:
            last = self.market.last(self.pos.symbol).close
            value += _q_cash(self.bet.stake * self._move_pct(last) / 100)
        self.series.append(SeriesPoint(self.market.today, value, self.contributed))
