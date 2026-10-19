"""
Flags metric points that deviate sharply from their own recent history.

Uses a trailing rolling baseline - mean/stdev computed only from the points
BEFORE each one, never including the point itself - so a spike can't get
absorbed into its own baseline and blunt its own z-score. Baselines are
computed independently per (service, metric_name): a checkout-api latency
spike is judged against checkout-api's own latency history, never against
payments-service or against a completely different metric.
"""
import pandas as pd

_ROLLING_WINDOW = 30  # trailing points used to compute the local baseline
_MIN_BASELINE_POINTS = 10  # don't score a point until there's enough history to trust the baseline
_Z_SCORE_THRESHOLD = 3.0  # standard "highly unusual" cutoff


def detect_metric_anomalies(points: list[dict]) -> list[dict]:
    """
    points: dicts with at least "id", "timestamp", "service", "metric_name",
    "value" - any order. Returns the subset that are anomalous, each with
    "z_score", "baseline_mean", "baseline_stdev" added, sorted by timestamp.
    """
    if not points:
        return []

    df = pd.DataFrame(points)
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    anomalies = []
    for _, group in df.groupby(["service", "metric_name"]):
        group = group.sort_values("timestamp")
        trailing = group["value"].shift(1).rolling(window=_ROLLING_WINDOW, min_periods=_MIN_BASELINE_POINTS)
        baseline_mean = trailing.mean()
        baseline_stdev = trailing.std()
        z_scores = (group["value"] - baseline_mean) / baseline_stdev

        for idx, z in z_scores.items():
            stdev = baseline_stdev[idx]
            if pd.isna(z) or pd.isna(stdev) or stdev == 0:
                continue
            if abs(z) < _Z_SCORE_THRESHOLD:
                continue
            row = group.loc[idx]
            anomalies.append(
                {
                    "id": int(row["id"]),
                    "timestamp": row["timestamp"].to_pydatetime(),
                    "service": row["service"],
                    "metric_name": row["metric_name"],
                    "value": float(row["value"]),
                    "z_score": round(float(z), 2),
                    "baseline_mean": round(float(baseline_mean[idx]), 2),
                    "baseline_stdev": round(float(stdev), 2),
                }
            )

    anomalies.sort(key=lambda a: a["timestamp"])
    return anomalies
