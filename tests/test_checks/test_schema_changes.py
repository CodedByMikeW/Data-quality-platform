import pandas as pd

from app.checks.schema_changes import SchemaChangeCheck, schema_of


def test_passes_when_schema_is_unchanged():
    df = pd.DataFrame({"id": [1, 2], "email": ["a@x.com", "b@x.com"]})
    result = SchemaChangeCheck().run(df, {"previous_schema": schema_of(df)})

    assert result.passed is True
    assert result.affected_rows == 0


def test_flags_an_added_column():
    previous = pd.DataFrame({"id": [1, 2], "email": ["a@x.com", "b@x.com"]})
    current = previous.assign(phone=["555-0100", "555-0101"])

    result = SchemaChangeCheck().run(current, {"previous_schema": schema_of(previous)})

    assert result.passed is False
    assert result.affected_row_keys == ["column 'phone' added"]


def test_flags_a_removed_column():
    previous = pd.DataFrame({"id": [1, 2], "email": ["a@x.com", "b@x.com"]})
    current = previous.drop(columns=["email"])

    result = SchemaChangeCheck().run(current, {"previous_schema": schema_of(previous)})

    assert result.passed is False
    assert result.affected_row_keys == ["column 'email' removed"]


def test_flags_a_changed_column_type():
    previous = pd.DataFrame({"id": [1, 2], "amount": [10, 20]})
    current = pd.DataFrame({"id": [1, 2], "amount": [10.5, 20.5]})

    result = SchemaChangeCheck().run(current, {"previous_schema": schema_of(previous)})

    assert result.passed is False
    assert len(result.affected_row_keys) == 1
    assert "amount" in result.affected_row_keys[0]
