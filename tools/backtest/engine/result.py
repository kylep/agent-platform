"""The result shape the engine (T4) and metrics (T5) fill in.

Everything here serializes through `canonical_json`, so a result is
byte-identical for the same spec, dataset and engine version.
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .spec import jsonable

# Every event kind the engine may log. An exclusion or an empty universe is
# an event too, so the timeline never hides a skipped decision.
EVENT_KINDS = (
    "contribution",  # money arrived (detail: amount)
    "buy", "sell",   # detail: shares, price, amount, commission, slippage
    "fx",            # a conversion (detail: from, to, rate, amount, fee; fx_fallback_day)
    "dividend",      # detail: per_share, gross, withheld, net, reinvested
    "split",         # logged only: prices are split-adjusted, share counts unchanged
    "rebalance",     # a rebalance date was processed
    "exclusion",     # symbol ineligible at this event (detail: reason)
    "cash",          # money held as cash (detail: reason: when_else | empty_universe | remainder)
)


@dataclass(frozen=True)
class SeriesPoint:
    day: date
    value: Decimal        # portfolio value in base currency at that day's close
    contributed: Decimal  # cumulative money in, base currency

    def to_dict(self):
        return {"day": self.day, "value": self.value, "contributed": self.contributed}


@dataclass(frozen=True)
class Event:
    day: date
    kind: str
    symbol: str | None = None
    detail: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {self.kind!r}")

    def to_dict(self):
        return {"day": self.day, "kind": self.kind, "symbol": self.symbol,
                "detail": self.detail}


@dataclass(frozen=True)
class Exclusion:
    strategy_id: str
    day: date
    symbol: str
    reason: str

    def to_dict(self):
        return {"strategy_id": self.strategy_id, "day": self.day, "symbol": self.symbol,
                "reason": self.reason}


@dataclass(frozen=True)
class Caveat:
    code: str   # stable key for the UI, e.g. hindsight, concentration, taxes, data
    text: str

    def to_dict(self):
        return {"code": self.code, "text": self.text}


@dataclass(frozen=True)
class StrategyResult:
    strategy_id: str
    label: str
    series: tuple = ()   # SeriesPoint per base-calendar trading day
    events: tuple = ()   # Event, in (day, engine order)
    metrics: dict = field(default_factory=dict)  # T5 owns the keys; None = not computable

    def to_dict(self):
        return {"strategy_id": self.strategy_id, "label": self.label,
                "series": list(self.series), "events": list(self.events),
                "metrics": self.metrics}


@dataclass(frozen=True)
class BacktestResult:
    experiment_id: str
    engine_version: str
    dataset_sha: str
    spec: dict           # Spec.to_dict()
    description: str     # describe(spec)
    assumed: tuple       # spec.Assumption, from validate()
    caveats: tuple       # Caveat
    exclusions: tuple    # Exclusion, across all strategies
    strategies: tuple    # StrategyResult, in spec order

    def to_dict(self):
        return jsonable({
            "experiment_id": self.experiment_id, "engine_version": self.engine_version,
            "dataset_sha": self.dataset_sha, "spec": self.spec,
            "description": self.description, "assumed": list(self.assumed),
            "caveats": list(self.caveats), "exclusions": list(self.exclusions),
            "strategies": list(self.strategies)})
