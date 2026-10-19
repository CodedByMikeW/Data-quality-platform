from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult


class MissingValuesCheck(BaseCheck):
    """Flags rows with a null in any of the given columns (default: all columns)."""

    name = "missing_values"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        columns = config.get("columns", list(df.columns))
        key_column = config.get("key_column", df.columns[0])

        mask = df[columns].isna().any(axis=1)
        affected = df.loc[mask, key_column].astype(str).tolist()

        return CheckResult(
            check_name=self.name,
            passed=len(affected) == 0,
            total_rows=len(df),
            affected_rows=len(affected),
            affected_row_keys=affected,
            details={
                "columns_checked": columns,
                "null_counts_by_column": df[columns].isna().sum().to_dict(),
            },
        )
