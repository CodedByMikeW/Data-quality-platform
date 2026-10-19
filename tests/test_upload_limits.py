"""
Upload limits: one file - however big, or however oddly shaped - must not be able
to use up the server's memory and take the site down for everyone.

Every limit is read from app.config on each request, so these tests lower it
(monkeypatch) and test at the edge: a file exactly at the limit is taken, one
byte over is turned away with HTTP 413 and a message that says what to do. The
middleware is also driven directly, the way a client that doesn't announce its
size (a chunked upload) would hit it. Embeddings are stubbed.
"""
import asyncio
import io
import json
import threading
import time
import zipfile

import pytest
from docx import Document as DocxFile
from fastapi import HTTPException, UploadFile

from app import limits, models
from app.config import settings
from app.database import SessionLocal, insert_in_batches
from app.document_extraction import FileTooLarge, TextTooLong, extract_text
from app.document_store import store_document
from app.embeddings import chunk_text, count_chunks

MB = 1024 * 1024


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    stub = lambda texts: [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in texts]  # noqa: E731
    monkeypatch.setattr("app.document_store.embed_texts", stub)
    monkeypatch.setattr("app.routers.documents.embed_texts", stub)


# ---------------------------------------------------------------- small helpers


def _table(rows: int, columns: int = 3) -> bytes:
    """A CSV with `rows` data rows and `columns` columns (the first is a key)."""
    lines = [",".join(["id"] + [f"c{i}" for i in range(1, columns)])]
    for r in range(rows):
        lines.append(",".join([f"R-{r:06d}"] + [str((r + 1) * (c + 1) % 97) for c in range(1, columns)]))
    return ("\n".join(lines) + "\n").encode()


def _exact_csv(n_bytes: int) -> bytes:
    """A real two-column CSV that is exactly n_bytes long."""
    out = bytearray(b"id,note\n")
    i = 0
    while n_bytes - len(out) > 200:
        out += f"{i:06d},{'x' * 56}\n".encode()  # 64 bytes a row
        i += 1
    last = n_bytes - len(out)
    out += f"{i:06d},{'x' * (last - 8)}\n".encode()
    assert len(out) == n_bytes
    return bytes(out)


def _run(client, dataset, content, filename="data.csv", extra=()):
    files = [("file", (filename, content, "text/csv")), *extra]
    return client.post(f"/datasets/{dataset}/runs", files=files)


def _document(client, filename, content, level="public"):
    return client.post("/documents", files={"file": (filename, content, "application/octet-stream")}, data={"access_level": level})


def _detail(resp) -> str:
    return resp.json()["detail"]


def _docx(paragraphs: list[str]) -> bytes:
    doc = DocxFile()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _unzips_to(megabytes: int, part: str = "word/document.xml") -> bytes:
    """
    A tiny file that unpacks to `megabytes` MB: what a zip bomb looks like to the Word reader.
    `part` is the one thing in it: the text part by default, or e.g. "word/media/scan.bin" for a picture.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(part, b"0" * (megabytes * MB))
    return buf.getvalue()


def _unzips_in_parts(parts: int, megabytes_each: int, name: str = "word/part{i}.bin") -> bytes:
    """Many parts that are each fine but add up to a lot: the other shape a zip bomb takes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i in range(parts):
            z.writestr(name.format(i=i), b"0" * (megabytes_each * MB))
    return buf.getvalue()


def _pdf(pages: list[str]) -> bytes:
    """A minimal real PDF with one line of text per page (plain words only: no parentheses)."""
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {n} >>",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    for i, text in enumerate(pages):
        content = f"BT /F1 12 Tf 20 700 Td ({text}) Tj ET"
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {5 + 2 * i} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    out = b"%PDF-1.4\n"
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{offset:010d} 00000 n \n" for offset in offsets).encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


# ---------------------------------------------------------------- the numbers the page uses


def test_the_page_can_ask_the_server_what_the_limits_are(anonymous_client, monkeypatch):
    body = anonymous_client.get("/limits").json()
    assert body["max_upload_bytes"] == settings.max_upload_mb * MB
    assert body["max_upload_mb"] == settings.max_upload_mb
    assert body["max_check_rows"] == settings.max_check_rows
    for key in ("max_check_columns", "max_check_cells", "max_reference_files", "max_incident_rows", "max_document_chunks"):
        assert body[key] == getattr(settings, key) > 0

    monkeypatch.setattr(settings, "max_upload_mb", 3)
    assert anonymous_client.get("/limits").json()["max_upload_bytes"] == 3 * MB


