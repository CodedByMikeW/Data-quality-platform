"""
How much one upload is allowed to cost, so a single huge file - or a small file
that is huge once it is opened - can't use up the server's memory and take the
site down for everyone.

The numbers live in app/config.py (MAX_UPLOAD_MB, MAX_CHECK_ROWS, ...) and are
read on every call, not copied at import, so changing the setting changes the
limit. What each guard stops:

- BodySizeLimitMiddleware: a request body bigger than any upload form could
  send, refused before the file is read (or, with no Content-Length, as soon as
  too many bytes have arrived).
- read_upload: one file bigger than the limit, refused after reading at most
  limit + 1 bytes.
- read_csv_limited: a CSV with too many rows, columns or cells - found by
  parsing no more than limit + 1 rows, so a 50 MB file is never fully parsed.
- upload_slot: several big uploads worked on at the same moment - they take
  turns instead of adding their memory together.

All of them answer HTTP 413 with a plain-language "detail" (the page shows it
as it is). The page also checks file sizes itself before uploading anything,
using the numbers from GET /limits; the server-side checks are what a script
calling the API directly runs into.
"""
import io
import re
import threading
from collections.abc import Collection

import pandas as pd
from fastapi import HTTPException, UploadFile
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

_FORM_OVERHEAD_BYTES = 1024 * 1024  # multipart boundaries and the small text fields that travel with a file
_MAX_HEADER_BYTES = 64 * 1024  # a real CSV header row is a few hundred bytes; a longer first line isn't one
_SLOT_WAIT_SECONDS = 20  # how long a big upload waits for its turn before the server says it is busy
_RUN_PATH = re.compile(r"/datasets/[^/]+/runs/?")  # the one request that carries a file plus its related files

_slots = threading.BoundedSemaphore(settings.max_concurrent_uploads)


def size_text(n_bytes: int) -> str:
    return f"{n_bytes / (1024 * 1024):.1f} MB"


def limit_text() -> str:
    return f"{settings.max_upload_mb} MB"


def too_large(detail: str) -> HTTPException:
    return HTTPException(status_code=413, detail=detail)


def public_limits() -> dict:
    """What GET /limits tells the page, so its "too big" check uses the same numbers as the server."""
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_upload_mb": settings.max_upload_mb,
        "max_check_rows": settings.max_check_rows,
        "max_check_columns": settings.max_check_columns,
        "max_check_cells": settings.max_check_cells,
        "max_reference_files": settings.max_reference_files,
        "max_incident_rows": settings.max_incident_rows,
        "max_document_chunks": settings.max_document_chunks,
    }


def read_upload(file: UploadFile, limit_bytes: int | None = None) -> bytes:
    """
    The file's bytes, or HTTP 413 if it is bigger than the limit. Reads at most
    limit + 1 bytes, so an oversized file is never pulled into memory whole just
    to be turned away.
    """
    limit = settings.max_upload_bytes if limit_bytes is None else limit_bytes
    data = file.file.read(limit + 1)
    if len(data) > limit:
        limit_label = limit_text() if limit_bytes is None else size_text(limit)
        how_big = f"is {size_text(file.size)} - " if file.size else "is "
        raise too_large(f"'{file.filename}' {how_big}over the {limit_label} limit for one file. Try a smaller file.")
    return data


