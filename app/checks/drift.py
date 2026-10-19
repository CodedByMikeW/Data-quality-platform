from typing import Optional

import numpy as np
import pandas as pd

from .base import BaseCheck, CheckResult

_N_BINS = 10
_PSI_DRIFT_THRESHOLD = 0.25  # conventional cutoff: PSI > 0.25 is considered significant drift
_EPSILON = 1e-4  # avoids divide-by-zero / log(0) when a bin has no observations


def distribution_snapshot(series: pd.Series) -> Optional[dict]:
    """
    Builds the baseline a future run's drift check will compare against:
    decile bin edges for this column's current values, plus what share of
    values actually fell in each bin (not assumed to be a perfect 10% each,
    since duplicate values can collapse adjacent bins together).
    """
    non_null = series.dropna()
    if len(non_null) < _N_BINS * 2:
        return None

    try:
        binned = pd.qcut(non_null, q=_N_BINS, duplicates="drop")
    except ValueError:
        return None

    categories = binned.cat.categories
    if len(categories) < 2:
        return None

    edges = [float(categories[0].left)] + [float(c.right) for c in categories]
    proportions = binned.value_counts(normalize=True, sort=False).tolist()
    return {"bin_edges": edges, "proportions": proportions}


class DriftCheck(BaseCheck):
    """
    Flags a numeric column whose values have shifted meaningfully from an
    earlier baseline, using the Population Stability Index (PSI) - the
    standard measure for this in production ML/data systems. The baseline
    is this same column's own distribution from a previous run, binned into
    deciles (see distribution_snapshot); PSI compares how much of the
    current data's mass has moved between those bins versus the baseline.
    PSI > 0.25 is the conventional "significant drift" cutoff.
    """

    name = "drift"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        column = config["column"]
        baseline = config["baseline"]

        edges = list(baseline["bin_edges"])
        expected = np.array(baseline["proportions"], dtype=float)

        # Widen the outer edges so current values outside the baseline's
        # observed range still land in the nearest bin instead of being
        # dropped - a real range shift should count as drift, not get
        # silently excluded from the comparison.
        edges[0] = -np.inf
        edges[-1] = np.inf

        non_null = df[column].dropna()
        current_binned = pd.cut(non_null, bins=edges)
        actual = current_binned.value_counts(normalize=True, sort=False).to_numpy(dtype=float)

        expected_safe = np.clip(expected, _EPSILON, None)
        actual_safe = np.clip(actual, _EPSILON, None)
        psi = float(np.sum((actual_safe - expected_safe) * np.log(actual_safe / expected_safe)))

        drifted = psi > _PSI_DRIFT_THRESHOLD

        return CheckResult(
            check_name=self.name,
            passed=not drifted,
            total_rows=1,
            affected_rows=1 if drifted else 0,
            affected_row_keys=[f"column '{column}' distribution shifted (PSI {psi:.2f})"] if drifted else [],
            details={"column": column, "psi": round(psi, 4)},
        )
