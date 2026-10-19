import hashlib

import pandas as pd
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app import models, schemas
from app.limits import read_csv_limited, read_upload, upload_slot
from app.models import utcnow
from app.anomaly_detection import detect_metric_anomalies
from app.log_burst_detection import detect_log_error_bursts
from app.incident_correlation import correlate_incidents
from app.root_cause_ranking import rank_root_cause
from app.incident_severity import classify_severity

_INCIDENT_STATUSES = {"open", "acknowledged", "resolved"}

router = APIRouter(prefix="/incidents", tags=["incidents"])

_VALID_LEVELS = {"INFO", "WARN", "ERROR", "CRITICAL"}
_LOG_COLUMNS = {"timestamp", "service", "level", "message"}
_METRIC_COLUMNS = {"timestamp", "service", "metric_name", "value"}
_LIST_LIMIT_DEFAULT = 500
_LIST_LIMIT_MAX = 2000


def _read_csv(file: UploadFile) -> pd.DataFrame:
    contents = read_upload(file)
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    return read_csv_limited(
        contents,
        file.filename,
        max_rows=settings.max_incident_rows,
        max_columns=settings.max_check_columns,
        too_many_rows_hint="Try a shorter time window - the stretch around the incident is what matters.",
    )


def _require_columns(df: pd.DataFrame, required: set[str]) -> None:
    missing = required - set(df.columns)
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required column(s): {sorted(missing)}")


def _parsed_timestamps(df: pd.DataFrame) -> pd.Series:
    timestamps = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    if timestamps.isna().any():
        raise HTTPException(status_code=400, detail="Some rows have an unparseable timestamp")
    return timestamps


@router.post("/logs/upload", response_model=schemas.IngestSummary, dependencies=[Depends(upload_slot)])
def upload_logs(file: UploadFile = File(...), db: Session = Depends(get_db)):
    df = _read_csv(file)
    _require_columns(df, _LOG_COLUMNS)
    timestamps = _parsed_timestamps(df)

    levels = df["level"].astype(str).str.strip().str.upper()
    unknown_levels = set(levels.unique()) - _VALID_LEVELS
    if unknown_levels:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown log level(s) {sorted(unknown_levels)}. Expected one of {sorted(_VALID_LEVELS)}.",
        )

    rows = [
        models.LogEntry(timestamp=ts.to_pydatetime(), service=str(service), level=level, message=str(message))
        for ts, service, level, message in zip(timestamps, df["service"], levels, df["message"])
    ]
    db.bulk_save_objects(rows)
    db.commit()

    return schemas.IngestSummary(rows_ingested=len(rows), services=sorted({r.service for r in rows}))


@router.post("/metrics/upload", response_model=schemas.IngestSummary, dependencies=[Depends(upload_slot)])
def upload_metrics(file: UploadFile = File(...), db: Session = Depends(get_db)):
    df = _read_csv(file)
    _require_columns(df, _METRIC_COLUMNS)
    timestamps = _parsed_timestamps(df)

    values = pd.to_numeric(df["value"], errors="coerce")
    if values.isna().any():
        raise HTTPException(status_code=400, detail="Some rows have a non-numeric value")

    rows = [
        models.MetricPoint(timestamp=ts.to_pydatetime(), service=str(service), metric_name=str(metric_name), value=float(value))
        for ts, service, metric_name, value in zip(timestamps, df["service"], df["metric_name"], values)
    ]
    db.bulk_save_objects(rows)
    db.commit()

    return schemas.IngestSummary(rows_ingested=len(rows), services=sorted({r.service for r in rows}))


@router.get("/services", response_model=list[str])
def list_services(db: Session = Depends(get_db)):
    log_services = {row[0] for row in db.query(models.LogEntry.service).distinct()}
    metric_services = {row[0] for row in db.query(models.MetricPoint.service).distinct()}
    return sorted(log_services | metric_services)


