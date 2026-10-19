import csv
import re
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, UploadFile, File, Form, Depends, Header, HTTPException, Query
from sqlalchemy import or_, func
from sqlalchemy.orm import Session, defer

from app.access import authorize_role, unlock
from app.config import settings
from app.database import get_db
from app import models, schemas
from app.document_extraction import extract_text, FileTooLarge, TextTooLong, UnsupportedFileType
from app.document_store import ACCESS_LEVELS, store_document, validated_access_level
from app.embeddings import embed_texts, cosine_similarity
from app.limits import document_too_long, read_upload, too_large, upload_slot

router = APIRouter(prefix="/documents", tags=["documents"])

_SNIPPET_RADIUS = 80  # characters shown on each side of the first keyword match
_SEMANTIC_SNIPPET_LENGTH = 240
_RESULT_LIMIT = 20
_RRF_K = 60  # standard constant from the reciprocal rank fusion literature

# A role's access is its max level and everything before it in ACCESS_LEVELS
# (least -> most restricted), rather than an explicit list per role.
_ROLE_MAX_LEVEL = {"employee": "public", "manager": "team", "admin": "confidential"}
_DEFAULT_ROLE = "employee"  # least-privilege default when no role is specified
_ANALYTICS_ROLES = {"manager", "admin"}  # search analytics is itself sensitive - who's searching for what
_TOP_QUERY_LIMIT = 10
_TABLE_PAGE_SIZE = 200  # rows the CSV viewer asks for at a time
_TABLE_MAX_PAGE = 500

# The access code - and, for confidential, the ID number - travel in headers,
# not the URL, so they never end up in server logs, the browser history or a
# copied link. Asking to view as a role above public only works with the
# matching code (and ID) - see app/access.py.
AccessCode = Annotated[str | None, Header(alias="X-Access-Code")]
AccessId = Annotated[str | None, Header(alias="X-Access-Id")]


def _allowed_levels(role: str, access_code: str | None = None, access_id: str | None = None) -> list[str]:
    max_level = _ROLE_MAX_LEVEL.get(role)
    if max_level is None:
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'. Expected one of {list(_ROLE_MAX_LEVEL)}.")
    authorize_role(role, access_code, access_id)
    return ACCESS_LEVELS[: ACCESS_LEVELS.index(max_level) + 1]


