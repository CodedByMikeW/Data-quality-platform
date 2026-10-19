"""
Turns one stored check run into a plain-text report that can be saved to
Knowledge Search, so "how did the customers data do last week?" becomes a
searchable question.

Deliberately a SUMMARY: scores, per-check counts, and column names/types -
never row values and never row identifiers. That stays true now that the
uploaded rows can be saved as well: they go in as their own document (see
data_filename), at the access level the person chose, so the numbers and the
rows are separate, visible decisions and the report never quietly turns into a
second copy of the data (customer emails, IDs and so on). The affected-row
keys stay where they already live, on the dashboard's per-check drill-down.
"""
from datetime import datetime, timezone
from typing import Any

# Same display names the dashboard uses (CHECK_META in static/app.js), in the
# same order as the score weights, so a report reads like the dashboard and
# two reports of the same dataset line up line-for-line.
CHECK_LABELS = {
    "missing_values": "Missing Values",
    "duplicates": "Duplicates",
    "invalid_formats": "Invalid Formats",
    "schema_changes": "Schema Changes",
    "outliers": "Outliers",
    "drift": "Data Drift",
    "broken_relationships": "Broken Relationships",
    "unexpected_values": "Unexpected Values",
}

# These two count columns, not rows (see SchemaChangeCheck / _merge_drift_results),
# so "114 of 3,000 rows affected" would be wrong for them.
_COLUMN_CHECK_VERBS = {"schema_changes": "changed", "drift": "drifted"}

# Mirror the dashboard's thresholds (static/app.js) so the report and the
# screen never disagree about whether a run passed.
CERTIFIED_THRESHOLD = 90  # overall score at or above this -> CERTIFIED
CHECK_PASS_PERCENT = 95  # a check at or above this -> PASS
CHECK_WARN_PERCENT = 85  # at or above this (but below PASS) -> WARN, otherwise FAIL

_MAX_COLUMNS_LISTED = 40
_MAX_CHANGES_LISTED = 20


def _label(dataset_name: str) -> str:
    return " ".join(str(dataset_name).split())  # collapse stray whitespace / newlines


def report_filename(dataset_name: str) -> str:
    """
    One filename per dataset label. Knowledge Search versions by filename, so
    re-running the same dataset adds a new version of ONE document instead of
    piling up a new document per run.
    """
    return f"quality-report-{_label(dataset_name)}.txt"


def data_filename(dataset_name: str) -> str:
    """The uploaded rows, saved next to the report: same label, so the pair sorts and versions together."""
    return f"quality-data-{_label(dataset_name)}.csv"


def _timestamp(value: datetime | None) -> str:
    if value is None:
        return "unknown time"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)  # stored as UTC; SQLite hands it back naive
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _clean(name: Any) -> str:
    return " ".join(str(name).split())


def _join_limited(items: list[str], limit: int) -> str:
    """Comma-joins the first `limit` items; says how many were left out rather than dumping hundreds."""
    shown = ", ".join(items[:limit])
    extra = len(items) - limit
    return shown + (f" (+{extra} more)" if extra > 0 else "")


def _check_status(pass_percent: float) -> str:
    if pass_percent >= CHECK_PASS_PERCENT:
        return "PASS"
    if pass_percent >= CHECK_WARN_PERCENT:
        return "WARN"
    return "FAIL"


def _check_line(check_name: str, data: dict) -> tuple[str, str]:
    """(status, one-line description) for a single check's stored breakdown entry."""
    label = CHECK_LABELS.get(check_name, check_name)
    pass_percent = float(data.get("pass_rate", 1.0)) * 100
    affected = int(data.get("affected_rows", 0))
    total = int(data.get("total_rows", 0))
    weight = data.get("weight", 0)
    status = _check_status(pass_percent)

    verb = _COLUMN_CHECK_VERBS.get(check_name)
    counts = (
        f"{affected:,} of {total:,} columns {verb}" if verb else f"{affected:,} of {total:,} rows affected"
    )
    return status, f"{label}: {status} - {pass_percent:.1f}% passed, {counts} (weight {weight})"


