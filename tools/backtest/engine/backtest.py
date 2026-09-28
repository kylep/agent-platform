"""The one entry point: validate -> simulate -> metrics -> caveats -> result."""
from dataclasses import replace

from . import caveats, metrics
from .data import Dataset
from .describe import describe
from .result import BacktestResult
from .simulate import simulate
from .spec import SpecError, experiment_id, validate
from .version import ENGINE_VERSION


def run_backtest(raw_spec, dataset, dataset_sha, fetched=None):
    """Run a raw (JSON) spec on a pinned dataset.

    `dataset` is a data.Dataset or the mapping `Dataset.from_dict` takes;
    `dataset_sha` is the pin's sha256 (the caller hashes the canonical rows);
    `fetched` is the pin's (first, last) fetch date, or None. Raises
    SpecError for an invalid spec and EngineError / DataIntegrityError when
    the data cannot support the run.
    """
    v = validate(raw_spec)
    if not v.ok:
        raise SpecError(v.errors)
    spec = v.spec
    if not isinstance(dataset, Dataset):
        dataset = Dataset.from_dict(dataset)
    sim = simulate(spec, dataset)
    results, reasons = [], {}
    for run in sim.strategies:
        m, reason = metrics.strategy_metrics(spec, run)
        if reason is not None:
            reasons[run.result.strategy_id] = reason
        results.append(replace(run.result, metrics=m))
    return BacktestResult(
        experiment_id=experiment_id(spec, dataset_sha, ENGINE_VERSION),
        engine_version=ENGINE_VERSION,
        dataset_sha=dataset_sha,
        spec=spec.to_dict(),
        description=describe(spec),
        assumed=tuple(v.assumed) + tuple(sim.assumed),
        caveats=caveats.build(spec, dataset, dataset_sha, sim.strategies, reasons, fetched),
        exclusions=sim.exclusions,
        strategies=tuple(results),
    )