def _visible_document(
    db: Session,
    document_id: int,
    role: str,
    access_code: str | None,
    access_id: str | None = None,
    with_text: bool = True,
) -> tuple[models.Document, list[str]]:
    """
    The document and the levels the caller may see, or 400/403/404 - never a
    document above their clearance. `with_text=False` leaves its text in the
    database: a saved data file can be 25 MB, and most requests only need to
    know what the document is.
    """
    allowed_levels = _allowed_levels(role, access_code, access_id)
    query = db.query(models.Document)
    if not with_text:
        query = query.options(defer(models.Document.text_content))
    doc = query.filter_by(id=document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.access_level not in allowed_levels:
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot view a '{doc.access_level}' document")
    return doc, allowed_levels


def _locked_hit(doc: models.Document) -> schemas.LockedSearchResult:
    """What a search says about a document the caller's code doesn't open yet: that it matched, and what unlocks it."""
    return schemas.LockedSearchResult(
        id=doc.id, filename=doc.filename, content_type=doc.content_type, access_level=doc.access_level
    )


_LINE = re.compile(r"[^\r\n]*(?:\r\n|\r|\n)|[^\r\n]+")


def _lines(text: str):
    """
    The lines of `text`, line endings kept, split exactly as io.StringIO(text,
    newline="") splits them (at \n, \r and \r\n) - but without StringIO's own
    copy of the text, which holds four bytes for every character: a 25 MB table
    would be 100 MB more in memory just to be read a page at a time.
    """
    for match in _LINE.finditer(text):
        yield match.group()


def _csv_page(text: str, offset: int, limit: int) -> schemas.DocumentTableOut:
    """
    The header, one page of rows and the row total of a CSV document's text.

    Parsed with the csv module on each request: a document is stored as its
    text (that is what keyword search reads), and parsing even tens of
    thousands of rows takes milliseconds, so there is no second copy of the
    data to keep in sync. A quoted field containing line breaks is one row;
    blank lines are skipped; short or long rows are padded to the widest row so
    every page has the same columns.
    """
    header: list[str] | None = None
    page: list[list[str]] = []
    total = 0
    width = 0
    try:
        for record in csv.reader(_lines(text)):
            if not record:
                continue
            width = max(width, len(record))
            if header is None:
                header = record
                continue
            if offset <= total < offset + limit:
                page.append(record)
            total += 1
    except csv.Error as exc:
        raise HTTPException(status_code=400, detail=f"This file can't be shown as a table: {exc}")

    header = header or []
    if header:
        header[0] = header[0].lstrip("\ufeff")  # Excel's byte-order mark rides along on the first column name
    return schemas.DocumentTableOut(
        columns=header + [""] * (width - len(header)),
        rows=[row + [""] * (width - len(row)) for row in page],
        total_rows=total,
        offset=offset,
        limit=limit,
    )


def _log_search(db: Session, query: str, mode: str, role: str, result_count: int) -> None:
    db.add(models.SearchQueryLog(query=query, mode=mode, role=role, result_count=result_count))
    db.commit()


@dataclass
class Citation:
    """
    Exactly which passage a result came from - precise enough to quote, not
    just an approximate snippet window. chunk_index/char_start/char_end are
    None only when a keyword match's character position doesn't fall inside
    any stored chunk (shouldn't normally happen, since chunks cover the
    whole document, but a result should still degrade to a document-level
    citation rather than error).
    """

    chunk_index: int | None
    char_start: int | None
    char_end: int | None

    def label(self, filename: str) -> str:
        if self.chunk_index is None:
            return filename
        return f"{filename}, chunk {self.chunk_index + 1}"


def _snippet_from_index(text: str, idx: int, match_len: int) -> str:
    start = max(0, idx - _SNIPPET_RADIUS)
    end = min(len(text), idx + match_len + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end].strip()}{suffix}"


def _citation_for_char_index(db: Session, document_id: int, char_index: int) -> Citation:
    chunk = (
        db.query(models.DocumentChunk)
        .filter(
            models.DocumentChunk.document_id == document_id,
            models.DocumentChunk.char_start <= char_index,
            models.DocumentChunk.char_end > char_index,
        )
        .first()
    )
    if not chunk:
        return Citation(None, None, None)
    return Citation(chunk.chunk_index, chunk.char_start, chunk.char_end)


