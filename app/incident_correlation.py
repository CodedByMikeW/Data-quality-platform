"""
Groups already-flagged metric anomalies and log error bursts into
incidents: clusters of signals within a short time gap of each other,
across services - the whole point of correlation is that one service's
anomaly and another service's anomaly landing close together in time is
itself the evidence they're related, not independent noise. Detection
(anomaly_detection.py, log_burst_detection.py) decides WHAT is anomalous;
this only decides which already-anomalous things belong together.
"""
_MAX_GAP_MINUTES = 10  # signals more than this far apart belong to different incidents


def correlate_incidents(anomalies: list[dict], log_bursts: list[dict]) -> list[dict]:
    """
    anomalies/log_bursts: dicts shaped like detect_metric_anomalies()'s
    output (id, timestamp, service, metric_name, value, z_score,
    baseline_mean, baseline_stdev). Returns incidents, each a cluster of
    signals ordered by timestamp with the maximum gap between any two
    consecutive signals no more than _MAX_GAP_MINUTES - which lets a long,
    slowly-evolving incident still merge into one, since only consecutive
    gaps are checked, not total span from the cluster's start.
    """
    signals = [{**a, "signal_type": "metric_anomaly"} for a in anomalies]
    signals += [{**b, "signal_type": "log_burst"} for b in log_bursts]

    if not signals:
        return []

    signals.sort(key=lambda s: s["timestamp"])

    clusters: list[list[dict]] = [[signals[0]]]
    for signal in signals[1:]:
        gap_minutes = (signal["timestamp"] - clusters[-1][-1]["timestamp"]).total_seconds() / 60
        if gap_minutes <= _MAX_GAP_MINUTES:
            clusters[-1].append(signal)
        else:
            clusters.append([signal])

    return [
        {
            "id": i,
            "started_at": cluster[0]["timestamp"],
            "ended_at": cluster[-1]["timestamp"],
            "services": sorted({s["service"] for s in cluster}),
            "signal_count": len(cluster),
            "signals": cluster,
        }
        for i, cluster in enumerate(clusters, start=1)
    ]
