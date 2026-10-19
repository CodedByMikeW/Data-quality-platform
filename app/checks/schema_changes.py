from typing import Optional

import pandas as pd

from .base import BaseCheck, CheckResult


def schema_of(df: pd.DataFrame) -> dict[str, str]:
    """The {column_name: dtype} snapshot stored per run and diffed against next time."""
    return {str(col): str(dtype) for col, dtype in df.dtypes.items()}


class SchemaChangeCheck(BaseCheck):
    """
    Flags when this run's columns don't match the previous run's for the
    same dataset - a column added, removed, or changed type. There's
    nothing to compare against on a dataset's first-ever run, so this only
    produces a result from the second run onward (see pipeline.run_all_checks).

    Unlike the row-level checks, "affected" here means changed columns, not
    changed rows - there's no single row a schema change belongs to, so
    total_rows/affected_rows are reused to mean total/changed columns. The
    generic CheckResult shape (pass rate, affected list) still applies, it's
    just counting a different kind of thing.
    """

    name = "schema_changes"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        config = config or {}
        previous_schema: dict[str, str] = config["previous_schema"]
        current_schema = schema_of(df)

        added = sorted(set(current_schema) - set(previous_schema))
        removed = sorted(set(previous_schema) - set(current_schema))
        changed_type = sorted(
            col
            for col in current_schema.keys() & previous_schema.keys()
            if current_schema[col] != previous_schema[col]
        )

        changes = (
            [f"column '{c}' added" for c in added]
            + [f"column '{c}' removed" for c in removed]
            + [f"column '{c}' changed type: {previous_schema[c]} -> {current_schema[c]}" for c in changed_type]
        )

        total_columns = len(set(current_schema) | set(previous_schema))

        return CheckResult(
            check_name=self.name,
            passed=len(changes) == 0,
            total_rows=total_columns,
            affected_rows=len(changes),
            affected_row_keys=changes,
            details={
                "added_columns": added,
                "removed_columns": removed,
                "changed_type_columns": changed_type,
            },
        )
