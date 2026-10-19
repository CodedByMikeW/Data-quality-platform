"""
The one place a piece of text becomes a searchable Knowledge Search document:
versioning, chunking, embedding and saving. Both the file-upload endpoint
and "save a quality report" go through store_document(), so they version and
permission documents identically.
"""
from fastapi import HTTPException
from sqlalchemy.orm import Session, defer

from app import models
from app.config import settings
from app.embeddings import chunk_text, count_chunks, embed_texts
from app.limits import document_too_long

# Ordered least -> most restricted, so a role's access is "this level and
# everything before it" rather than needing an explicit list per role.
ACCESS_LEVELS = ["public", "team", "confidential"]

# Every column but the text: what a saved document is described by. The text can be 25 MB.
_DESCRIPTION_COLUMNS = [column.key for column in models.Document.__table__.columns if column.key != "text_content"]


def validated_access_level(access_level: str) -> str:
    if access_level not in ACCESS_LEVELS:
        raise HTTPException(
            status_code=400, detail=f"Unknown access_level '{access_level}'. Expected one of {ACCESS_LEVELS}."
        )
    return access_level


def store_document(
    db: Session, filename: str, content_type: str, text: str, access_level: str, embed: bool = True
) -> models.Document:
    """
    Saves `text` as the newest version of `filename`.

    embed=False stores the text without chunks or embeddings. That is the right
    call for big tabular data: embedding thousands of rows is slow, and a
    "similar meaning" match over rows of a table isn't useful. The document is
    still listed, can be opened, and is found by keyword search - it just never
    appears in semantic results.

    Saving the same filename again isn't a duplicate - it's a new version.
    The previous latest is kept (not deleted) for history, just demoted so
    search and listings only ever surface the current one.

    All-or-nothing: embedding is the step that can fail (the search model
    missing or unable to load), so it runs BEFORE anything is written, and
    the demote / insert / chunk writes then go in as a single transaction.
    A failure therefore leaves the previous version exactly as it was -
    never a latest document with no searchable chunks.
    """
    if embed and count_chunks(text) > settings.max_document_chunks:
        raise document_too_long()  # before any embedding work: that is the slow, memory-hungry part

    chunks = chunk_text(text) if embed else []
    vectors = embed_texts([chunk for chunk, _, _ in chunks]) if chunks else []

    try:
        # Only its version number and flag are used - not its text, which can be as big as the new one.
        previous = (
            db.query(models.Document)
            .options(defer(models.Document.text_content))
            .filter_by(filename=filename, is_latest=True)
            .first()
        )
        version = 1
        if previous:
            version = previous.version + 1
            previous.is_latest = False

        doc = models.Document(
            filename=filename,
            content_type=content_type,
            text_content=text,
            char_count=len(text),
            access_level=access_level,
            version=version,
            is_latest=True,
        )
        db.add(doc)
        db.flush()  # assigns doc.id so the chunks can point at it, without committing yet

        db.add_all(
            models.DocumentChunk(
                document_id=doc.id,
                chunk_index=i,
                chunk_text=chunk,
                char_start=char_start,
                char_end=char_end,
                embedding=vector,
            )
            for i, ((chunk, char_start, char_end), vector) in enumerate(zip(chunks, vectors))
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    # What the callers report on (id, version, level...) - not the text again, which they never read back.
    db.refresh(doc, attribute_names=_DESCRIPTION_COLUMNS)
    return doc
