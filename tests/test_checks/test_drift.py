import numpy as np
import pandas as pd

from app.checks.drift import DriftCheck, distribution_snapshot


def _baseline():
    np.random.seed(0)
    return distribution_snapshot(pd.Series(np.random.normal(100, 10, 500)))


def test_passes_when_distribution_is_unchanged():
    np.random.seed(1)
    df = pd.DataFrame({"amount": np.random.normal(100, 10, 500)})

    result = DriftCheck().run(df, {"column": "amount", "baseline": _baseline()})

    assert result.passed is True
    assert result.details["psi"] < 0.25


def test_flags_a_shifted_distribution():
    np.random.seed(1)
    df = pd.DataFrame({"amount": np.random.normal(160, 10, 500)})

    result = DriftCheck().run(df, {"column": "amount", "baseline": _baseline()})

    assert result.passed is False
    assert result.details["psi"] > 0.25
    assert "amount" in result.affected_row_keys[0]


def test_too_few_samples_returns_no_baseline():
    assert distribution_snapshot(pd.Series([1, 2, 3])) is None