def test_the_defaults_are_big_enough_to_be_useful_and_small_enough_to_be_safe():
    assert 5 <= settings.max_upload_mb <= 25
    assert 50_000 <= settings.max_check_rows <= 1_000_000
    assert settings.max_check_cells <= 6_000_000
    assert settings.max_concurrent_uploads <= 4


# ---------------------------------------------------------------- one file's size


def test_a_file_exactly_at_the_limit_is_taken_and_one_byte_over_is_not(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)

    at_limit = _run(client, "edge", _exact_csv(MB))
    assert at_limit.status_code == 200, at_limit.text
    assert at_limit.json()["row_count"] > 10_000

    over = _run(client, "edge-over", _exact_csv(MB + 1))
    assert over.status_code == 413
    assert "over the 1 MB limit for one file" in _detail(over)
    assert "Try a smaller file" in _detail(over)
    assert client.get("/datasets/edge-over/runs").status_code == 404  # nothing was created for it


def test_read_upload_takes_exactly_the_limit_and_refuses_one_byte_more():
    def upload(n):
        return UploadFile(io.BytesIO(b"a" * n), filename="f.csv", size=n)

    assert len(limits.read_upload(upload(100), limit_bytes=100)) == 100
    with pytest.raises(HTTPException) as exc:
        limits.read_upload(upload(101), limit_bytes=100)
    assert exc.value.status_code == 413
    assert "'f.csv'" in exc.value.detail


def test_an_oversized_file_is_never_read_whole():
    class Spy(io.BytesIO):
        asked: list = []

        def read(self, n=-1):
            self.asked.append(n)
            return super().read(n)

    spy = Spy(b"x" * 10_000)
    spy.asked = []
    with pytest.raises(HTTPException):
        limits.read_upload(UploadFile(spy, filename="big.csv", size=10_000), limit_bytes=100)
    assert spy.asked == [101]  # one byte past the limit is enough to know


def test_every_upload_form_refuses_an_oversized_file(client, monkeypatch):
    """
    Each file is a little over 1 MB but has one row and one word, so no row,
    column or length limit could be what turns it away - only the size limit.
    """
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    blob = b"x" * (MB + 10)
    logs = b"timestamp,service,level,message\n2026-01-01T00:00:00Z,api,INFO," + blob + b"\n"
    metrics = b"timestamp,service,metric_name,value\n2026-01-01T00:00:00Z,api," + blob + b",1\n"
    forms = {
        "check": lambda: _run(client, "x", b"id,note\n1," + blob + b"\n"),
        "related file": lambda: _run(client, "x", _table(5), extra=[("reference_files", ("ref.csv", b"id,note\n1," + blob + b"\n", "text/csv"))]),
        "document": lambda: _document(client, "big.txt", blob),
        "logs": lambda: client.post("/incidents/logs/upload", files={"file": ("l.csv", logs, "text/csv")}),
        "metrics": lambda: client.post("/incidents/metrics/upload", files={"file": ("m.csv", metrics, "text/csv")}),
    }
    for name, upload in forms.items():
        resp = upload()
        assert resp.status_code == 413, name
        assert "over the 1 MB limit for one file" in _detail(resp), name


