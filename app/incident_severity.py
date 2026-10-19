"""
Classifies a correlated incident's severity - critical, warning, or info -
from directly explainable factors: how anomalous its worst signal is (peak
z-score), how long it has run, and whether it has spread across more than
one service. No black box: every incident's severity traces back to these
three numbers, the same way every other score in this app does.
"""
_CRITICAL_Z_THRESHOLD = 20.0  # empirically separates the real seeded incident (z up to 117) from noise (z 3-10)
_CRITICAL_DURATION_MINUTES = 10.0  # a cross-service incident running this long is critical even without one huge spike


def classify_severity(incident: dict) -> str:
    """
    incident: a correlate_incidents() output dict, already carrying
    "signals", "services", "signal_count", "started_at", "ended_at".

    critical: spread across more than one service, AND either a peak
    z-score far beyond the 3-sigma detection threshold or a long runtime -
    the signature of a real cascading outage, not a coincidence.
    warning: more than one signal, but not (yet) confirmed to be spreading
    across services or severe enough to call critical.
    info: a single isolated signal - most likely statistical noise that
    happened to cross the 3-sigma threshold by chance.
    """
    peak_z = max(abs(s["z_score"]) for s in incident["signals"])
    is_multi_service = len(incident["services"]) > 1
    duration_minutes = (incident["ended_at"] - incident["started_at"]).total_seconds() / 60

    if is_multi_service and (peak_z >= _CRITICAL_Z_THRESHOLD or duration_minutes >= _CRITICAL_DURATION_MINUTES):
        return "critical"
    if incident["signal_count"] > 1:
        return "warning"
    return "info"
