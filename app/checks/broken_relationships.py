from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult


class BrokenRelationshipsCheck(BaseCheck):
    """
    Flags rows whose value in a foreign-key-style column doesn't exist in
    the referenced column of another, separately-uploaded dataset - e.g. an
    orders.customer_id with no matching row in customers.customer_id. Needs
    that other dataset's values handed in directly (reference_values) -
    past runs only persist aggregated results, not raw rows, so this check
    only works when the related file is uploaded alongside this one.
    """

    name = "broken_relationships"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        column = config["column"]
        reference_values: set[str] = config["reference_values"]
        reference_label = config.get("reference_label", "")
        key_column = config.get("key_column", df.columns[0])

        # A null isn't a "broken" reference - that's missing_values' job.
        non_null_mask = df[column].notna()
        valid_mask = df[column].astype(str).isin(reference_values)
        broken_mask = non_null_mask & ~valid_mask
        affected = df.loc[broken_mask, key_column].astype(str).tolist()

        return CheckResult(
            check_name=self.name,
            passed=len(affected) == 0,
            total_rows=len(df),
            affected_rows=len(affected),
            affected_row_keys=affected,
            details={"column": column, "references": reference_label},
        )
