import pandas as pd

from app.checks.duplicates import DuplicatesCheck


def test_flags_matching_subset_as_duplicates():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "email": ["a@x.com", "b@x.com", "a@x.com", "d@x.com"],
        "name": ["A", "B", "A", "D"],
    })
    result = DuplicatesCheck().run(df, {"subset": ["email", "name"], "key_column": "id"})

    assert result.affected_rows == 2
    assert set(result.affected_row_keys) == {"1", "3"}
    assert result.details["duplicate_groups"] == 1


def test_passes_when_no_duplicates():
    df = pd.DataFrame({
        "id": [1, 2, 3],
        "email": ["a@x.com", "b@x.com", "c@x.com"],
        "name": ["A", "B", "C"],
    })
    result = DuplicatesCheck().run(df, {"subset": ["email", "name"], "key_column": "id"})

    assert result.passed is True
    assert result.affected_rows == 0


def test_a_full_row_match_can_leave_out_a_column_that_only_numbers_the_rows():
    # What the pipeline does for a file with no id column: the row number it makes up is different on every
    # row, so it is left out of the comparison - counted, it would hide every duplicate.
    df = pd.DataFrame({
        "city": ["Paris", "Paris", "Rome", "Oslo"],
        "amount": [10, 10, 5, 7],
        "_row_number": ["1", "2", "3", "4"],
    })
    result = DuplicatesCheck().run(df, {"key_column": "_row_number", "ignore_columns": ["_row_number"]})

    assert result.passed is False
    assert result.affected_rows == 2
    assert result.affected_row_keys == ["1", "2"]
    assert result.details["compared_columns"] == "all_columns"  # still the whole row, as far as the file's own columns go
    assert result.details["duplicate_groups"] == 1


def test_rows_that_differ_in_any_compared_column_are_not_duplicates():
    df = pd.DataFrame({
        "city": ["Paris", "Paris", "Rome"],
        "amount": [10, 11, 5],
        "_row_number": ["1", "2", "3"],
    })
    result = DuplicatesCheck().run(df, {"key_column": "_row_number", "ignore_columns": ["_row_number"]})

    assert result.passed is True
    assert result.affected_rows == 0


def test_an_explicit_subset_wins_over_ignore_columns():
    df = pd.DataFrame({
        "email": ["a@x.com", "a@x.com", "b@x.com"],
        "name": ["A", "Z", "B"],
        "n": ["1", "2", "3"],
    })
    result = DuplicatesCheck().run(df, {"subset": ["email"], "ignore_columns": ["email"], "key_column": "n"})

    assert result.affected_row_keys == ["1", "2"]
    assert result.details["compared_columns"] == ["email"]


def test_ignoring_a_column_the_file_does_not_have_changes_nothing():
    df = pd.DataFrame({"a": [1, 1, 2]})
    result = DuplicatesCheck().run(df, {"key_column": "a", "ignore_columns": ["_row_number"]})

    assert result.affected_rows == 2


def test_with_every_column_ignored_there_is_nothing_to_compare_and_it_passes():
    df = pd.DataFrame({"_row_number": ["1", "2"]})
    result = DuplicatesCheck().run(df, {"key_column": "_row_number", "ignore_columns": ["_row_number"]})

    assert result.passed is True
    assert result.affected_rows == 0
    assert result.details["duplicate_groups"] == 0


def test_duplicate_rows_with_a_blank_cell_still_count_as_groups():
    df = pd.DataFrame({
        "id": [1, 2, 3, 4],
        "city": ["Paris", "Paris", "Rome", "Rome"],
        "note": [None, None, "x", "x"],
    })
    result = DuplicatesCheck().run(df, {"subset": ["city", "note"], "key_column": "id"})

    assert result.affected_rows == 4
    assert result.details["duplicate_groups"] == 2  # not 1: the rows with the blank are a group too