def _keyword_matches(db: Session, query: str, limit: int) -> list[tuple[models.Document, str, Citation]]:
    """
    (document, snippet, citation) triples ranked by a simple relevance
    signal - how many times the query appears, with a filename match
    counted for more than a body match. Not real BM25, but enough of a
    signal to rank by, which is what reciprocal rank fusion needs (ranks,
    not comparable raw scores). Covers documents at every access level:
    whether the caller may see a match's snippet is decided where the
    results are built, which swaps a match the caller's code doesn't open
    for a locked hit (see _locked_hit) before anything leaves the router.

    The database finds the matching documents (without their text); each
    text is then read on its own, scored, cut down to its snippet and let go.
    Memory holds one document at a time, however many matched - a saved data
    file can be 25 MB, and a search matching several shouldn't need them all.
    """
    pattern = f"%{query}%"
    lower_query = query.lower()
    candidates = (
        db.query(models.Document)
        .options(defer(models.Document.text_content))
        .filter(
            models.Document.is_latest.is_(True),
            or_(
                models.Document.filename.ilike(pattern),
                models.Document.text_content.ilike(pattern),
            ),
        )
        .all()
    )

    scored = []  # (relevance, document, snippet, where the first match is - or -1)
    for doc in candidates:
        text = db.query(models.Document.text_content).filter(models.Document.id == doc.id).scalar()
        lowered = text.lower()
        idx = lowered.find(lower_query)
        relevance = doc.filename.lower().count(lower_query) * 5 + lowered.count(lower_query)
        if idx == -1:
            # Matched only by filename, not body text - no character
            # position to cite, so fall back to a plain document citation.
            window = text[: _SNIPPET_RADIUS * 2]
            snippet = window + ("…" if len(text) > len(window) else "")
        else:
            snippet = _snippet_from_index(text, idx, len(query))
        scored.append((relevance, doc, snippet, idx))
        del text, lowered

    scored.sort(key=lambda item: item[0], reverse=True)  # stable: equal scores keep the database's order

    results = []
    for _, doc, snippet, idx in scored[:limit]:
        citation = Citation(None, None, None) if idx == -1 else _citation_for_char_index(db, doc.id, idx)
        results.append((doc, snippet, citation))
    return results


def _semantic_matches(db: Session, query: str, limit: int) -> list[tuple[models.Document, str, float, Citation]]:
    """
    (document, snippet, similarity, citation) tuples, one per document - its
    single best-matching chunk - ranked by cosine similarity to the
    embedded query. The citation here is exact by construction: semantic
    search matches a specific chunk directly, no lookup needed. Like keyword
    search it covers every access level and leaves it to the caller to lock
    the matches the code doesn't open.
    """
    query_vector = embed_texts([query])[0]

    chunks = (
        db.query(models.DocumentChunk)
        .join(models.Document)
        .filter(models.Document.is_latest.is_(True))
        .all()
    )

    best_per_doc: dict[int, tuple[float, models.DocumentChunk]] = {}
    for chunk in chunks:
        score = cosine_similarity(query_vector, chunk.embedding)
        current = best_per_doc.get(chunk.document_id)
        if current is None or score > current[0]:
            best_per_doc[chunk.document_id] = (score, chunk)

    ranked = sorted(best_per_doc.values(), key=lambda pair: pair[0], reverse=True)[:limit]

    results = []
    for score, chunk in ranked:
        if score <= 0:
            continue
        snippet = chunk.chunk_text[:_SEMANTIC_SNIPPET_LENGTH]
        if len(chunk.chunk_text) > _SEMANTIC_SNIPPET_LENGTH:
            snippet += "…"
        citation = Citation(chunk.chunk_index, chunk.char_start, chunk.char_end)
        results.append((chunk.document, snippet, score, citation))
    return results


