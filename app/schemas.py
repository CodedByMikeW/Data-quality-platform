from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class CheckRunOut(BaseModel):
    id: int
    dataset_id: int
    score: float
    breakdown: dict
    row_count: int
    created_at: datetime
    relationship_feedback: list[dict] | None = None

    model_config = ConfigDict(from_attributes=True)


class IssuesOut(BaseModel):
    check_name: str
    count: int
    row_keys: list[str]


class SampleKindOut(BaseModel):
    kind: str
    title: str
    description: str


class SampleOut(SampleKindOut):
    """One generated sample: everything the page needs to run the check and to show the data."""

    dataset_name: str  # the label the check runs under, e.g. "sample-orders"
    filename: str
    seed: int
    row_count: int
    columns: list[str]
    preview_rows: list[list[str]]  # the first rows, as the CSV text has them (blank = missing)
    csv: str  # the whole file


class SavedDocumentOut(BaseModel):
    document_id: int
    filename: str
    version: int
    created: bool  # False when these exact rows were already saved at this level


class KnowledgeReportOut(BaseModel):
    document_id: int
    filename: str
    version: int
    access_level: str
    created: bool  # False when this exact report was already saved (a repeated save)
    data: SavedDocumentOut | None = None  # the uploaded rows, when they were sent along and saved
    data_error: str | None = None  # why they weren't saved, when they were sent but couldn't be


class DocumentOut(BaseModel):
    id: int
    filename: str
    content_type: str
    char_count: int
    access_level: str
    version: int
    is_latest: bool
    uploaded_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentDetailOut(DocumentOut):
    text_content: str


# Every document is listed and searchable, but only the ones the caller's code
# opens come back in full. The rest come back as a locked entry that says what
# it is and what unlocks it - its name, type, level and date, never anything
# from inside it (no text, excerpt, match score or size). The locked entry
# deliberately has no field for any of that, so it can't be filled in by mistake.
class DocumentListEntry(DocumentOut):
    locked: Literal[False] = False


class LockedDocumentOut(BaseModel):
    id: int
    filename: str
    content_type: str
    access_level: str
    version: int
    uploaded_at: datetime
    locked: Literal[True] = True

    model_config = ConfigDict(from_attributes=True)


ListedDocument = Annotated[DocumentListEntry | LockedDocumentOut, Field(discriminator="locked")]


class DocumentTableOut(BaseModel):
    columns: list[str]
    rows: list[list[str]]
    total_rows: int  # every data row in the file, not just this page
    offset: int
    limit: int


class UnlockRequest(BaseModel):
    code: str
    id: str | None = None  # the ID number; only the confidential code asks for one


class UnlockOut(BaseModel):
    role: str  # the role this code (and ID) lets you view as: manager | admin
    level: str  # the highest access level it opens: team | confidential
    # True when the code was the confidential one but no ID came with it: it opens
    # team and public on its own, and the ID is what adds confidential.
    needs_id: bool = False


class AccessHintOut(BaseModel):
    enabled: bool
    team_code: str | None = None
    confidential_code: str | None = None
    confidential_id: str | None = None  # the ID number that goes with the confidential code - shown for confidential only


class DocumentSearchResult(BaseModel):
    id: int
    filename: str
    content_type: str
    access_level: str
    snippet: str
    chunk_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    citation: str
    locked: Literal[False] = False


class SemanticSearchResult(BaseModel):
    id: int
    filename: str
    content_type: str
    access_level: str
    snippet: str
    similarity: float
    chunk_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    citation: str
    locked: Literal[False] = False


class HybridSearchResult(BaseModel):
    id: int
    filename: str
    content_type: str
    access_level: str
    snippet: str
    sources: list[str]
    similarity: float | None = None
    score: float
    chunk_index: int | None = None
    char_start: int | None = None
    char_end: int | None = None
    citation: str
    locked: Literal[False] = False


class LockedSearchResult(BaseModel):
    """A search hit on a document the caller's code doesn't open: that it matched, and what unlocks it. Nothing from inside it."""

    id: int
    filename: str
    content_type: str
    access_level: str
    locked: Literal[True] = True


KeywordHit = Annotated[DocumentSearchResult | LockedSearchResult, Field(discriminator="locked")]
SemanticHit = Annotated[SemanticSearchResult | LockedSearchResult, Field(discriminator="locked")]
HybridHit = Annotated[HybridSearchResult | LockedSearchResult, Field(discriminator="locked")]


class QueryCount(BaseModel):
    query: str
    count: int


class SearchAnalyticsSummary(BaseModel):
    total_searches: int
    searches_by_mode: dict[str, int]
    top_queries: list[QueryCount]
    zero_result_queries: list[QueryCount]


class LogEntryOut(BaseModel):
    id: int
    timestamp: datetime
    service: str
    level: str
    message: str

    model_config = ConfigDict(from_attributes=True)


class MetricPointOut(BaseModel):
    id: int
    timestamp: datetime
    service: str
    metric_name: str
    value: float

    model_config = ConfigDict(from_attributes=True)


class IngestSummary(BaseModel):
    rows_ingested: int
    services: list[str]


class MetricAnomalyOut(BaseModel):
    id: int
    timestamp: datetime
    service: str
    metric_name: str
    value: float
    z_score: float
    baseline_mean: float
    baseline_stdev: float


class IncidentSignal(MetricAnomalyOut):
    signal_type: str  # "metric_anomaly" | "log_burst"


class RootCauseCandidate(BaseModel):
    service: str
    rank: int
    first_seen: datetime
    signal_count: int
    max_z_score: float


class CorrelatedIncident(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime
    services: list[str]
    signal_count: int
    signals: list[IncidentSignal]
    root_cause: list[RootCauseCandidate]
    severity: str
    status: str
    resolution_note: str | None = None


class IncidentRecordOut(BaseModel):
    id: int
    started_at: datetime
    ended_at: datetime
    services: list[str]
    signal_count: int
    severity: str
    root_cause_service: str | None
    status: str
    resolution_note: str | None
    resolved_at: datetime | None
    first_detected_at: datetime
    last_seen_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ResolutionRequest(BaseModel):
    note: str | None = None


class IncidentHistorySummary(BaseModel):
    total_incidents: int
    counts_by_status: dict[str, int]
    mean_time_to_resolution_minutes: float | None


class DeleteSummary(BaseModel):
    rows_deleted: int
