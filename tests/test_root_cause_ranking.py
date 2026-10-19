from datetime import datetime, timedelta, timezone

from app.root_cause_ranking import rank_root_cause

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signal(minutes_offset: int, service: str, z_score: float = 10.0) -> dict:
    return {
        "timestamp": _START + timedelta(minutes=minutes_offset),
        "service": service,
        "metric_name": "latency_ms",
        "value": 999.0,
        "z_score": z_score,
    }


def test_single_service_incident_ranks_that_service_first():
    incident = {"signals": [_signal(0, "payments-service")]}
    ranked = rank_root_cause(incident)

    assert len(ranked) == 1
    assert ranked[0]["service"] == "payments-service"
    assert ranked[0]["rank"] == 1


def test_earliest_service_is_ranked_the_likely_root_cause():
    incident = {
        "signals": [
            _signal(4, "checkout-api"),
            _signal(0, "payments-service"),
        ]
    }
    ranked = rank_root_cause(incident)

    assert ranked[0]["service"] == "payments-service"
    assert ranked[0]["rank"] == 1
    assert ranked[1]["service"] == "checkout-api"
    assert ranked[1]["rank"] == 2


def test_ties_in_onset_time_break_by_highest_severity():
    incident = {
        "signals": [
            _signal(0, "inventory-service", z_score=4.0),
            _signal(0, "payments-service", z_score=15.0),
        ]
    }
    ranked = rank_root_cause(incident)

    assert ranked[0]["service"] == "payments-service"
    assert ranked[1]["service"] == "inventory-service"


def test_first_seen_uses_earliest_signal_per_service():
    incident = {
        "signals": [
            _signal(5, "payments-service"),
            _signal(0, "payments-service"),  # this one should count as first_seen
            _signal(2, "payments-service"),
        ]
    }
    ranked = rank_root_cause(incident)

    assert ranked[0]["first_seen"] == _START
    assert ranked[0]["signal_count"] == 3


def test_max_z_score_uses_absolute_value_and_the_largest_magnitude():
    incident = {
        "signals": [
            _signal(0, "payments-service", z_score=-20.0),  # unusually low, but still severe
            _signal(1, "payments-service", z_score=5.0),
        ]
    }
    ranked = rank_root_cause(incident)

    assert ranked[0]["max_z_score"] == 20.0


def test_three_service_cascade_ranks_in_onset_order():
    incident = {
        "signals": [
            _signal(8, "checkout-api"),
            _signal(0, "payments-service"),
            _signal(4, "inventory-service"),
        ]
    }
    ranked = rank_root_cause(incident)

    assert [r["service"] for r in ranked] == ["payments-service", "inventory-service", "checkout-api"]
    assert [r["rank"] for r in ranked] == [1, 2, 3]
