from datetime import datetime, timedelta, timezone

from app.incident_correlation import correlate_incidents

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signal(minutes_offset: int, service: str, **overrides) -> dict:
    base = {
        "id": minutes_offset,
        "timestamp": _START + timedelta(minutes=minutes_offset),
        "service": service,
        "metric_name": "latency_ms",
        "value": 999.0,
        "z_score": 10.0,
        "baseline_mean": 100.0,
        "baseline_stdev": 10.0,
    }
    base.update(overrides)
    return base


def test_empty_input_returns_empty_list():
    assert correlate_incidents([], []) == []


def test_single_signal_becomes_its_own_incident():
    incidents = correlate_incidents([_signal(0, "checkout-api")], [])
    assert len(incidents) == 1
    assert incidents[0]["services"] == ["checkout-api"]
    assert incidents[0]["signal_count"] == 1


def test_cross_service_signals_close_in_time_merge_into_one_incident():
    # payments-service anomaly, then a checkout-api anomaly 4 minutes
    # later - exactly the cascade shape a real incident produces.
    anomalies = [_signal(0, "payments-service"), _signal(4, "checkout-api")]
    incidents = correlate_incidents(anomalies, [])

    assert len(incidents) == 1
    assert incidents[0]["services"] == ["checkout-api", "payments-service"]
    assert incidents[0]["signal_count"] == 2


def test_signals_far_apart_become_separate_incidents():
    anomalies = [_signal(0, "payments-service"), _signal(60, "payments-service")]
    incidents = correlate_incidents(anomalies, [])

    assert len(incidents) == 2
    assert incidents[0]["signal_count"] == 1
    assert incidents[1]["signal_count"] == 1


def test_chain_of_signals_each_within_gap_of_the_last_merges_even_over_a_long_span():
    # Each signal is only 8 minutes after the previous (within the 10-minute
    # gap), but the whole chain spans 40 minutes - well over the gap. It
    # must still merge into one incident, since only consecutive gaps are
    # checked, not distance from the cluster's start.
    anomalies = [_signal(i * 8, "payments-service") for i in range(6)]
    incidents = correlate_incidents(anomalies, [])

    assert len(incidents) == 1
    assert incidents[0]["signal_count"] == 6


def test_incident_spans_started_at_to_ended_at():
    anomalies = [_signal(0, "payments-service"), _signal(5, "checkout-api")]
    incidents = correlate_incidents(anomalies, [])

    assert incidents[0]["started_at"] == _START
    assert incidents[0]["ended_at"] == _START + timedelta(minutes=5)


def test_merges_metric_anomalies_and_log_bursts_together():
    anomalies = [_signal(0, "payments-service", metric_name="latency_ms")]
    log_bursts = [_signal(3, "payments-service", metric_name="error_log_rate")]
    incidents = correlate_incidents(anomalies, log_bursts)

    assert len(incidents) == 1
    signal_types = {s["signal_type"] for s in incidents[0]["signals"]}
    assert signal_types == {"metric_anomaly", "log_burst"}


def test_services_list_is_deduplicated_and_sorted():
    anomalies = [
        _signal(0, "payments-service"),
        _signal(1, "payments-service", metric_name="cpu_pct"),
        _signal(2, "checkout-api"),
    ]
    incidents = correlate_incidents(anomalies, [])
    assert incidents[0]["services"] == ["checkout-api", "payments-service"]
