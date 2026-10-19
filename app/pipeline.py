"""
Builds and runs the check pipeline for whatever columns a dataset actually
has (see app/profiling.py), instead of assuming the demo `customers`
schema. This is the piece that makes the platform work on any CSV.
"""
import pandas as pd

from app.checks.base import CheckResult
from app.checks.duplicates import DuplicatesCheck
from app.checks.invalid_formats import InvalidFormatCheck
from app.checks.missing_values import MissingValuesCheck
from app.checks.broken_relationships import BrokenRelationshipsCheck
from app.checks.drift import DriftCheck, distribution_snapshot
from app.checks.outliers import OutlierCheck
from app.checks.schema_changes import SchemaChangeCheck
from app.checks.unexpected_values import UnexpectedValuesCheck
from app.profiling import (
    detect_format_checks,
    detect_key_column,
    detect_outlier_checks,
    detect_unexpected_value_checks,
)


def _merge_format_results(results: list[CheckResult], total_rows: int) -> CheckResult | None:
    """
    Multiple columns can each get their own format check (email, an ID
    column, etc). They're combined into one "invalid_formats" result - a
    row is affected if it fails ANY of them - so the score and dashboard
    still deal with one named check per category, regardless of how many
    columns were actually profiled.
    """
    if not results:
        return None

    affected_keys: set[str] = set()
    per_column = {}
    for result in results:
        affected_keys.update(result.affected_row_keys)
        per_column[result.details["column"]] = {
            "pattern": result.details["pattern"],
            "affected_rows": result.affected_rows,
        }

    return CheckResult(
        check_name="invalid_formats",
        passed=len(affected_keys) == 0,
        total_rows=total_rows,
        affected_rows=len(affected_keys),
        affected_row_keys=sorted(affected_keys),
        details={"columns_checked": list(per_column.keys()), "per_column": per_column},
    )


def _merge_outlier_results(results: list[CheckResult], total_rows: int) -> CheckResult | None:
    """Same one-check-per-column-merged-into-one-named-check pattern as format checks above."""
    if not results:
        return None

    affected_keys: set[str] = set()
    per_column = {}
    for result in results:
        affected_keys.update(result.affected_row_keys)
        per_column[result.details["column"]] = {
            "lower_bound": result.details["lower_bound"],
            "upper_bound": result.details["upper_bound"],
            "affected_rows": result.affected_rows,
        }

    return CheckResult(
        check_name="outliers",
        passed=len(affected_keys) == 0,
        total_rows=total_rows,
        affected_rows=len(affected_keys),
        affected_row_keys=sorted(affected_keys),
        details={"columns_checked": list(per_column.keys()), "per_column": per_column},
    )


def _merge_unexpected_value_results(results: list[CheckResult], total_rows: int) -> CheckResult | None:
    """Same one-check-per-column-merged-into-one-named-check pattern as the merges above."""
    if not results:
        return None

    affected_keys: set[str] = set()
    per_column = {}
    for result in results:
        affected_keys.update(result.affected_row_keys)
        per_column[result.details["column"]] = {
            "rare_values": result.details["rare_values"],
            "affected_rows": result.affected_rows,
        }

    return CheckResult(
        check_name="unexpected_values",
        passed=len(affected_keys) == 0,
        total_rows=total_rows,
        affected_rows=len(affected_keys),
        affected_row_keys=sorted(affected_keys),
        details={"columns_checked": list(per_column.keys()), "per_column": per_column},
    )


def _merge_drift_results(results: list[CheckResult]) -> CheckResult | None:
    """
    Same one-check-per-column pattern as the merges above, but - like
    schema_changes - drift is a per-column, not per-row, concern:
    total_rows/affected_rows here mean total/drifted columns, not data rows.
    """
    if not results:
        return None

    affected_keys: list[str] = []
    per_column = {}
    for result in results:
        affected_keys.extend(result.affected_row_keys)
        per_column[result.details["column"]] = {"psi": result.details["psi"]}

    return CheckResult(
        check_name="drift",
        passed=len(affected_keys) == 0,
        total_rows=len(results),
        affected_rows=len(affected_keys),
        affected_row_keys=affected_keys,
        details={"columns_checked": list(per_column.keys()), "per_column": per_column},
    )


def _merge_broken_relationship_results(results: list[CheckResult], total_rows: int) -> CheckResult | None:
    """Same one-check-per-relationship-merged-into-one-named-check pattern as the row-level merges above."""
    if not results:
        return None

    affected_keys: set[str] = set()
    per_column = {}
    for result in results:
        affected_keys.update(result.affected_row_keys)
        per_column[result.details["column"]] = {
            "references": result.details["references"],
            "affected_rows": result.affected_rows,
        }

    return CheckResult(
        check_name="broken_relationships",
        passed=len(affected_keys) == 0,
        total_rows=total_rows,
        affected_rows=len(affected_keys),
        affected_row_keys=sorted(affected_keys),
        details={"columns_checked": list(per_column.keys()), "per_column": per_column},
    )


