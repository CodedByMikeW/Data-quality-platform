import pandas as pd

from app.checks.unexpected_values import UnexpectedValuesCheck


def test_flags_a_stray_value_outside_the_normal_set():
    df = pd.DataFrame({
        "id": range(1, 11),
        "status": ["active"] * 5 + ["inactive"] * 4 + ["Pnding"],
    })
    result = UnexpectedValuesCheck().run(df, {"column": "status", "key_column": "id"})

    assert result.affected_rows == 1
    assert result.affected_row_keys == ["10"]
    assert result.passed is False


def test_passes_when_every_value_is_common():
    df = pd.DataFrame({
        "id": range(1, 11),
        "status": ["active"] * 5 + ["inactive"] * 5,
    })
    result = UnexpectedValuesCheck().run(df, {"column": "status", "key_column": "id"})

    assert result.passed is True
    assert result.affected_rows == 0
