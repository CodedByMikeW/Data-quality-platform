"""
Generates synthetic log entries and metric points for the Incident
Intelligence Platform, with one deliberate incident baked in: a payment
gateway outage that spikes payments-service latency and errors, which
cascades into checkout-api a few minutes later. That's what gives later
correlation and root-cause steps a real causality chain to detect instead
of just noise.

Run from the project root:
    python -m scripts.seed_incidents
"""
import random
from datetime import datetime, timedelta, timezone

random.seed(42)

_SERVICES = ["checkout-api", "payments-service", "inventory-service"]

# (mean, stdev) baseline for each service's metrics under normal conditions.
_BASELINE_METRICS = {
    "checkout-api": {"latency_ms": (120, 15), "error_rate": (0.8, 0.3), "cpu_pct": (35, 5)},
    "payments-service": {"latency_ms": (90, 10), "error_rate": (0.5, 0.2), "cpu_pct": (28, 4)},
    "inventory-service": {"latency_ms": (60, 8), "error_rate": (0.3, 0.15), "cpu_pct": (25, 4)},
}

_WINDOW_MINUTES = 180  # 3 hours of 1-minute samples
_INCIDENT_START_MINUTE = 120  # payments-service starts failing 2h into the window
_INCIDENT_DURATION_MINUTES = 15
_CASCADE_DELAY_MINUTES = 4  # checkout-api degrades ~4 min after payments does

_BASELINE_MESSAGES = {
    "checkout-api": ["Order placed successfully", "Cart validated", "Checkout session started"],
    "payments-service": ["Payment authorized", "Payment captured", "Refund processed"],
    "inventory-service": ["Stock reserved", "Inventory synced", "Stock level updated"],
}
_INCIDENT_MESSAGES_PAYMENTS = [
    "Connection timeout to payment gateway",
    "Circuit breaker opened for PaymentGatewayClient",
    "Retrying failed payment transaction (attempt 3/3)",
    "Payment gateway returned 503 Service Unavailable",
]
_CASCADE_MESSAGES_CHECKOUT = [
    "Checkout failed: downstream payment service unavailable",
    "Request to payments-service timed out after 5000ms",
    "Order could not be completed - payment error",
]


def _in_window(minute: int, start: int, duration: int) -> bool:
    return start <= minute < start + duration


def generate_incident_demo_data(start_time: datetime | None = None) -> tuple[list[dict], list[dict]]:
    """Returns (log_rows, metric_rows) - plain dicts ready to become
    LogEntry/MetricPoint rows, sorted by timestamp."""
    start_time = start_time or (datetime.now(timezone.utc) - timedelta(minutes=_WINDOW_MINUTES))

    metric_rows = []
    log_rows = []

    for minute in range(_WINDOW_MINUTES):
        ts = start_time + timedelta(minutes=minute)
        in_incident = _in_window(minute, _INCIDENT_START_MINUTE, _INCIDENT_DURATION_MINUTES)
        in_cascade = _in_window(minute, _INCIDENT_START_MINUTE + _CASCADE_DELAY_MINUTES, _INCIDENT_DURATION_MINUTES)

        for service, metrics in _BASELINE_METRICS.items():
            for metric_name, (mean, stdev) in metrics.items():
                value = random.gauss(mean, stdev)
                if service == "payments-service" and in_incident:
                    value = {
                        "latency_ms": random.gauss(950, 120),
                        "error_rate": random.gauss(20, 4),
                        "cpu_pct": random.gauss(88, 5),
                    }[metric_name]
                elif service == "checkout-api" and in_cascade and metric_name in ("latency_ms", "error_rate"):
                    value = {"latency_ms": random.gauss(600, 90), "error_rate": random.gauss(9, 2)}[metric_name]
                metric_rows.append(
                    {
                        "timestamp": ts,
                        "service": service,
                        "metric_name": metric_name,
                        "value": round(max(0.0, value), 2),
                    }
                )

        for service in _SERVICES:
            for _ in range(random.randint(1, 3)):
                log_rows.append(
                    {
                        "timestamp": ts + timedelta(seconds=random.randint(0, 59)),
                        "service": service,
                        "level": "INFO",
                        "message": random.choice(_BASELINE_MESSAGES[service]),
                    }
                )
            if random.random() < 0.03:
                log_rows.append(
                    {
                        "timestamp": ts + timedelta(seconds=random.randint(0, 59)),
                        "service": service,
                        "level": "WARN",
                        "message": "Response time above target threshold",
                    }
                )

        if in_incident:
            for _ in range(random.randint(4, 7)):
                log_rows.append(
                    {
                        "timestamp": ts + timedelta(seconds=random.randint(0, 59)),
                        "service": "payments-service",
                        "level": "ERROR",
                        "message": random.choice(_INCIDENT_MESSAGES_PAYMENTS),
                    }
                )
        if in_cascade:
            for _ in range(random.randint(2, 4)):
                log_rows.append(
                    {
                        "timestamp": ts + timedelta(seconds=random.randint(0, 59)),
                        "service": "checkout-api",
                        "level": "ERROR",
                        "message": random.choice(_CASCADE_MESSAGES_CHECKOUT),
                    }
                )

    log_rows.sort(key=lambda r: r["timestamp"])
    metric_rows.sort(key=lambda r: r["timestamp"])
    return log_rows, metric_rows


if __name__ == "__main__":
    logs, metrics = generate_incident_demo_data()
    error_logs = [row for row in logs if row["level"] == "ERROR"]
    print(f"Generated {len(logs)} log entries ({len(error_logs)} ERROR-level) and {len(metrics)} metric points.")
