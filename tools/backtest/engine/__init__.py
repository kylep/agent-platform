"""Backtest engine: pure standard-library, deterministic.

No I/O, clock, randomness or environment access anywhere under this
package: the same spec, dataset and ENGINE_VERSION must give byte-identical
output on every run and platform.
"""
from .backtest import run_backtest
from .describe import describe
from .primitives import describe_primitives
from .result import (
    EVENT_KINDS,
    BacktestResult,
    Caveat,
    Event,
    Exclusion,
    SeriesPoint,
    StrategyResult,
)
from .spec import (
    CASH_Q,
    DECIMAL_CONTEXT,
    SHARES_Q,
    Assumption,
    Issue,
    Spec,
    SpecError,
    Validation,
    canonical_json,
    experiment_id,
    parse_spec,
    spec_hash,
    validate,
)
from .version import ENGINE_VERSION

__all__ = [
    "Assumption", "BacktestResult", "CASH_Q", "Caveat", "DECIMAL_CONTEXT", "ENGINE_VERSION",
    "EVENT_KINDS",
    "Event", "Exclusion", "Issue", "SHARES_Q", "SeriesPoint", "Spec", "SpecError",
    "StrategyResult", "Validation", "canonical_json", "describe", "describe_primitives",
    "experiment_id", "parse_spec", "run_backtest", "spec_hash", "validate",
]
