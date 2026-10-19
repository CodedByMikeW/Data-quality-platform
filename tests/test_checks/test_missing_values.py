import pandas as pd

from app.checks.missing_values import MissingValuesCheck


def test_flags_rows_with_nulls():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "email": ["a@x.com", None, "c@x.com", None],
    })
    result = MissingValuesCheck().run(df, {"columns": ["email"], "key_column": "id"})

    assert result.affected_rows == 2
    assert set(result.affected_row_keys) == {"2", "4"}
    assert result.passed is False
    assert result.pass_rate == 0.5


def test_passes_when_no_nulls():
    df = pd.DataFrame({"id": [1, 2], "email": ["a@x.com", "b@x.com"]})
    result = MissingValuesCheck().run(df, {"columns": ["email"], "key_column": "id"})

    assert result.passed is True
    assert result.affected_rows == 0
    assert result.pass_rate == 1.0
