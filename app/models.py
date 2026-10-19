from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, ForeignKey, JSON, Text
from sqlalchemy.orm import relationship

from app.database import Base


def utcnow():
    return datetime.now(timezone.utc)


class Dataset(Base):
    __tablename__ = "datasets"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow)

    runs = relationship("CheckRun", back_populates="dataset", order_by="CheckRun.created_at")


class CheckRun(Base):
    __tablename__ = "check_runs"

    id = Column(Integer, primary_key=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id"), nullable=False)
    score = Column(Float, nullable=False)
    breakdown = Column(JSON, nullable=False)
    row_count = Column(Integer, nullable=False)
    schema_snapshot = Column(JSON, nullable=True)
    distribution_snapshot = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    dataset = relationship("Dataset", back_populates="runs")
    issues = relationship("CheckIssue", back_populates="run", cascade="all, delete-orphan")


class CheckIssue(Base):
    __tablename__ = "check_issues"

    id = Column(Integer, primary_key=True)
    run_id = Column(Integer, ForeignKey("check_runs.id"), nullable=False, index=True)
    check_name = Column(String, nullable=False, index=True)
    row_key = Column(String, nullable=False)

    run = relationship("CheckRun", back_populates="issues")


class Document(Base):
    """Knowledge search engine: an uploaded file, parsed to plain text and stored."""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    content_type = Column(String, nullable=False)  # "pdf" | "docx" | "csv" | "text"
    text_content = Column(Text, nullable=False)
    char_count = Column(Integer, nullable=False)
    access_level = Column(String, nullable=False, default="public")  # "public" | "team" | "confidential"
    version = Column(Integer, nullable=False, default=1)
    is_latest = Column(Boolean, nullable=False, default=True)
    uploaded_at = Column(DateTime, default=utcnow)

    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    """
    One overlapping word-window of a document, plus its embedding vector -
    the unit semantic search actually matches against, since embedding an
    entire document as one vector loses too much granularity to be useful.
    """

    __tablename__ = "document_chunks"

    id = Column(Integer, primary_key=True)
    document_id = Column(Integer, ForeignKey("documents.id"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=False)
    char_end = Column(Integer, nullable=False)
    embedding = Column(JSON, nullable=False)  # list[float]

    document = relationship("Document", back_populates="chunks")


class SearchQueryLog(Base):
    """One search request against the knowledge base - the raw event that
    search analytics (top queries, zero-result queries, volume by mode)
    is aggregated from."""

    __tablename__ = "search_query_logs"

    id = Column(Integer, primary_key=True)
    query = Column(String, nullable=False)
    mode = Column(String, nullable=False)  # "keyword" | "semantic" | "hybrid"
    role = Column(String, nullable=False)
    result_count = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=utcnow, index=True)


class LogEntry(Base):
    """Incident intelligence: one ingested application log line."""

    __tablename__ = "log_entries"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    service = Column(String, nullable=False, index=True)
    level = Column(String, nullable=False)  # "INFO" | "WARN" | "ERROR" | "CRITICAL"
    message = Column(Text, nullable=False)


class MetricPoint(Base):
    """Incident intelligence: one ingested metric sample (e.g. latency_ms
    for payments-service at a point in time)."""

    __tablename__ = "metric_points"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    service = Column(String, nullable=False, index=True)
    metric_name = Column(String, nullable=False, index=True)
    value = Column(Float, nullable=False)


class Incident(Base):
    """
    A correlated incident, persisted so it has a stable identity to track
    resolution against - correlate_incidents() recomputes clusters fresh
    from raw signals on every request, which is fine for detection but
    can't carry a "resolved" flag on its own since nothing about it
    persists between requests. fingerprint (derived from started_at +
    the sorted service list) is what lets a later request recognize "this
    is the same incident I saw before" and update it instead of creating
    a duplicate.
    """

    __tablename__ = "incidents"

    id = Column(Integer, primary_key=True)
    fingerprint = Column(String, unique=True, nullable=False, index=True)
    started_at = Column(DateTime, nullable=False)
    ended_at = Column(DateTime, nullable=False)
    services = Column(JSON, nullable=False)  # list[str]
    signal_count = Column(Integer, nullable=False)
    severity = Column(String, nullable=False)
    root_cause_service = Column(String, nullable=True)
    status = Column(String, nullable=False, default="open")  # "open" | "acknowledged" | "resolved"
    resolution_note = Column(Text, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    first_detected_at = Column(DateTime, default=utcnow)
    last_seen_at = Column(DateTime, default=utcnow)