@router.post("", response_model=schemas.DocumentOut, dependencies=[Depends(upload_slot)])
def upload_document(
    file: UploadFile = File(...),
    access_level: str = Form("public"),
    db: Session = Depends(get_db),
):
    access_level = validated_access_level(access_level)

    contents = read_upload(file)
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        content_type, text = extract_text(file.filename, contents)
    except UnsupportedFileType as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except TextTooLong:
        raise document_too_long()
    except FileTooLarge as exc:
        raise too_large(str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Could not read '{file.filename}': {exc}")

    if not text:
        raise HTTPException(status_code=400, detail="No extractable text found in this file")

    # Re-uploading a filename is a new version, not a duplicate - the
    # versioning, embedding and (all-or-nothing) saving live in store_document,
    # shared with the quality-report save so both behave identically.
    return store_document(db, file.filename, content_type, text, access_level)


@router.get("", response_model=list[schemas.ListedDocument])
def list_documents(
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Every current document, newest first. The ones the caller's code opens come
    back in full; the rest come back locked - name, type, level and date, so
    anyone can see that a document exists and what it takes to open it, but
    nothing from inside it.
    """
    allowed_levels = _allowed_levels(role, access_code, access_id)
    docs = (
        db.query(models.Document)
        .options(defer(models.Document.text_content))  # the list shows names and levels; saved data files are big
        .filter(models.Document.is_latest.is_(True))
        .order_by(models.Document.uploaded_at.desc())
        .all()
    )
    return [
        schemas.DocumentListEntry.model_validate(doc)
        if doc.access_level in allowed_levels
        else schemas.LockedDocumentOut.model_validate(doc)
        for doc in docs
    ]


@router.get("/search", response_model=list[schemas.KeywordHit])
def search_documents(
    q: str,
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Plain substring search (case-insensitive) over filenames and extracted
    text - portable across SQLite (local) and Postgres (docker-compose),
    unlike SQLite's FTS5 which only works on one of those backends. Searches
    every document; a match the caller's code doesn't open comes back locked
    (name and level only), the same as in the list.
    """
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    allowed_levels = _allowed_levels(role, access_code, access_id)
    matches = _keyword_matches(db, query, _RESULT_LIMIT)
    _log_search(db, query, "keyword", role, len(matches))

    return [
        schemas.DocumentSearchResult(
            id=doc.id,
            filename=doc.filename,
            content_type=doc.content_type,
            access_level=doc.access_level,
            snippet=snippet,
            chunk_index=citation.chunk_index,
            char_start=citation.char_start,
            char_end=citation.char_end,
            citation=citation.label(doc.filename),
        )
        if doc.access_level in allowed_levels
        else _locked_hit(doc)
        for doc, snippet, citation in matches
    ]


@router.get("/semantic-search", response_model=list[schemas.SemanticHit])
def semantic_search_documents(
    q: str,
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Matches on meaning, not shared words - embeds the query and ranks every
    stored chunk by cosine similarity to it. Brute-force (no vector index),
    which is fine at this dataset size and keeps this portable across
    SQLite and Postgres without adding a vector-search extension. Covers
    every document; a match the caller's code doesn't open comes back locked.
    """
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    allowed_levels = _allowed_levels(role, access_code, access_id)
    matches = _semantic_matches(db, query, _RESULT_LIMIT)
    _log_search(db, query, "semantic", role, len(matches))

    return [
        schemas.SemanticSearchResult(
            id=doc.id,
            filename=doc.filename,
            content_type=doc.content_type,
            access_level=doc.access_level,
            snippet=snippet,
            similarity=round(score, 3),
            chunk_index=citation.chunk_index,
            char_start=citation.char_start,
            char_end=citation.char_end,
            citation=citation.label(doc.filename),
        )
        if doc.access_level in allowed_levels
        else _locked_hit(doc)
        for doc, snippet, score, citation in matches
    ]


@router.get("/hybrid-search", response_model=list[schemas.HybridHit])
def hybrid_search_documents(
    q: str,
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Merges keyword and semantic results with Reciprocal Rank Fusion: each
    document's score is the sum of 1/(k + rank) across whichever of the two
    ranked lists it appears in. RRF is the standard way production hybrid
    search combines rankings from scoring methods that aren't on the same
    scale - a cosine similarity and a substring count can't be averaged
    meaningfully, but their ranks can be fused. Covers every document; a
    match the caller's code doesn't open comes back locked.
    """
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Search query cannot be empty")
    allowed_levels = _allowed_levels(role, access_code, access_id)

    keyword_results = _keyword_matches(db, query, _RESULT_LIMIT)
    semantic_results = _semantic_matches(db, query, _RESULT_LIMIT)

    scores: dict[int, float] = {}
    documents: dict[int, models.Document] = {}
    snippets: dict[int, str] = {}
    citations: dict[int, Citation] = {}
    sources: dict[int, set] = {}
    similarities: dict[int, float] = {}

    for rank, (doc, snippet, citation) in enumerate(keyword_results, start=1):
        scores[doc.id] = scores.get(doc.id, 0.0) + 1 / (_RRF_K + rank)
        documents[doc.id] = doc
        sources.setdefault(doc.id, set()).add("keyword")
        snippets[doc.id] = snippet  # a literal keyword snippet is preferred whenever one exists
        citations[doc.id] = citation

    for rank, (doc, snippet, similarity, citation) in enumerate(semantic_results, start=1):
        scores[doc.id] = scores.get(doc.id, 0.0) + 1 / (_RRF_K + rank)
        documents[doc.id] = doc
        sources.setdefault(doc.id, set()).add("semantic")
        similarities[doc.id] = round(similarity, 3)
        snippets.setdefault(doc.id, snippet)  # only used if this document had no keyword snippet
        citations.setdefault(doc.id, citation)  # only used if this document had no keyword citation

    ranked_ids = sorted(scores, key=lambda doc_id: scores[doc_id], reverse=True)[:_RESULT_LIMIT]
    _log_search(db, query, "hybrid", role, len(ranked_ids))

    return [
        schemas.HybridSearchResult(
            id=doc_id,
            filename=documents[doc_id].filename,
            content_type=documents[doc_id].content_type,
            access_level=documents[doc_id].access_level,
            snippet=snippets[doc_id],
            sources=sorted(sources[doc_id]),
            similarity=similarities.get(doc_id),
            score=round(scores[doc_id], 4),
            chunk_index=citations[doc_id].chunk_index,
            char_start=citations[doc_id].char_start,
            char_end=citations[doc_id].char_end,
            citation=citations[doc_id].label(documents[doc_id].filename),
        )
        if documents[doc_id].access_level in allowed_levels
        else _locked_hit(documents[doc_id])
        for doc_id in ranked_ids
    ]


@router.get("/analytics/summary", response_model=schemas.SearchAnalyticsSummary)
def get_search_analytics(
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Aggregated over every logged search: volume by mode, the most frequent
    queries, and queries that came back empty - a zero-result query is a
    content gap, the single most actionable signal a search analytics view
    can surface. Gated to manager/admin since query patterns can themselves
    reveal what's sensitive, same reasoning as any other access-controlled
    view in this app.
    """
    if role not in _ROLE_MAX_LEVEL:
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'. Expected one of {list(_ROLE_MAX_LEVEL)}.")
    authorize_role(role, access_code, access_id)
    if role not in _ANALYTICS_ROLES:
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot view search analytics")

    mode_rows = db.query(models.SearchQueryLog.mode, func.count(models.SearchQueryLog.id)).group_by(
        models.SearchQueryLog.mode
    )
    searches_by_mode = {"keyword": 0, "semantic": 0, "hybrid": 0}
    for mode, count in mode_rows:
        searches_by_mode[mode] = count
    total_searches = sum(searches_by_mode.values())

    lower_query = func.lower(models.SearchQueryLog.query)
    top_queries = (
        db.query(lower_query.label("query"), func.count(models.SearchQueryLog.id).label("count"))
        .group_by(lower_query)
        .order_by(func.count(models.SearchQueryLog.id).desc())
        .limit(_TOP_QUERY_LIMIT)
        .all()
    )
    zero_result_queries = (
        db.query(lower_query.label("query"), func.count(models.SearchQueryLog.id).label("count"))
        .filter(models.SearchQueryLog.result_count == 0)
        .group_by(lower_query)
        .order_by(func.count(models.SearchQueryLog.id).desc())
        .limit(_TOP_QUERY_LIMIT)
        .all()
    )

    return schemas.SearchAnalyticsSummary(
        total_searches=total_searches,
        searches_by_mode=searches_by_mode,
        top_queries=[schemas.QueryCount(query=q, count=c) for q, c in top_queries],
        zero_result_queries=[schemas.QueryCount(query=q, count=c) for q, c in zero_result_queries],
    )


@router.delete("/search-logs", response_model=schemas.DeleteSummary)
def delete_search_logs(
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Permanently deletes every logged search query - no filter, no undo.
    Gated the same way viewing analytics is: whoever can see the query
    patterns is who's allowed to clear them.
    """
    if role not in _ROLE_MAX_LEVEL:
        raise HTTPException(status_code=400, detail=f"Unknown role '{role}'. Expected one of {list(_ROLE_MAX_LEVEL)}.")
    authorize_role(role, access_code, access_id)
    if role not in _ANALYTICS_ROLES:
        raise HTTPException(status_code=403, detail=f"Role '{role}' cannot delete search logs")

    count = db.query(models.SearchQueryLog).delete()
    db.commit()
    return schemas.DeleteSummary(rows_deleted=count)


@router.post("/access/unlock", response_model=schemas.UnlockOut)
def unlock_access(body: schemas.UnlockRequest):
    """
    Checks an access code (and, for confidential, the ID number) and says what
    it opens. The page calls this when someone presses Unlock, so a wrong code
    is caught right there rather than showing up later as documents that
    mysteriously aren't listed. The confidential code on its own comes back as
    team access with `needs_id: true` - the page then asks for the ID on its
    pad and calls this again with both. Nothing is remembered server-side:
    every later request carries the code and ID again.
    """
    role, level, needs_id = unlock(body.code, body.id)
    return schemas.UnlockOut(role=role, level=level, needs_id=needs_id)


@router.get("/access/hint", response_model=schemas.AccessHintOut)
def get_access_hint():
    """
    The codes themselves - and the confidential ID number, which goes with the
    confidential code and nothing else - for the page's hint box, so they
    aren't forgotten while the site is still a demo. Turn it off with
    SHOW_ACCESS_HINT=false (and change the codes) before the site is on a
    public URL.
    """
    if not settings.show_access_hint:
        return schemas.AccessHintOut(enabled=False)
    return schemas.AccessHintOut(
        enabled=True,
        team_code=settings.team_access_code,
        confidential_code=settings.confidential_access_code,
        confidential_id=settings.confidential_id_number,
    )


@router.get("/{document_id}", response_model=schemas.DocumentDetailOut)
def get_document(
    document_id: int,
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    doc, _ = _visible_document(db, document_id, role, access_code, access_id)
    return doc


@router.get("/{document_id}/table", response_model=schemas.DocumentTableOut)
def get_document_table(
    document_id: int,
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=_TABLE_PAGE_SIZE, ge=1, le=_TABLE_MAX_PAGE),
    db: Session = Depends(get_db),
):
    """
    A CSV document as a table - its header plus one page of rows - for the
    page's viewer. Same permission check as opening the document itself, so a
    document the caller can't open can't be read through here either.
    """
    doc, _ = _visible_document(db, document_id, role, access_code, access_id)
    if doc.content_type != "csv":
        raise HTTPException(status_code=400, detail="Only CSV documents can be shown as a table")
    return _csv_page(doc.text_content, offset, limit)


@router.get("/{document_id}/history", response_model=list[schemas.DocumentOut])
def get_document_history(
    document_id: int,
    role: str = _DEFAULT_ROLE,
    access_code: AccessCode = None,
    access_id: AccessId = None,
    db: Session = Depends(get_db),
):
    """
    Every version ever uploaded under this document's filename, newest
    first - re-uploading a filename doesn't overwrite it, it supersedes it,
    so old versions stay queryable for audit even though search only
    surfaces the latest one. A version above the caller's clearance is
    left out of the list rather than raising.
    """
    doc, allowed_levels = _visible_document(db, document_id, role, access_code, access_id, with_text=False)

    return (
        db.query(models.Document)
        .options(defer(models.Document.text_content))
        .filter(models.Document.filename == doc.filename, models.Document.access_level.in_(allowed_levels))
        .order_by(models.Document.version.desc())
        .all()
    )