def _schema_section(run: Any, previous_run: Any | None) -> list[str]:
    schema: dict | None = getattr(run, "schema_snapshot", None)
    if not schema:
        return []

    lines = ["SCHEMA"]
    columns = [f"{_clean(col)} ({dtype})" for col, dtype in schema.items()]
    lines.append(f"{len(columns)} columns: {_join_limited(columns, _MAX_COLUMNS_LISTED)}")

    previous_schema: dict | None = getattr(previous_run, "schema_snapshot", None) if previous_run else None
    if previous_run is None:
        lines.append("First run for this dataset, so there is no earlier schema to compare against.")
    elif previous_schema is None:
        lines.append(f"Run #{previous_run.id} has no stored schema to compare against.")
    else:
        # Same comparison SchemaChangeCheck makes: columns added, removed, or retyped.
        added = sorted(set(schema) - set(previous_schema))
        removed = sorted(set(previous_schema) - set(schema))
        retyped = sorted(c for c in schema.keys() & previous_schema.keys() if schema[c] != previous_schema[c])
        if not (added or removed or retyped):
            lines.append(f"No schema changes since run #{previous_run.id}.")
        else:
            lines.append(f"Schema changes since run #{previous_run.id}:")
            if added:
                lines.append("- Added: " + _join_limited([f"'{_clean(c)}'" for c in added], _MAX_CHANGES_LISTED))
            if removed:
                lines.append("- Removed: " + _join_limited([f"'{_clean(c)}'" for c in removed], _MAX_CHANGES_LISTED))
            if retyped:
                changes = [f"'{_clean(c)}' ({previous_schema[c]} -> {schema[c]})" for c in retyped]
                lines.append("- Changed type: " + _join_limited(changes, _MAX_CHANGES_LISTED))
    return lines


def build_report(dataset_name: str, run: Any, previous_run: Any | None = None) -> str:
    """
    Plain text, no markup, so it reads the same in the document list, in a
    search snippet and in the semantic-search chunks. Fully determined by the
    stored run (and the run before it), so building it twice gives identical
    text - which is what lets a retried save be recognised as a repeat.
    """
    breakdown: dict = run.breakdown or {}
    score = float(run.score)
    verdict = "CERTIFIED" if score >= CERTIFIED_THRESHOLD else "NEEDS REVIEW"

    lines = [
        f"DATA QUALITY REPORT: {_clean(dataset_name)}",
        "",
        f"Dataset: {_clean(dataset_name)}",
        f"Run: #{run.id} on {_timestamp(run.created_at)}",
        f"Rows checked: {int(run.row_count):,}",
        f"Quality score: {score:.1f} / 100 ({verdict})",
    ]

    if previous_run is not None:
        change = score - float(previous_run.score)
        lines.append(
            f"Previous run: #{previous_run.id} scored {float(previous_run.score):.1f} on "
            f"{_timestamp(previous_run.created_at)} (change: {change:+.1f})"
        )
    else:
        lines.append("Previous run: none - this is the first run for this dataset.")

    # Known checks in the dashboard's order first, then anything unrecognised
    # (a check added later still shows up, just under its raw name).
    ordered = [name for name in CHECK_LABELS if name in breakdown]
    ordered += [name for name in breakdown if name not in CHECK_LABELS]

    lines += ["", "CHECK RESULTS"]
    statuses: dict[str, str] = {}
    for name in ordered:
        status, line = _check_line(name, breakdown[name])
        statuses[name] = status
        lines.append(line)

    def label_of(name: str) -> str:
        return CHECK_LABELS.get(name, name)

    needs_attention = [f"{label_of(n)} ({s})" for n, s in statuses.items() if s != "PASS"]
    clean = [label_of(n) for n, s in statuses.items() if s == "PASS"]
    not_run = [label for name, label in CHECK_LABELS.items() if name not in breakdown]

    lines.append("")
    lines.append("Needs attention: " + (", ".join(needs_attention) if needs_attention else "nothing - every check passed."))
    if clean:
        lines.append("Passing: " + ", ".join(clean))
    if not_run:
        lines.append("Checks not run this time: " + ", ".join(not_run))

    schema_lines = _schema_section(run, previous_run)
    if schema_lines:
        lines += [""] + schema_lines

    lines += [
        "",
        "ABOUT THIS REPORT",
        "Summary generated by the Enterprise Data Quality Platform from the stored results of this run. "
        "It contains counts and column names only - no row values and no row identifiers.",
    ]
    return "\n".join(lines)
