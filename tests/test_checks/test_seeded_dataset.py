"""
This is the test that matters most for the "defensible numbers" story:
it runs the same corruption logic the seed script uses, then asserts each
check reports exactly the count the corruption step claims to have caused.
If a check's count ever drifts from ground truth, this fails.
"""
from scripts.seed_dataset import generate_clean_customers, corrupt_dataset
from app.pipeline import run_all_checks


def test_check_counts_match_seeded_ground_truth():
    clean = generate_clean_customers(n=2000)
    corrupted, ground_truth = corrupt_dataset(clean)

    results = {r.check_name: r for r in run_all_checks(corrupted)}

    assert results["missing_values"].affected_rows == ground_truth["missing_values"]
    assert results["duplicates"].affected_rows == ground_truth["duplicates"]
    assert results["invalid_formats"].affected_rows == ground_truth["invalid_customer_id"]
    assert results["unexpected_values"].affected_rows == ground_truth["unexpected_plan_value"]
