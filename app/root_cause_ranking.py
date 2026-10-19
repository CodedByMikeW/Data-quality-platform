"""
Within an already-correlated incident, ranks the services involved by how
likely each is to be the root cause rather than a downstream effect.

Onset time is the primary signal: in a real cascade, the service that
failed first is the strongest candidate for "this is where it started" -
temporal precedence is the standard first heuristic real incident review
reaches for, long before anyone has a confirmed root cause. Peak severity
(largest absolute z-score) only breaks ties between services that appear
to have started at effectively the same moment.
"""


def rank_root_cause(incident: dict) -> list[dict]:
    """
    incident: a single correlate_incidents() output dict (has "signals").
    Returns services ranked most-likely-root-cause first: each entry has
    service, rank (1-indexed), first_seen, signal_count, and max_z_score
    (the largest absolute z-score among that service's signals in this
    incident - severity is symmetric, an unusually low value is just as
    anomalous as an unusually high one).
    """
    by_service: dict[str, dict] = {}
    for signal in incident["signals"]:
        service = signal["service"]
        entry = by_service.setdefault(
            service,
            {"service": service, "first_seen": signal["timestamp"], "signal_count": 0, "max_z_score": 0.0},
        )
        entry["signal_count"] += 1
        entry["first_seen"] = min(entry["first_seen"], signal["timestamp"])
        entry["max_z_score"] = max(entry["max_z_score"], abs(signal["z_score"]))

    ranked = sorted(by_service.values(), key=lambda e: (e["first_seen"], -e["max_z_score"]))
    for i, entry in enumerate(ranked, start=1):
        entry["rank"] = i
    return ranked
