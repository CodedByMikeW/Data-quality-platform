from app.checks.base import CheckResult

# Weights are out of 100 across all 8 planned checks. Only checks that have
# actually been implemented and run contribute to a given score - the score
# is normalized by the weight of checks that ran, not the weight of all 8,
# so the number stays meaningful before every check exists yet.
DEFAULT_WEIGHTS = {
    "missing_values": 15,
    "duplicates": 10,
    "invalid_formats": 15,
    "schema_changes": 10,
    "outliers": 10,
    "drift": 15,
    "broken_relationships": 15,
    "unexpected_values": 10,
}


def compute_score(results: list[CheckResult], weights: dict[str, float] | None = None) -> dict:
    weights = weights or DEFAULT_WEIGHTS

    total_weight = sum(weights.get(r.check_name, 0) for r in results) or 1
    weighted_sum = sum(weights.get(r.check_name, 0) * r.pass_rate for r in results)
    score = round((weighted_sum / total_weight) * 100, 1)

    breakdown = {
        r.check_name: {
            "weight": weights.get(r.check_name, 0),
            "pass_rate": round(r.pass_rate, 4),
            "affected_rows": r.affected_rows,
            "total_rows": r.total_rows,
        }
        for r in results
    }

    return {"score": score, "breakdown": breakdown}
