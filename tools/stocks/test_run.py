"""Pure-function tests (no network — CI runs these against the baked deps)."""
import pandas as pd

from run import history_to_dict, shape_output, summarize


def _frame():
    idx = pd.to_datetime(["2026-08-04", "2026-08-05", "2026-08-06"])
    return pd.DataFrame({
        "Open": [100.0, 102.0, 101.5],
        "High": [103.0, 104.0, 102.0],
        "Low": [99.0, 101.0, 100.0],
        "Close": [102.123456, 103.0, 101.0],
        "Volume": [1_000_000.0, 1_200_000.0, float("nan")],
    }, index=idx)


def test_history_normalization():
    h = history_to_dict(_frame())
    assert list(h) == ["2026-08-04", "2026-08-05", "2026-08-06"]
    assert h["2026-08-04"]["close"] == 102.1235  # rounded to 4dp
    assert h["2026-08-04"]["volume"] == 1_000_000
    assert h["2026-08-06"]["volume"] is None  # NaN → None


def test_summary_math():
    s = summarize(history_to_dict(_frame()))
    assert s["days"] == 3
    assert s["start_date"] == "2026-08-04" and s["end_date"] == "2026-08-06"
    assert s["return_pct"] == round((101.0 / 102.1235 - 1) * 100, 2)
    assert s["high"] == 104.0 and s["low"] == 99.0


def test_shape_full_vs_closes_only():
    h = history_to_dict(_frame())
    assert "history" in shape_output("T", "1mo", h)
    long = shape_output("T", "1y", h)
    assert "history" not in long and long["closes"]["2026-08-05"] == 103.0
