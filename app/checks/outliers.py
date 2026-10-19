from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult


class OutlierCheck(BaseCheck):
    """
    Flags numeric values that fall far outside a column's typical range,
    using Tukey's IQR rule: outside [Q1 - 1.5*IQR, Q3 + 1.5*IQR]. The
    "typical range" is learned per-column from the data itself, so this
    needs no domain config to work on any numeric column.
    """

    name = "outliers"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        column = config["column"]
        key_column = config.get("key_column", df.columns[0])

        series = df[column]
        q1, q3 = series.quantile(0.25), series.quantile(0.75)
        iqr = q3 - q1

        if iqr == 0:
            # No spread to compare against (e.g. a column that's almost all
            # one value) - flagging every deviation would just be relabeling
            # normal variation as "outliers".
            return CheckResult(
                check_name=self.name,
                passed=True,
                total_rows=len(df),
                affected_rows=0,
                affected_row_keys=[],
                details={"column": column, "lower_bound": float(q1), "upper_bound": float(q3)},
            )

        lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
        outlier_mask = series.notna() & ((series < lower) | (series > upper))
        affected = df.loc[outlier_mask, key_column].astype(str).tolist()

        return CheckResult(
            check_name=self.name,
            passed=len(affected) == 0,
            total_rows=len(df),
            affected_rows=len(affected),
            affected_row_keys=affected,
            details={"column": column, "lower_bound": float(lower), "upper_bound": float(upper)},
        )
