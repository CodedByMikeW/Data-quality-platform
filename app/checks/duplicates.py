from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult


class DuplicatesCheck(BaseCheck):
    """
    Flags rows that duplicate another row. `subset` in config restricts
    comparison to specific columns (e.g. just email+name); omit it to
    require a full-row match. `ignore_columns` leaves columns out of a
    full-row match: the pipeline uses it for the row number it makes up when a
    file has no id column - that number is different on every row, so a match
    that counted it could never find a duplicate. (A `subset` wins over it.)
    """

    name = "duplicates"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        subset = config.get("subset")
        key_column = config.get("key_column", df.columns[0])

        compared = subset
        ignored = [column for column in config.get("ignore_columns") or [] if column in df.columns]
        if compared is None and ignored:
            compared = [column for column in df.columns if column not in ignored]

        if compared is not None and len(compared) == 0:
            dup_mask = pd.Series(False, index=df.index)  # nothing left to compare
        else:
            dup_mask = df.duplicated(subset=compared, keep=False)
        affected_df = df.loc[dup_mask]
        affected = affected_df[key_column].astype(str).tolist()

        group_cols = compared or list(df.columns)
        # dropna=False: rows with a blank cell still form a group (the default would drop them from the count)
        duplicate_groups = affected_df.groupby(group_cols, dropna=False).ngroups if not affected_df.empty else 0

        return CheckResult(
            check_name=self.name,
            passed=len(affected) == 0,
            total_rows=len(df),
            affected_rows=len(affected),
            affected_row_keys=affected,
            details={
                "compared_columns": subset or "all_columns",
                "duplicate_groups": duplicate_groups,
            },
        )
