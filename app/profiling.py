"""
Turns an arbitrary DataFrame into check configuration, instead of assuming
the demo `customers` schema. This is what lets the platform run against
any CSV, not just the seeded demo data.

Two things get auto-detected:
1. A key column (for reporting *which* row an issue belongs to) - a column
   literally named like an id, or a synthetic row number if none exists.
2. Which columns are worth format-checking, and what pattern to check them
   against - a real @-format check for anything named like an email, and a
   general structural-pattern detector for everything else (built from
   whatever shape most of the column's values already share, e.g. a
   consistent "LETTERS-000000" pattern). Free-text columns like names, or
   columns with genuinely inconsistent formatting like phone numbers,
   correctly find no dominant shape and are left unchecked rather than
   producing false positives.
"""
import itertools
import re

import pandas as pd

_ID_NAME_PATTERN = re.compile(r"(^|_)id$", re.IGNORECASE)
_EMAIL_NAME_HINTS = ("email", "e_mail")
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

_SHAPE_DOMINANCE_THRESHOLD = 0.9  # this share of non-null values must share one shape
_SHAPE_MIN_SAMPLES = 5  # too few values to trust a "dominant" pattern below this

_OUTLIER_MIN_SAMPLES = 5  # too few values to have a meaningful "typical range"
_OUTLIER_MIN_UNIQUE = 5  # fewer distinct values means it's really categorical (e.g. a status code), not continuous

_UNEXPECTED_MIN_SAMPLES = 30  # too few values to trust any value's frequency as "normal"
_UNEXPECTED_MIN_UNIQUE = 2  # a single-value column has nothing to be unexpected relative to
_UNEXPECTED_MAX_UNIQUE = 20  # more distinct values than this is free text, not a fixed category set


def detect_key_column(df: pd.DataFrame) -> str | None:
    """A column literally named (or ending in) 'id', if one exists."""
    for col in df.columns:
        if _ID_NAME_PATTERN.search(str(col)):
            return col
    return None


def _shape_and_pattern(value: str) -> tuple[str, str]:
    """
    Splits a value into alternating digit/non-digit runs and builds both a
    shape key (for counting how many values share a shape) and the regex
    that matches that exact shape, in parallel - so there's no fragile
    reverse-engineering from a display string back into a pattern later.
    For example "CUST-000123" becomes shape "CUST-#6" (a literal "CUST-"
    prefix plus a run of 6 digits), paired with the matching regex.
    """
    shape_parts = []
    pattern_parts = []
    for is_digit, group in itertools.groupby(str(value), key=str.isdigit):
        chunk = "".join(group)
        if is_digit:
            shape_parts.append(f"#{len(chunk)}")
            pattern_parts.append(rf"\d{{{len(chunk)}}}")
        else:
            shape_parts.append(chunk)
            pattern_parts.append(re.escape(chunk))
    return "".join(shape_parts), "^" + "".join(pattern_parts) + "$"


def _dominant_shape_pattern(series: pd.Series) -> str | None:
    non_null = series.dropna().astype(str)
    if len(non_null) < _SHAPE_MIN_SAMPLES:
        return None

    shape_counts: dict[str, int] = {}
    shape_to_pattern: dict[str, str] = {}
    for value in non_null:
        shape, pattern = _shape_and_pattern(value)
        shape_counts[shape] = shape_counts.get(shape, 0) + 1
        shape_to_pattern.setdefault(shape, pattern)

    top_shape, top_count = max(shape_counts.items(), key=lambda kv: kv[1])
    if top_count / len(non_null) < _SHAPE_DOMINANCE_THRESHOLD:
        return None
    return shape_to_pattern[top_shape]


def detect_format_checks(df: pd.DataFrame) -> list[dict]:
    """Returns [{"column": ..., "pattern": ...}, ...] for columns worth format-checking."""
    checks = []
    for col in df.columns:
        lower = str(col).lower()
        if any(hint in lower for hint in _EMAIL_NAME_HINTS):
            checks.append({"column": col, "pattern": _EMAIL_PATTERN})
            continue

        pattern = _dominant_shape_pattern(df[col])
        if pattern:
            checks.append({"column": col, "pattern": pattern})
    return checks


def detect_outlier_checks(df: pd.DataFrame, key_column: str | None = None) -> list[dict]:
    """
    Returns [{"column": ...}, ...] for numeric columns worth outlier-checking.
    Excludes the key column (an id has no "typical value" to deviate from)
    and low-cardinality numeric columns (a status code or a boolean-as-int
    isn't a continuous quantity - flagging its rarer value as an "outlier"
    would just be relabeling normal categorical variation).
    """
    checks = []
    for col in df.columns:
        if col == key_column or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        non_null = df[col].dropna()
        if len(non_null) < _OUTLIER_MIN_SAMPLES or non_null.nunique() < _OUTLIER_MIN_UNIQUE:
            continue
        checks.append({"column": col})
    return checks


def detect_unexpected_value_checks(df: pd.DataFrame, key_column: str | None = None) -> list[dict]:
    """
    Returns [{"column": ...}, ...] for text/categorical columns worth
    checking for stray values - a column with a small, mostly-fixed set of
    values (a status, a plan tier, a country code) where a handful of rows
    use something outside that set. Numeric columns are outliers' job, not
    this one; free-text columns (names, addresses) have no fixed "expected
    set" to begin with and are correctly left alone.
    """
    checks = []
    for col in df.columns:
        if col == key_column or pd.api.types.is_numeric_dtype(df[col]):
            continue
        non_null = df[col].dropna()
        if len(non_null) < _UNEXPECTED_MIN_SAMPLES:
            continue
        nunique = non_null.nunique()
        if nunique < _UNEXPECTED_MIN_UNIQUE or nunique > _UNEXPECTED_MAX_UNIQUE:
            continue
        checks.append({"column": col})
    return checks