def test_the_refusal_names_the_file_and_says_how_big_it_is(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    resp = _document(client, "manual.txt", b"x" * (MB + MB // 2))
    assert resp.status_code == 413
    assert _detail(resp) == "'manual.txt' is 1.5 MB - over the 1 MB limit for one file. Try a smaller file."


# ---------------------------------------------------------------- the request as a whole


def test_a_request_far_bigger_than_any_form_sends_is_refused_up_front(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    resp = _document(client, "huge.txt", b"a" * (3 * MB))  # the most /documents takes is 1 MB plus form fields
    assert resp.status_code == 413
    assert "the most the server takes in one request is 2.0 MB" in _detail(resp)


def test_the_check_form_gets_room_for_its_related_files_and_nothing_else_does(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 10)
    monkeypatch.setattr(settings, "max_reference_files", 3)
    one_file = 10 * MB + MB
    assert limits.max_body_bytes("/documents") == one_file
    assert limits.max_body_bytes("/incidents/logs/upload") == one_file
    assert limits.max_body_bytes("/datasets/customers/runs") == 4 * 10 * MB + MB
    assert limits.max_body_bytes("/datasets/customers/runs/") == 4 * 10 * MB + MB
    assert limits.max_body_bytes("/datasets/customers/runs/5/knowledge-report") == one_file


def _scope(method="POST", path="/documents", length=None):
    headers = [] if length is None else [(b"content-length", str(length).encode())]
    return {"type": "http", "method": method, "path": path, "headers": headers}


async def _discard(message):
    return None


def test_a_declared_size_over_the_limit_is_refused_before_a_byte_is_read(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    limit = limits.max_body_bytes("/documents")
    seen = {"app": False, "receive": False}
    sent = []

    async def app(scope, receive, send):
        seen["app"] = True

    async def receive():
        seen["receive"] = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    asyncio.run(limits.BodySizeLimitMiddleware(app)(_scope(length=limit + 1), receive, send))

    assert seen == {"app": False, "receive": False}
    assert sent[0]["status"] == 413
    assert "Try a smaller file" in json.loads(sent[1]["body"])["detail"]


def test_a_declared_size_at_the_limit_goes_through(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    limit = limits.max_body_bytes("/documents")
    reached = []

    async def app(scope, receive, send):
        reached.append(True)

    asyncio.run(limits.BodySizeLimitMiddleware(app)(_scope(length=limit), None, _discard))
    assert reached == [True]


def test_only_uploads_are_limited(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    reached = []

    async def app(scope, receive, send):
        reached.append(scope["method"])

    for method in ("GET", "DELETE", "HEAD"):
        asyncio.run(limits.BodySizeLimitMiddleware(app)(_scope(method=method, length=10 * MB), None, _discard))
    assert reached == ["GET", "DELETE", "HEAD"]


def test_every_method_that_can_carry_a_file_is_limited(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    for method in ("POST", "PUT", "PATCH"):
        sent = []

        async def app(scope, receive, send):
            raise AssertionError(f"a {method} request over the limit got through")

        async def send(message):
            sent.append(message)

        asyncio.run(limits.BodySizeLimitMiddleware(app)(_scope(method=method, length=10 * MB), None, send))
        assert sent[0]["status"] == 413, method


def _chunks(sizes):
    pending = [b"a" * n for n in sizes]

    async def receive():
        body = pending.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(pending)}

    return receive


async def _read_whole_body(receive):
    async def app(scope, receive, send):
        while (await receive()).get("more_body"):
            pass

    await limits.BodySizeLimitMiddleware(app)(_scope(), receive, _discard)


def test_a_body_that_never_says_its_size_is_stopped_once_it_is_too_big(monkeypatch):
    """Chunked uploads have no Content-Length: the bytes are counted as they arrive."""
    monkeypatch.setattr(settings, "max_upload_mb", 1)  # 2 MB for a whole request on /documents

    asyncio.run(_read_whole_body(_chunks([MB, MB])))  # exactly 2 MB: fine

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_read_whole_body(_chunks([MB, MB, 1])))
    assert exc.value.status_code == 413


def test_a_chunked_upload_through_the_real_app_gets_a_clean_413(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    boundary = "testboundary"

    def body():  # a streamed generator: no Content-Length header
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="big.txt"\r\n\r\n'.encode()
        for _ in range(40):
            yield b"a" * (64 * 1024)  # 2.5 MB in all
        yield f"\r\n--{boundary}--\r\n".encode()

    resp = client.post("/documents", content=body(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert resp.status_code == 413
    assert "over the 2.0 MB the server takes in one request" in _detail(resp)


# ---------------------------------------------------------------- rows, columns, cells


def test_a_check_takes_the_row_limit_but_not_one_row_more(client, monkeypatch):
    monkeypatch.setattr(settings, "max_check_rows", 50)
    assert _run(client, "rows", _table(50)).status_code == 200

    over = _run(client, "rows-over", _table(51))
    assert over.status_code == 413
    assert "'data.csv' has more than 50 rows, the most one upload takes" in _detail(over)


def test_a_check_takes_the_column_limit_but_not_one_more(client, monkeypatch):
    monkeypatch.setattr(settings, "max_check_columns", 5)
    assert _run(client, "cols", _table(20, columns=5)).status_code == 200

    over = _run(client, "cols-over", _table(20, columns=6))
    assert over.status_code == 413
    assert "has 6 columns - a check handles up to 5" in _detail(over)


def test_a_very_wide_file_is_turned_away_by_its_header_alone(client):
    wide = _table(3, columns=5_000)
    assert len(wide) < MB
    resp = _run(client, "wide", wide)
    assert resp.status_code == 413
    assert "has 5,000 columns" in _detail(resp)


def test_rows_times_columns_is_limited_too(client, monkeypatch):
    monkeypatch.setattr(settings, "max_check_cells", 100)
    assert _run(client, "cells", _table(10, columns=10)).status_code == 200  # 100 cells

    over = _run(client, "cells-over", _table(11, columns=10))  # 110 cells
    assert over.status_code == 413
    assert "too big to check in one go" in _detail(over)
    assert "with 10 columns it can have up to 10 rows (100 cells in all)" in _detail(over)


def test_a_first_line_that_cannot_be_a_header_is_refused(client):
    resp = _run(client, "oneline", b"a" * 70_000)
    assert resp.status_code == 413
    assert "isn't a CSV header row" in _detail(resp)


def test_only_the_rows_up_to_the_limit_are_ever_parsed():
    """A file that is too long is turned away without parsing the rest, so a broken tail can't change the answer."""
    good = _table(6)
    broken_tail = b'"never closed\n' * 3
    with pytest.raises(HTTPException) as exc:
        limits.read_csv_limited(good + broken_tail, "f.csv", max_rows=5)
    assert exc.value.status_code == 413


def test_a_file_that_is_not_a_csv_is_still_a_plain_400(client):
    resp = _run(client, "empty", b"")
    assert resp.status_code == 400
    assert _detail(resp).startswith("Could not parse CSV")


def test_a_big_file_inside_the_limits_still_works(client):
    """Handling big files is the point: a 20,000-row, 8-column file (about 1 MB) is checked in one go."""
    resp = _run(client, "big-but-fine", _table(20_000, columns=8))
    assert resp.status_code == 200, resp.text
    assert resp.json()["row_count"] == 20_000


def test_a_file_where_every_row_fails_two_checks_keeps_every_flagged_row(client):
    """The worst case for memory: thousands of flagged rows. All of them are saved - in batches - and can be listed."""
    rows = [f"R-{i:05d},{'' if i % 2 else 'x'},{i}" for i in range(3_000)]
    csv_text = "id,note,n\n" + "\n".join(rows + rows) + "\n"  # every row twice: duplicates, and half have a blank

    resp = _run(client, "worst", csv_text.encode())
    assert resp.status_code == 200, resp.text
    run = resp.json()

    for check in ("duplicates", "missing_values"):
        flagged = run["breakdown"][check]["affected_rows"]
        assert flagged >= 3_000
        issues = client.get(f"/datasets/worst/runs/{run['id']}/issues/{check}?limit=100000").json()
        assert issues["count"] == flagged == len(issues["row_keys"])


# ---------------------------------------------------------------- related files


def test_a_check_takes_its_related_files_up_to_the_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "max_reference_files", 2)
    related = [("reference_files", (f"r{i}.csv", _table(5), "text/csv")) for i in range(3)]

    assert _run(client, "refs", _table(5), extra=related[:2]).status_code == 200

    over = _run(client, "refs-over", _table(5), extra=related)
    assert over.status_code == 413
    assert "3 related files is more than one check takes - the limit is 2" in _detail(over)


def test_a_related_file_is_held_to_the_same_size_limits(client, monkeypatch):
    monkeypatch.setattr(settings, "max_check_rows", 20)
    resp = _run(client, "refs-big", _table(5), extra=[("reference_files", ("customers.csv", _table(21), "text/csv"))])
    assert resp.status_code == 413
    assert "'customers.csv' has more than 20 rows" in _detail(resp)


def test_a_broken_related_file_is_named(client):
    resp = _run(client, "refs-bad", _table(5), extra=[("reference_files", ("ref.csv", b"", "text/csv"))])
    assert resp.status_code == 400
    assert "Could not parse reference file 'ref.csv'" in _detail(resp)


# ---------------------------------------------------------------- incident uploads


def test_the_incident_forms_have_a_column_limit_too(client, monkeypatch):
    monkeypatch.setattr(settings, "max_check_columns", 5)
    wide_logs = b"timestamp,service,level,message,a,b\n2026-01-01T00:00:00Z,api,INFO,hi,1,2\n"
    wide_metrics = b"timestamp,service,metric_name,value,a,b\n2026-01-01T00:00:00Z,api,cpu,1,1,2\n"
    for path, body in (("logs", wide_logs), ("metrics", wide_metrics)):
        resp = client.post(f"/incidents/{path}/upload", files={"file": (f"{path}.csv", body, "text/csv")})
        assert resp.status_code == 413, path
        assert "has 6 columns" in _detail(resp), path


def _logs(rows: int) -> bytes:
    lines = ["timestamp,service,level,message"]
    lines += [f"2026-01-01T00:{i // 60:02d}:{i % 60:02d}Z,api,INFO,ok {i}" for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


def test_incident_uploads_have_a_row_limit_that_suggests_a_shorter_window(client, monkeypatch):
    monkeypatch.setattr(settings, "max_incident_rows", 5)
    ok = client.post("/incidents/logs/upload", files={"file": ("logs.csv", _logs(5), "text/csv")})
    assert ok.status_code == 200, ok.text
    assert ok.json()["rows_ingested"] == 5

    over = client.post("/incidents/logs/upload", files={"file": ("logs.csv", _logs(6), "text/csv")})
    assert over.status_code == 413
    assert "more than 5 rows" in _detail(over)
    assert "shorter time window" in _detail(over)

    metrics = b"timestamp,service,metric_name,value\n" + b"".join(
        f"2026-01-01T00:00:{i:02d}Z,api,cpu,{i}\n".encode() for i in range(6)
    )
    assert client.post("/incidents/metrics/upload", files={"file": ("m.csv", metrics, "text/csv")}).status_code == 413


# ---------------------------------------------------------------- documents


@pytest.mark.parametrize("words", [0, 1, 149, 150, 151, 269, 270, 271, 390, 391, 1000, 5000])
def test_counting_chunks_agrees_with_actually_chunking(words):
    text = " ".join(f"w{i}" for i in range(words))
    assert count_chunks(text) == len(chunk_text(text))


def test_a_document_too_long_to_index_is_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "max_document_chunks", 2)
    two_chunks = " ".join(f"word{i}" for i in range(270)).encode()  # 270 words = 2 chunks
    three_chunks = " ".join(f"word{i}" for i in range(271)).encode()  # one more word makes a third

    assert _document(client, "fits.txt", two_chunks).status_code == 200
    over = _document(client, "toolong.txt", three_chunks)
    assert over.status_code == 413
    assert "too long to index for search" in _detail(over)
    assert "about 240 words" in _detail(over)  # 2 chunks of 120 new words each


def test_only_text_that_is_embedded_is_held_to_the_length_limit(monkeypatch):
    """A check's data file is kept as plain text, without embeddings, so a long table is fine."""
    monkeypatch.setattr(settings, "max_document_chunks", 1)
    long_table = "a,b\n" + "alpha,beta gamma delta epsilon\n" * 200
    db = SessionLocal()
    try:
        saved = store_document(db, "data.csv", "csv", long_table, "public", embed=False)
        assert saved.char_count == len(long_table)
        with pytest.raises(HTTPException) as exc:
            store_document(db, "searchable.csv", "csv", long_table, "public")
        assert exc.value.status_code == 413
    finally:
        db.close()


def test_a_csv_document_is_checked_for_length_before_it_is_embedded(client, monkeypatch):
    monkeypatch.setattr(settings, "max_document_chunks", 1)
    long_csv = ("a,b\n" + "alpha,beta gamma delta epsilon\n" * 200).encode()
    resp = _document(client, "table.csv", long_csv)
    assert resp.status_code == 413


def test_word_files_that_unpack_to_far_more_than_their_size_are_refused(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)  # everything in a Word file may unpack to 2 MB
    bomb = _unzips_to(6, "word/media/scan.bin")
    assert len(bomb) < 100 * 1024  # a few KB on the wire

    with pytest.raises(FileTooLarge, match="unpacks to 6 MB, more than the 2 MB"):
        extract_text("bomb.docx", bomb)

    resp = _document(client, "bomb.docx", bomb)
    assert resp.status_code == 413
    assert "unpacks to 6 MB" in _detail(resp)
    assert "more than the 2 MB" in _detail(resp)

    # one byte-count up is still over; a 1 MB unpack is well within what is tolerated:
    # refused as "not a Word file", not as too big
    assert _document(client, "bigger.docx", _unzips_to(3, "word/media/scan.bin")).status_code == 413
    resp = _document(client, "odd.docx", _unzips_to(1, "word/media/scan.bin"))
    assert resp.status_code == 400


def test_the_unpacked_size_is_the_total_and_exactly_the_limit_is_tolerated(client, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)  # everything in a Word file may unpack to 2 MB

    # six parts of 1 MB: no part is big, but together they are 6 MB
    resp = _document(client, "parts.docx", _unzips_in_parts(6, 1))
    assert resp.status_code == 413
    assert "unpacks to 6 MB" in _detail(resp)

    # exactly 2 MB is within the limit: refused as "not a Word file", not as too big
    assert _document(client, "exact.docx", _unzips_in_parts(2, 1)).status_code == 400
    assert _document(client, "exact-one.docx", _unzips_to(2, "word/media/scan.bin")).status_code == 400


def test_the_text_inside_a_word_file_gets_a_much_smaller_allowance_than_its_pictures(client, monkeypatch):
    """
    Pictures cost about their size to open; the text parts are built into a tree
    that costs about ten times theirs. So 10 MB of XML is the most a Word file
    may hold (or the upload limit, if that is lower), however much room the whole
    file has.
    """
    monkeypatch.setattr(settings, "max_upload_mb", 25)  # room for 50 MB in all

    # 11 MB of text: well inside the 50 MB total, but over what the text may be
    for part in ("word/document.xml", "word/_rels/document.xml.rels", "customXml/item1.XML"):
        with pytest.raises(FileTooLarge, match="text inside this Word file unpacks to 11 MB, more than the 10 MB"):
            extract_text("long.docx", _unzips_to(11, part))
    resp = _document(client, "long.docx", _unzips_to(11))
    assert resp.status_code == 413
    assert "text inside this Word file" in _detail(resp)
    assert "more than the 10 MB" in _detail(resp)

    # the text parts are added together, not judged one by one
    assert _document(client, "many.docx", _unzips_in_parts(11, 1, "word/part{i}.xml")).status_code == 413

    # exactly 10 MB of text is tolerated; so are 40 MB of pictures next to it
    assert _document(client, "exact.docx", _unzips_to(10)).status_code == 400
    mixed = io.BytesIO()
    with zipfile.ZipFile(mixed, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", b"0" * (10 * MB))
        for i in range(4):
            z.writestr(f"word/media/photo{i}.png", b"0" * (10 * MB))
    assert _document(client, "mixed.docx", mixed.getvalue()).status_code == 400  # 50 MB in all: exactly the limit


def test_the_text_allowance_is_never_more_than_the_upload_limit(monkeypatch):
    monkeypatch.setattr(settings, "max_upload_mb", 1)  # so 1 MB of text, though 10 MB is the ceiling
    with pytest.raises(FileTooLarge, match="unpacks to 2 MB, more than the 1 MB"):
        extract_text("a.docx", _unzips_to(2))
    # not refused for size: python-docx says what is wrong with a file that has no Word parts
    with pytest.raises(Exception) as exc:
        extract_text("a.docx", _unzips_to(1))
    assert not isinstance(exc.value, FileTooLarge)


def test_an_ordinary_word_file_is_unaffected(client):
    resp = _document(client, "report.docx", _docx(["First paragraph.", "Second one."]))
    assert resp.status_code == 200
    assert resp.json()["filename"] == "report.docx"


def test_a_word_file_with_more_text_than_could_be_indexed_is_stopped_while_reading(monkeypatch):
    monkeypatch.setattr(settings, "max_document_chunks", 1)  # room for about 2,000 characters
    extract_text("short.docx", _docx(["x" * 1_500]))
    with pytest.raises(TextTooLong):
        extract_text("long.docx", _docx(["x" * 1_000 for _ in range(3)]))


def test_a_pdf_with_more_text_than_could_be_indexed_is_stopped_while_reading(monkeypatch):
    monkeypatch.setattr(settings, "max_document_chunks", 1)
    _, text = extract_text("one.pdf", _pdf(["hello from the first page"]))
    assert "hello from the first page" in text
    with pytest.raises(TextTooLong):
        extract_text("long.pdf", _pdf(["x" * 1_000, "y" * 1_000, "z" * 1_000]))


def test_a_word_or_pdf_file_with_too_much_text_gets_the_plain_answer_from_the_route(client, monkeypatch):
    monkeypatch.setattr(settings, "max_document_chunks", 1)  # room for about 2,000 characters
    for name, content in (
        ("long.docx", _docx(["x" * 1_000 for _ in range(3)])),
        ("long.pdf", _pdf(["x" * 1_000, "y" * 1_000, "z" * 1_000])),
    ):
        resp = _document(client, name, content)
        assert resp.status_code == 413, name
        assert "too long to index for search" in _detail(resp), name


# ---------------------------------------------------------------- saving flagged rows


def test_flagged_rows_go_into_the_database_in_batches():
    class Spy:
        def __init__(self):
            self.sizes = []

        def execute(self, statement, batch):
            self.sizes.append(len(batch))

    spy = Spy()
    rows = ({"run_id": 1, "check_name": "duplicates", "row_key": str(i)} for i in range(12_345))  # a generator, not a list
    assert insert_in_batches(spy, models.CheckIssue, rows) == 12_345
    assert spy.sizes == [5_000, 5_000, 2_345]

    assert insert_in_batches(Spy(), models.CheckIssue, iter(())) == 0


def test_batched_inserts_really_land_in_the_table():
    db = SessionLocal()
    try:
        dataset = models.Dataset(name="batches")
        db.add(dataset)
        db.commit()
        run = models.CheckRun(dataset_id=dataset.id, score=90.0, breakdown={}, row_count=7, schema_snapshot={}, distribution_snapshot={})
        db.add(run)
        db.commit()

        rows = [{"run_id": run.id, "check_name": "missing_values", "row_key": f"k{i}"} for i in range(23)]
        assert insert_in_batches(db, models.CheckIssue, rows, batch_size=10) == 23
        db.commit()
        assert db.query(models.CheckIssue).filter_by(run_id=run.id).count() == 23
    finally:
        db.close()


# ---------------------------------------------------------------- a few at a time


def test_when_every_place_is_taken_the_server_says_it_is_busy(client, monkeypatch):
    metrics = b"timestamp,service,metric_name,value\n2026-01-01T00:00:00Z,api,cpu,1\n2026-01-01T00:00:01Z,api,cpu,2\n"
    uploads = {
        "checks": lambda: _run(client, "busy", _table(5)),
        "documents": lambda: _document(client, "busy.txt", b"hello there"),
        "logs": lambda: client.post("/incidents/logs/upload", files={"file": ("l.csv", _logs(2), "text/csv")}),
        "metrics": lambda: client.post("/incidents/metrics/upload", files={"file": ("m.csv", metrics, "text/csv")}),
    }
    taken = threading.BoundedSemaphore(1)
    taken.acquire()  # somebody else's upload is being worked on
    monkeypatch.setattr(limits, "_slots", taken)
    monkeypatch.setattr(limits, "_SLOT_WAIT_SECONDS", 0.05)

    for name, upload in uploads.items():
        resp = upload()
        assert resp.status_code == 503, name
        assert resp.headers["Retry-After"] == "10", name
        assert "busy" in _detail(resp), name

    taken.release()
    for name, upload in uploads.items():
        assert upload().status_code == 200, name


def test_a_second_upload_waits_for_its_turn_instead_of_being_turned_away(client, monkeypatch):
    place = threading.BoundedSemaphore(1)
    place.acquire()  # another upload is being worked on...
    monkeypatch.setattr(limits, "_slots", place)
    monkeypatch.setattr(limits, "_SLOT_WAIT_SECONDS", 10)
    threading.Timer(0.5, place.release).start()  # ...and finishes half a second later

    started = time.monotonic()
    resp = _run(client, "waits", _table(5))
    assert resp.status_code == 200, resp.text
    assert time.monotonic() - started >= 0.4  # it really waited, rather than getting in at once


def test_the_place_is_given_back_whether_the_upload_works_or_not(client, monkeypatch):
    monkeypatch.setattr(limits, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(limits, "_SLOT_WAIT_SECONDS", 0.2)

    for content, expected in [(_table(5), 200), (b"", 400), (b"a" * 70_000, 413), (_table(5), 200), (b"", 400)]:
        assert _run(client, "reuse", content).status_code == expected  # a leaked place would make these 503
