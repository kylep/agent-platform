"""The engine's semantic version.

Bump it with ANY change that can alter a result for the same spec and
dataset (simulation, rounding, metrics, caveat text, output shape). It feeds
`experiment_id`, so a rerun under a new version is a new experiment; the
golden test (test_metrics.py) fails on changed output until it is bumped and
the golden files are regenerated.
"""
ENGINE_VERSION = "1.0.0"
