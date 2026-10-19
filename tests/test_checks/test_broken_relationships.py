import pandas as pd

from app.checks.broken_relationships import BrokenRelationshipsCheck


def test_flags_ids_missing_from_the_reference_dataset():
    orders = pd.DataFrame({
        "order_id": [1, 2, 3, 4],
        "customer_id": ["CUST-001", "CUST-002", "CUST-999", "CUST-001"],
    })
    result = BrokenRelationshipsCheck().run(orders, {
        "column": "customer_id",
        "key_column": "order_id",
        "reference_values": {"CUST-001", "CUST-002"},
        "reference_label": "customers.csv",
    })

    assert result.affected_rows == 1
    assert result.affected_row_keys == ["3"]
    assert result.passed is False


def test_passes_when_every_id_exists_in_the_reference_dataset():
    orders = pd.DataFrame({
        "order_id": [1, 2],
        "customer_id": ["CUST-001", "CUST-002"],
    })
    result = BrokenRelationshipsCheck().run(orders, {
        "column": "customer_id",
        "key_column": "order_id",
        "reference_values": {"CUST-001", "CUST-002"},
        "reference_label": "customers.csv",
    })

    assert result.passed is True
    assert result.affected_rows == 0


def test_null_references_are_not_flagged():
    orders = pd.DataFrame({
        "order_id": [1, 2],
        "customer_id": ["CUST-001", None],
    })
    result = BrokenRelationshipsCheck().run(orders, {
        "column": "customer_id",
        "key_column": "order_id",
        "reference_values": {"CUST-001"},
        "reference_label": "customers.csv",
    })

    assert result.passed is True
    assert result.affected_rows == 0
