import pandas as pd

from app.checks.outliers import OutlierCheck


def test_flags_values_far_outside_typical_range():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5, 6, 7, 8],
        "amount": [10, 12, 11, 13, 12, 11, 10, 500],
    })
    result = OutlierCheck().run(df, {"column": "amount", "key_column": "id"})

    assert result.affected_rows == 1
    assert result.affected_row_keys == ["8"]
    assert result.passed is False


def test_passes_when_values_are_close_together():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5],
        "amount": [10, 11, 12, 13, 14],
    })
    result = OutlierCheck().run(df, {"column": "amount", "key_column": "id"})

    assert result.passed is True
    assert result.affected_rows == 0


def test_no_spread_never_flags_anything():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4, 5, 6],
        "amount": [10, 10, 10, 10, 10, 10],
    })
    result = OutlierCheck().run(df, {"column": "amount", "key_column": "id"})

    assert result.passed is True
    assert result.affected_rows == 0