@router.get("/logs", response_model=list[schemas.LogEntryOut])
def list_logs(
    service: str | None = None,
    level: str | None = None,
    limit: int = _LIST_LIMIT_DEFAULT,
    db: Session = Depends(get_db),
):
    q = db.query(models.LogEntry)
    if service:
        q = q.filter(models.LogEntry.service == service)
    if level:
        q = q.filter(models.LogEntry.level == level.upper())
    return q.order_by(models.LogEntry.timestamp.desc()).limit(min(limit, _LIST_LIMIT_MAX)).all()


@router.delete("/logs", response_model=schemas.DeleteSummary)
def delete_logs(db: Session = Depends(get_db)):
    """
    Permanently deletes every ingested log entry - no filter, no undo.
    Existing incident records aren't touched: they were already persisted
    as their own rows, so they stay as history even after the raw logs
    that helped detect them are gone.
    """
    count = db.query(models.LogEntry).delete()
    db.commit()
    return schemas.DeleteSummary(rows_deleted=count)


@router.get("/metrics", response_model=list[schemas.MetricPointOut])
def list_metrics(
    service: str | None = None,
    metric_name: str | None = None,
    limit: int = _LIST_LIMIT_DEFAULT,
    db: Session = Depends(get_db),
):
    q = db.query(models.MetricPoint)
    if service:
        q = q.filter(models.MetricPoint.service == service)
    if metric_name:
        q = q.filter(models.MetricPoint.metric_name == metric_name)
    return q.order_by(models.MetricPoint.timestamp.desc()).limit(min(limit, _LIST_LIMIT_MAX)).all()


@router.get("/anomalies", response_model=list[schemas.MetricAnomalyOut])
def get_metric_anomalies(service: str | None = None, db: Session = Depends(get_db)):
    """
    Every metric point whose value is a statistical outlier against its own
    service+metric's recent trailing history. Filtering by service first is
    safe - the rolling baseline is already computed independently per
    (service, metric_name) group, so narrowing to one service can't change
    another service's baseline or vice versa.
    """
    q = db.query(models.MetricPoint)
    if service:
        q = q.filter(models.MetricPoint.service == service)

    points = [
        {"id": p.id, "timestamp": p.timestamp, "service": p.service, "metric_name": p.metric_name, "value": p.value}
        for p in q.all()
    ]
    return detect_metric_anomalies(points)


@router.get("/log-bursts", response_model=list[schemas.MetricAnomalyOut])
def get_log_error_bursts(service: str | None = None, db: Session = Depends(get_db)):
    """
    Bursts of ERROR/CRITICAL log volume per service, detected the same way
    as a metric anomaly: bucket into 1-minute windows, then flag windows
    whose error count is a statistical outlier against that service's own
    recent trailing baseline.
    """
    q = db.query(models.LogEntry)
    if service:
        q = q.filter(models.LogEntry.service == service)

    log_rows = [{"timestamp": entry.timestamp, "service": entry.service, "level": entry.level} for entry in q.all()]
    return detect_log_error_bursts(log_rows)


