"""
Detects bursts of ERROR/CRITICAL log volume per service.

Reuses detect_metric_anomalies() rather than reinventing baseline/z-score
logic: a burst is just "unusually many errors counted in a time window,"
which is the same statistical question as "unusually high latency" - the
only new work here is turning discrete log lines into a regular time
series of per-window error counts, including the quiet windows (a bucket
with zero errors is a real zero, not a missing data point - the baseline
needs those zeros to know what "normal" looks like).
"""
import pandas as pd

from app.anomaly_detection import detect_metric_anomalies

_BUCKET_MINUTES = 1
_ERROR_LEVELS = {"ERROR", "CRITICAL"}
_METRIC_NAME = "error_log_rate"


def detect_log_error_bursts(log_rows: list[dict]) -> list[dict]:
    """
    log_rows: dicts with "timestamp", "service", "level". Returns burst
    windows in the same shape detect_metric_anomalies produces - id,
    timestamp, service, metric_name ("error_log_rate"), value (the error
    count in that window), z_score, baseline_mean, baseline_stdev.
    """
    if not log_rows:
        return []

    df = pd.DataFrame(log_rows)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["is_error"] = df["level"].isin(_ERROR_LEVELS)

    bucketed_rows = []
    next_id = 1
    for service, group in df.groupby("service"):
        group = group.set_index("timestamp").sort_index()
        counts = group["is_error"].resample(f"{_BUCKET_MINUTES}min").sum()
        for ts, count in counts.items():
            bucketed_rows.append(
                {
                    "id": next_id,
                    "timestamp": ts.to_pydatetime(),
                    "service": service,
                    "metric_name": _METRIC_NAME,
                    "value": float(count),
                }
            )
            next_id += 1

    return detect_metric_anomalies(bucketed_rows)
