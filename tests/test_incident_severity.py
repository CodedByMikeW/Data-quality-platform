from datetime import datetime, timedelta, timezone

from app.incident_severity import classify_severity

_START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _signal(minutes_offset: int, service: str, z_score: float) -> dict:
    return {
        "timestamp": _START + timedelta(minutes=minutes_offset),
        "service": service,
        "metric_name": "latency_ms",
        "value": 999.0,
        "z_score": z_score,
    }


def _incident(signals: list[dict]) -> dict:
    timestamps = [s["timestamp"] for s in signals]
    return {
        "signals": signals,
        "services": sorted({s["service"] for s in signals}),
        "signal_count": len(signals),
        "started_at": min(timestamps),
        "ended_at": max(timestamps),
    }


def test_single_isolated_signal_is_info():
    incident = _incident([_signal(0, "inventory-service", 4.1)])
    assert classify_severity(incident) == "info"


def test_multi_service_high_z_score_is_critical():
    incident = _incident(
        [
            _signal(0, "payments-service", 117.0),
            _signal(4, "checkout-api", 38.0),
        ]
    )
    assert classify_severity(incident) == "critical"


def test_multi_service_long_duration_is_critical_even_with_modest_z_score():
    incident = _incident(
        [
            _signal(0, "payments-service", 4.0),
            _signal(12, "checkout-api", 4.5),  # 12 minutes apart - past the critical duration bar
        ]
    )
    assert classify_severity(incident) == "critical"


def test_multi_service_low_z_score_short_duration_is_warning():
    incident = _incident(
        [
            _signal(0, "payments-service", 4.0),
            _signal(2, "checkout-api", 4.5),  # only 2 minutes - short and not severe
        ]
    )
    assert classify_severity(incident) == "warning"


def test_single_service_multiple_signals_is_warning():
    incident = _incident(
        [
            _signal(0, "payments-service", 4.0),
            _signal(1, "payments-service", 4.2),
        ]
    )
    assert classify_severity(incident) == "warning"


def test_severity_uses_absolute_z_score():
    incident = _incident(
        [
            _signal(0, "payments-service", -117.0),  # unusually low, but just as severe
            _signal(4, "checkout-api", 5.0),
        ]
    )
    assert classify_severity(incident) == "critical"
