from datetime import datetime, timedelta, timezone

from app.log_burst_detection import detect_log_error_bursts

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _quiet_minutes(count: int, service: str = "payments-service", start_minute: int = 0) -> list[dict]:
    return [
        {"timestamp": _START + timedelta(minutes=start_minute + m, seconds=10), "service": service, "level": "INFO"}
        for m in range(count)
    ]


def _error_minute(minute: int, count: int, service: str = "payments-service") -> list[dict]:
    return [
        {"timestamp": _START + timedelta(minutes=minute, seconds=s), "service": service, "level": "ERROR"}
        for s in range(0, min(count * 5, 60), 5)
    ]


def test_empty_input_returns_empty_list():
    assert detect_log_error_bursts([]) == []


def test_flags_a_burst_after_a_quiet_baseline():
    # 15 quiet minutes, then a ramping burst (values keep rising, so the
    # trailing baseline can't "catch up" and absorb it) - a real burst, not
    # noise, should show up somewhere in that window.
    rows = _quiet_minutes(15)
    for i, minute in enumerate(range(15, 21)):
        rows += _error_minute(minute, count=[5, 8, 12, 15, 18, 20][i])
    rows += _quiet_minutes(2, start_minute=21)

    bursts = detect_log_error_bursts(rows)

    assert len(bursts) >= 1
    for b in bursts:
        minute_offset = round((b["timestamp"] - _START).total_seconds() / 60)
        assert 15 <= minute_offset < 21  # only within the burst window, never the quiet minutes
        assert b["metric_name"] == "error_log_rate"
        assert b["service"] == "payments-service"


def test_does_not_flag_a_normal_quiet_period():
    # No errors at all across 20 minutes - nothing should ever be flagged
    # just because a bucket has a nonzero baseline mean of exactly 0.
    assert detect_log_error_bursts(_quiet_minutes(20)) == []


def test_warn_level_logs_are_not_counted_as_errors():
    rows = [
        {"timestamp": _START + timedelta(minutes=m, seconds=10), "service": "payments-service", "level": "WARN"}
        for m in range(15)
    ]
    # A burst of WARN logs alone should never register as an error burst.
    rows += [
        {"timestamp": _START + timedelta(minutes=15, seconds=s), "service": "payments-service", "level": "WARN"}
        for s in range(0, 60, 5)
    ]
    assert detect_log_error_bursts(rows) == []


def test_critical_level_counts_as_an_error():
    rows = _quiet_minutes(15)
    for i, minute in enumerate(range(15, 21)):
        rows += [
            {"timestamp": _START + timedelta(minutes=minute, seconds=s), "service": "payments-service", "level": "CRITICAL"}
            for s in range(0, min([5, 8, 12, 15, 18, 20][i] * 5, 60), 5)
        ]
    rows += _quiet_minutes(2, start_minute=21)

    assert len(detect_log_error_bursts(rows)) >= 1


def test_bursts_are_independent_per_service():
    quiet_a = _quiet_minutes(15, service="checkout-api")
    burst_b = _quiet_minutes(15, service="payments-service")
    for i, minute in enumerate(range(15, 21)):
        burst_b += _error_minute(minute, count=[5, 8, 12, 15, 18, 20][i], service="payments-service")
    burst_b += _quiet_minutes(2, service="payments-service", start_minute=21)

    bursts = detect_log_error_bursts(quiet_a + burst_b)

    assert len(bursts) >= 1
    assert all(b["service"] == "payments-service" for b in bursts)
