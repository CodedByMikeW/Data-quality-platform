from datetime import datetime, timedelta, timezone

from app.anomaly_detection import detect_metric_anomalies

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _points(values: list[float], service: str = "checkout-api", metric_name: str = "latency_ms") -> list[dict]:
    return [
        {
            "id": i + 1,
            "timestamp": _START + timedelta(minutes=i),
            "service": service,
            "metric_name": metric_name,
            "value": value,
        }
        for i, value in enumerate(values)
    ]


def test_empty_input_returns_empty_list():
    assert detect_metric_anomalies([]) == []


def test_flags_a_clear_spike_after_a_stable_baseline():
    baseline = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 101.0]
    points = _points(baseline + [900.0])

    anomalies = detect_metric_anomalies(points)

    assert len(anomalies) == 1
    assert anomalies[0]["value"] == 900.0
    assert anomalies[0]["z_score"] > 3.0


def test_does_not_flag_points_within_normal_range():
    stable = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 101.0, 100.0, 99.0, 100.0]
    assert detect_metric_anomalies(_points(stable)) == []


def test_does_not_flag_before_enough_baseline_history():
    # Only 5 prior points before the "spike" - fewer than the minimum
    # baseline size, so this must not be scored at all (not enough history
    # to trust a baseline yet), rather than flagged or crash.
    points = _points([100.0, 101.0, 99.0, 100.0, 102.0, 900.0])
    assert detect_metric_anomalies(points) == []


def test_baseline_for_a_point_never_includes_its_own_value():
    # If a point's own value leaked into its own baseline (missing the
    # shift(1)), a single huge spike would inflate its own mean/stdev and
    # could dilute its own z-score. baseline_mean here must reflect only
    # the ~100 prior points, nowhere near the 900 spike.
    baseline = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 101.0]
    points = _points(baseline + [900.0])

    anomalies = detect_metric_anomalies(points)

    assert len(anomalies) == 1
    assert anomalies[0]["baseline_mean"] < 105


def test_baselines_are_independent_per_service_and_metric():
    stable_a = _points(
        [100.0, 101.0, 99.0, 100.0, 101.0, 99.0, 100.0, 101.0, 99.0, 100.0, 101.0, 100.0],
        service="checkout-api",
        metric_name="latency_ms",
    )
    spiking_b = _points(
        [10.0, 11.0, 9.0, 10.0, 11.0, 9.0, 10.0, 11.0, 9.0, 10.0, 11.0, 500.0],
        service="payments-service",
        metric_name="error_rate",
    )

    anomalies = detect_metric_anomalies(stable_a + spiking_b)

    assert len(anomalies) == 1
    assert anomalies[0]["service"] == "payments-service"
    assert anomalies[0]["metric_name"] == "error_rate"


def test_zero_variance_baseline_does_not_crash_or_flag():
    # A perfectly flat baseline has stdev 0 - dividing by it would produce
    # inf/NaN z-scores. Even a real change afterward must not be flagged
    # from an undefined baseline, and this must not raise.
    points = _points([100.0] * 12 + [150.0])
    assert detect_metric_anomalies(points) == []