def _fingerprint(incident: dict) -> str:
    """
    Identifies "the same incident" across separate correlation runs.
    started_at shouldn't move once a cluster first forms (new signals can
    only extend ended_at forward in time), and combined with the sorted
    service list it's specific enough to avoid colliding with a genuinely
    different incident that happens to start at the same instant.
    """
    key = f"{incident['started_at'].isoformat()}|{','.join(sorted(incident['services']))}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _upsert_incident_record(db: Session, incident: dict) -> models.Incident:
    fingerprint = _fingerprint(incident)
    record = db.query(models.Incident).filter_by(fingerprint=fingerprint).first()
    root_cause_service = incident["root_cause"][0]["service"] if incident["root_cause"] else None

    if record:
        record.ended_at = incident["ended_at"]
        record.signal_count = incident["signal_count"]
        record.severity = incident["severity"]
        record.root_cause_service = root_cause_service
        record.last_seen_at = utcnow()
    else:
        record = models.Incident(
            fingerprint=fingerprint,
            started_at=incident["started_at"],
            ended_at=incident["ended_at"],
            services=incident["services"],
            signal_count=incident["signal_count"],
            severity=incident["severity"],
            root_cause_service=root_cause_service,
            status="open",
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


def _get_incident_or_404(db: Session, incident_id: int) -> models.Incident:
    record = db.query(models.Incident).filter_by(id=incident_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Incident not found")
    return record


@router.get("/correlated", response_model=list[schemas.CorrelatedIncident])
def get_correlated_incidents(db: Session = Depends(get_db)):
    """
    Merges every currently-flagged metric anomaly and log error burst,
    across all services, into incidents - clusters of signals close
    together in time. Deliberately not filterable by service: correlation
    only finds anything interesting by looking across services, so
    narrowing to one would defeat the point.

    Each computed incident is persisted (or updated, if already known) so
    it has a stable id and status to track resolution against - without
    that, "mark this incident resolved" has nothing durable to attach to,
    since correlation recomputes clusters fresh from raw signals on every
    request.
    """
    metric_points = [
        {"id": p.id, "timestamp": p.timestamp, "service": p.service, "metric_name": p.metric_name, "value": p.value}
        for p in db.query(models.MetricPoint).all()
    ]
    anomalies = detect_metric_anomalies(metric_points)

    log_rows = [
        {"timestamp": entry.timestamp, "service": entry.service, "level": entry.level}
        for entry in db.query(models.LogEntry).all()
    ]
    log_bursts = detect_log_error_bursts(log_rows)

    incidents = correlate_incidents(anomalies, log_bursts)
    for incident in incidents:
        incident["root_cause"] = rank_root_cause(incident)
        incident["severity"] = classify_severity(incident)
        record = _upsert_incident_record(db, incident)
        incident["id"] = record.id
        incident["status"] = record.status
        incident["resolution_note"] = record.resolution_note
    return incidents


@router.post("/correlated/{incident_id}/acknowledge", response_model=schemas.IncidentRecordOut)
def acknowledge_incident(incident_id: int, db: Session = Depends(get_db)):
    record = _get_incident_or_404(db, incident_id)
    if record.status == "open":
        record.status = "acknowledged"
        db.commit()
        db.refresh(record)
    return record


@router.post("/correlated/{incident_id}/resolve", response_model=schemas.IncidentRecordOut)
def resolve_incident(incident_id: int, body: schemas.ResolutionRequest, db: Session = Depends(get_db)):
    record = _get_incident_or_404(db, incident_id)
    record.status = "resolved"
    record.resolution_note = body.note
    record.resolved_at = utcnow()
    db.commit()
    db.refresh(record)
    return record


@router.post("/correlated/{incident_id}/reopen", response_model=schemas.IncidentRecordOut)
def reopen_incident(incident_id: int, db: Session = Depends(get_db)):
    record = _get_incident_or_404(db, incident_id)
    record.status = "open"
    record.resolution_note = None
    record.resolved_at = None
    db.commit()
    db.refresh(record)
    return record


@router.get("/history/summary", response_model=schemas.IncidentHistorySummary)
def get_incident_history_summary(db: Session = Depends(get_db)):
    """
    MTTR (mean time to resolution) is computed only over incidents that
    have actually been resolved - an open or acknowledged incident has no
    resolution time yet, and counting it as zero or excluding it silently
    would both understate how long real resolutions take.
    """
    records = db.query(models.Incident).all()
    counts_by_status = {status: 0 for status in _INCIDENT_STATUSES}
    for record in records:
        counts_by_status[record.status] = counts_by_status.get(record.status, 0) + 1

    resolution_times = [
        (record.resolved_at - record.first_detected_at).total_seconds() / 60
        for record in records
        if record.status == "resolved" and record.resolved_at is not None
    ]
    mttr = sum(resolution_times) / len(resolution_times) if resolution_times else None

    return schemas.IncidentHistorySummary(
        total_incidents=len(records),
        counts_by_status=counts_by_status,
        mean_time_to_resolution_minutes=round(mttr, 1) if mttr is not None else None,
    )