def read_csv_limited(
    contents: bytes,
    filename: str,
    *,
    max_rows: int,
    max_columns: int | None = None,
    max_cells: int | None = None,
    parse_error: str = "Could not parse CSV",
    too_many_rows_hint: str = "Try a smaller extract - a sample of the rows turns up the same kinds of problems.",
    columns: Collection[str] | None = None,
) -> pd.DataFrame:
    """
    `contents` parsed as a CSV - or HTTP 413 naming the limit it is over (rows,
    columns, or rows x columns), or HTTP 400 `parse_error: ...` if it isn't a CSV.

    The header comes first and on its own, so a file with an absurd number of
    columns is turned away before its rows are touched. Rows are then parsed
    with nrows = limit + 1: one row past the limit is enough to know the file is
    too long, and it keeps a huge file from ever being parsed in full.

    `columns`, when given, are the only columns read: the file's other columns
    still count toward max_columns (a 5,000-column file is turned away either
    way) but are never held in memory, and the cell limit counts what is read.
    A file that is only looked up in - a related file whose one key column is
    all a relationship check needs - then costs a fraction of its size. If none
    of the names is in the file, its first column is read: the rows still have
    to be counted for the row limit, and one column is the cheapest way to.
    """
    if len(contents) > _MAX_HEADER_BYTES and not re.search(rb"[\r\n]", contents[: _MAX_HEADER_BYTES + 1]):
        raise too_large(
            f"The first line of '{filename}' is longer than {_MAX_HEADER_BYTES // 1024} KB, "
            "so it isn't a CSV header row."
        )

    try:
        header = pd.read_csv(io.BytesIO(contents), nrows=0).columns
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{parse_error}: {exc}")

    n_columns = len(header)
    if max_columns is not None and n_columns > max_columns:
        raise too_large(
            f"'{filename}' has {n_columns:,} columns - a check handles up to {max_columns:,}. "
            "Try leaving out the columns you don't need."
        )

    # Positions, not names: a header with the same name twice has no name the second one can be asked for by.
    wanted = None if columns is None else ([i for i, name in enumerate(header) if name in columns] or [0])
    n_loaded = n_columns if wanted is None else len(wanted)

    row_limit, limited_by_cells = max_rows, False
    if max_cells is not None and n_loaded and max_cells // n_loaded < row_limit:
        row_limit, limited_by_cells = max_cells // n_loaded, True

    try:
        df = pd.read_csv(io.BytesIO(contents), nrows=row_limit + 1, usecols=wanted)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"{parse_error}: {exc}")

    if len(df) > row_limit:
        if limited_by_cells:
            raise too_large(
                f"'{filename}' is too big to check in one go: with {n_loaded:,} columns it can have "
                f"up to {row_limit:,} rows ({max_cells:,} cells in all). Try fewer rows or fewer columns."
            )
        raise too_large(f"'{filename}' has more than {max_rows:,} rows, the most one upload takes. {too_many_rows_hint}")
    return df


def document_too_long() -> HTTPException:
    """Knowledge Search embeds every chunk of a document when it is uploaded, so a book-length one is turned away."""
    words = settings.max_document_chunks * 120  # a chunk is 150 words and shares 30 with the one before it
    return too_large(
        f"This document is too long to index for search - the limit is about {words:,} words "
        "(a few hundred pages). Split it into parts or upload a shorter version."
    )


def upload_slot():
    """
    A FastAPI dependency: holds one of a few places for working on an upload
    (parsing, checking, indexing) until the request is done. Two 25 MB files
    checked at the same moment need twice the memory of one, so beyond a couple
    the rest wait their turn; if the wait runs long the server says it is busy
    instead of piling more on.
    """
    if not _slots.acquire(timeout=_SLOT_WAIT_SECONDS):
        raise HTTPException(
            status_code=503,
            detail="The server is busy checking other files right now. Wait a few seconds and try again.",
            headers={"Retry-After": "10"},
        )
    try:
        yield
    finally:
        _slots.release()


def max_body_bytes(path: str) -> int:
    """The biggest request body the server reads for this path: its files plus a little for form fields."""
    files = 1 + settings.max_reference_files if _RUN_PATH.fullmatch(path) else 1
    return files * settings.max_upload_bytes + _FORM_OVERHEAD_BYTES


def _declared_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


class BodySizeLimitMiddleware:
    """
    Refuses a request body bigger than max_body_bytes(path) with HTTP 413.

    Pure ASGI on purpose (not BaseHTTPMiddleware): it has to answer before
    anything reads the body. A request that announces its size in Content-Length
    is refused without reading a byte. One that doesn't (chunked uploads) is
    counted as it streams in, and refused when it passes the limit - the
    HTTPException raised from receive() is turned into the 413 by the framework,
    the same as any other error from the app.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in ("POST", "PUT", "PATCH"):
            await self.app(scope, receive, send)
            return

        limit = max_body_bytes(scope["path"])
        declared = _declared_length(scope)
        if declared is not None and declared > limit:
            detail = (
                f"This upload is {size_text(declared)}; the most the server takes in one request "
                f"is {size_text(limit)}. Try a smaller file."
            )
            await JSONResponse({"detail": detail}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise too_large(
                        f"This upload is over the {size_text(limit)} the server takes in one request. "
                        "Try a smaller file."
                    )
            return message

        await self.app(scope, counting_receive, send)