def compute_distribution_snapshot(df: pd.DataFrame) -> dict[str, dict]:
    """The baseline this run hands off for a future run's drift check to compare against."""
    df = df.copy(deep=False)  # a view, not a second copy of the data: pandas copies a column only if it is changed
    key_column = detect_key_column(df)
    snapshot = {}
    for cfg in detect_outlier_checks(df, key_column):  # numeric-column selection, reused as-is
        column = cfg["column"]
        snap = distribution_snapshot(df[column])
        if snap is not None:
            snapshot[column] = snap
    return snapshot


def run_all_checks(
    df: pd.DataFrame,
    previous_schema: dict[str, str] | None = None,
    previous_distributions: dict[str, dict] | None = None,
    reference_datasets: list[tuple[str, pd.DataFrame]] | None = None,
    relationship_rules: list[dict] | None = None,
    relationship_feedback: list[dict] | None = None,
) -> list[CheckResult]:
    """
    relationship_feedback, if a list is passed in, gets filled in with one
    entry per submitted relationship rule - applied (with its result) or
    skipped (with why) - so a caller can report back exactly what happened
    with each rule instead of the rule just silently doing nothing.
    """
    # A view, not a second copy of the data (pandas copies a column only if it is
    # changed, so the caller's frame is as safe as with a full copy): for a big file
    # the full copy was the largest single use of memory in a run.
    df = df.copy(deep=False)

    key_column = detect_key_column(df)
    duplicates_config = {"key_column": key_column}
    if key_column is None:
        # No natural id-like column - fall back to a synthetic 1-based row
        # number so "which rows are affected" still means something.
        key_column = "_row_number"
        df[key_column] = (df.index + 1).astype(str)
        # That number is different on every row, so a full-row match that counted it
        # could never find a duplicate: compare only the file's own columns.
        duplicates_config = {"key_column": key_column, "ignore_columns": [key_column]}

    results = [
        MissingValuesCheck().run(df, {"key_column": key_column}),  # all columns, no config needed
        DuplicatesCheck().run(df, duplicates_config),  # full-row match, no other config needed
    ]

    format_results = [
        InvalidFormatCheck().run(df, {**cfg, "key_column": key_column})
        for cfg in detect_format_checks(df)
    ]
    merged_format_result = _merge_format_results(format_results, total_rows=len(df))
    if merged_format_result:
        results.append(merged_format_result)

    outlier_results = [
        OutlierCheck().run(df, {**cfg, "key_column": key_column})
        for cfg in detect_outlier_checks(df, key_column)
    ]
    merged_outlier_result = _merge_outlier_results(outlier_results, total_rows=len(df))
    if merged_outlier_result:
        results.append(merged_outlier_result)

    unexpected_value_results = [
        UnexpectedValuesCheck().run(df, {**cfg, "key_column": key_column})
        for cfg in detect_unexpected_value_checks(df, key_column)
    ]
    merged_unexpected_value_result = _merge_unexpected_value_results(unexpected_value_results, total_rows=len(df))
    if merged_unexpected_value_result:
        results.append(merged_unexpected_value_result)

    if previous_schema is not None:
        results.append(SchemaChangeCheck().run(df, {"previous_schema": previous_schema}))

    if previous_distributions:
        drift_results = []
        for cfg in detect_outlier_checks(df, key_column):
            column = cfg["column"]
            baseline = previous_distributions.get(column)
            if baseline is None:
                continue
            drift_results.append(DriftCheck().run(df, {"column": column, "baseline": baseline}))
        merged_drift_result = _merge_drift_results(drift_results)
        if merged_drift_result:
            results.append(merged_drift_result)

    if relationship_rules and reference_datasets:
        relationship_results = []
        for rule in relationship_rules:
            column = rule["column"]
            ref_index = rule["reference_index"]
            ref_column = rule["reference_column"]
            entry = {"column": column, "reference_column": ref_column}

            if column not in df.columns:
                entry.update(status="skipped", reason=f"column '{column}' not found in the uploaded file")
            elif ref_index >= len(reference_datasets):
                entry.update(status="skipped", reason="reference file not found")
            else:
                ref_label, ref_df = reference_datasets[ref_index]
                entry["reference_label"] = ref_label
                if ref_column not in ref_df.columns:
                    entry.update(status="skipped", reason=f"column '{ref_column}' not found in '{ref_label}'")
                else:
                    reference_values = set(ref_df[ref_column].dropna().astype(str))
                    result = BrokenRelationshipsCheck().run(
                        df,
                        {
                            "column": column,
                            "key_column": key_column,
                            "reference_values": reference_values,
                            "reference_label": ref_label,
                        },
                    )
                    relationship_results.append(result)
                    entry.update(status="applied", affected_rows=result.affected_rows, total_rows=result.total_rows)

            if relationship_feedback is not None:
                relationship_feedback.append(entry)

        merged_relationship_result = _merge_broken_relationship_results(relationship_results, total_rows=len(df))
        if merged_relationship_result:
            results.append(merged_relationship_result)

    return results
