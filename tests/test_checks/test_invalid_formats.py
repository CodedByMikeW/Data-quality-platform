import pandas as pd

from app.checks.invalid_formats import InvalidFormatCheck


def test_flags_ids_that_dont_match_pattern():
    df = pd.DataFrame({
        "customer_id": ["CUST-000001", "000002", "CUST-000003", "CUST-abcdef"],
    })
    result = InvalidFormatCheck().run(
        df, {"column": "customer_id", "pattern": r"^CUST-\d{6}$", "key_column": "customer_id"}
    )

    assert result.affected_rows == 2
    assert set(result.affected_row_keys) == {"000002", "CUST-abcdef"}


def test_passes_when_all_valid():
    df = pd.DataFrame({"customer_id": ["CUST-000001", "CUST-000002"]})
    result = InvalidFormatCheck().run(
        df, {"column": "customer_id", "pattern": r"^CUST-\d{6}$", "key_column": "customer_id"}
    )

    assert result.passed is True


def test_nulls_are_not_flagged_as_invalid_format():
    # A missing value is a different problem (missing_values catches it) -
    # it shouldn't also count against invalid_formats.
    df = pd.DataFrame({"customer_id": ["CUST-000001", None, "CUST-000003"]})
    result = InvalidFormatCheck().run(
        df, {"column": "customer_id", "pattern": r"^CUST-\d{6}$", "key_column": "customer_id"}
    )

    assert result.affected_rows == 0
