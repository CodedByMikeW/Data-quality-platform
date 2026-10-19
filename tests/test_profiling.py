import pandas as pd

from app.profiling import detect_key_column, detect_format_checks


def test_detects_id_suffixed_column_as_key():
    df = pd.DataFrame({"customer_id": ["a"], "name": ["b"]})
    assert detect_key_column(df) == "customer_id"


def test_detects_literal_id_column_as_key():
    df = pd.DataFrame({"id": ["a"], "notes": ["b"]})
    assert detect_key_column(df) == "id"


def test_no_key_column_when_nothing_looks_like_an_id():
    df = pd.DataFrame({"name": ["a"], "amount": [1]})
    assert detect_key_column(df) is None


def test_email_named_column_gets_an_email_pattern():
    df = pd.DataFrame({"user_email": [f"person{i}@x.com" for i in range(10)]})
    checks = detect_format_checks(df)
    assert len(checks) == 1
    assert checks[0]["column"] == "user_email"
    assert "@" in checks[0]["pattern"]


def test_consistent_shape_column_gets_a_pattern():
    # 20 values that all share one shape - well above the confidence
    # threshold - should get picked up as format-checkable.
    df = pd.DataFrame({"sku": [f"SKU-{i:04d}" for i in range(20)]})
    checks = detect_format_checks(df)
    assert len(checks) == 1
    assert checks[0]["column"] == "sku"


def test_free_text_column_is_not_format_checked():
    # Real names don't share one structural shape - no dominant pattern,
    # so this should be left alone rather than produce false positives.
    df = pd.DataFrame({"name": ["Alice Johnson", "Bo Li", "Charlie", "Dana-Marie Reyes", "E"]})
    checks = detect_format_checks(df)
    assert checks == []


def test_too_few_values_are_not_format_checked():
    df = pd.DataFrame({"code": ["AB-01", "AB-02"]})
    assert detect_format_checks(df) == []
