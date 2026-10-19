from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult

_RARE_SHARE_THRESHOLD = 0.01  # a value seen in less than this share of non-null rows is "unexpected"...
_RARE_ABSOLUTE_FLOOR = 2  # ...or, on smaller datasets, in fewer than this many rows outright


class UnexpectedValuesCheck(BaseCheck):
    """
    Flags values that are rare outliers within their own column's normal
    set of values - e.g. a stray "Pnding" in a status column that's
    otherwise "Active"/"Inactive"/"Pending". The "normal set" is learned
    from the data itself: whichever values are common enough to clear the
    rarity threshold below.
    """

    name = "unexpected_values"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        column = config["column"]
        key_column = config.get("key_column", df.columns[0])

        non_null = df[column].dropna()
        counts = non_null.value_counts()
        threshold = max(_RARE_ABSOLUTE_FLOOR, round(_RARE_SHARE_THRESHOLD * len(non_null)))
        rare_values = set(counts[counts < threshold].index)

        unexpected_mask = df[column].isin(rare_values)
        affected = df.loc[unexpected_mask, key_column].astype(str).tolist()

        return CheckResult(
            check_name=self.name,
            passed=len(affected) == 0,
            total_rows=len(df),
            affected_rows=len(affected),
            affected_row_keys=affected,
            details={"column": column, "rare_values": sorted(str(v) for v in rare_values)},
        )
