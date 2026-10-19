import re
from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult


class InvalidFormatCheck(BaseCheck):
    """
    Flags rows where a column's value doesn't match a required regex
    pattern. Config: {"column": "customer_id", "pattern": r"^CUST-\\d{6}$"}
    """

    name = "invalid_formats"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        column = config["column"]
        pattern = re.compile(config["pattern"])
        key_column = config.get("key_column", df.columns[0])

        # A null isn't "badly formatted" - it's missing, a different problem
        # that missing_values already catches. Only non-null values that
        # don't match the pattern count as invalid here.
        non_null_mask = df[column].notna()
        matches = df[column].astype(str).str.match(pattern)
        invalid_mask = non_null_mask & ~matches
        affected = df.loc[invalid_mask, key_column].astype(str).tolist()

        return CheckResult(
            check_name=self.name,
            passed=len(affected) == 0,
            total_rows=len(df),
            affected_rows=len(affected),
            affected_row_keys=affected,
            details={"column": column, "pattern": config["pattern"]},
        )
