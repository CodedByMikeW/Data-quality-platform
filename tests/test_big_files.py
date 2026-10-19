"""
Handling files up to 25 MB without a bigger file meaning a bigger risk.

Raising the size limit is only safe if what a big file costs stays in check, so
these tests hold the pieces that do that:

- the defaults are the numbers the page, the README and .env.example promise;
- a related file is read for the one column a relationship rule looks it up by;
- the checks don't make a second copy of the data they are checking;
- Knowledge Search reads a document's text only where it needs it (listing,
  history, versioning and searching never load a saved data file whole), and
  gives the same answers as the version that did;
- the CSV viewer pages through a document without copying its text;
- saving a check's rows takes its turn like a check does.

Nothing here makes a 25 MB file - the checks run on small ones against limits
lowered to match (see the other upload-limit tests). The real 25 MB runs are
measured against a live server (see the README's memory paragraph).
"""
import csv
import io
import os
import random
import re
import threading
import zipfile
from pathlib import Path

import pandas as pd
import pytest
from fastapi import HTTPException
from sqlalchemy import event, inspect

from app import limits, models
from app.config import Settings, settings
from app.database import SessionLocal, engine
from app.document_extraction import FileTooLarge, TextTooLong, extract_text
from app.document_store import store_document
from app.pipeline import compute_distribution_snapshot, run_all_checks
from app.quality_report import data_filename
from app.checks.schema_changes import schema_of
from app.routers import documents as documents_router
from app.routers import runs as runs_router

MB = 1024 * 1024
STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    stub = lambda texts: [[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for _ in texts]  # noqa: E731
    monkeypatch.setattr("app.document_store.embed_texts", stub)
    monkeypatch.setattr("app.routers.documents.embed_texts", stub)


@pytest.fixture
def statements():
    """Every SQL statement the database is asked to run while the test does its thing, as (sql, parameters)."""
    seen: list[tuple[str, object]] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        seen.append((statement, parameters))

    event.listen(engine, "before_cursor_execute", record)
    yield seen
    event.remove(engine, "before_cursor_execute", record)


def _selected_columns(statement: str) -> str:
    """What a SELECT asks for: the part before FROM (a WHERE clause may mention a column without reading it)."""
    return re.split(r"\s+FROM\s+", statement, maxsplit=1)[0]


def _reads_document_text(statements: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """The statements that fetch a document's text (not ones that only look inside it, like LIKE in a WHERE)."""
    return [
        (sql, params)
        for sql, params in statements
        if sql.lstrip().startswith("SELECT") and "documents.text_content" in _selected_columns(sql)
    ]


def _table(rows: int, columns: int = 3) -> bytes:
    lines = [",".join(["id"] + [f"c{i}" for i in range(1, columns)])]
    for r in range(rows):
        lines.append(",".join([f"R-{r:06d}"] + [str((r + 1) * (c + 1) % 97) for c in range(1, columns)]))
    return ("\n".join(lines) + "\n").encode()


def _post_run(client, dataset, content, extra=(), relationships=None):
    files = [("file", ("data.csv", content, "text/csv")), *extra]
    data = {"relationships": relationships} if relationships is not None else None
    return client.post(f"/datasets/{dataset}/runs", files=files, data=data)


def _upload(client, filename, content, level="public"):
    data = content.encode("utf-8") if isinstance(content, str) else content
    resp = client.post(
        "/documents", files={"file": (filename, io.BytesIO(data), "application/octet-stream")}, data={"access_level": level}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


# ---------------------------------------------------------------- the numbers


def _default_settings(monkeypatch) -> Settings:
    """The settings as they are with nothing in the environment and no .env file: what a fresh clone runs with."""
    for name in list(os.environ):
        if name.lower() in Settings.model_fields:
            monkeypatch.delenv(name)
    return Settings(_env_file=None)


def test_the_defaults_are_the_numbers_the_readme_promises(monkeypatch):
    fresh = _default_settings(monkeypatch)
    assert fresh.max_upload_mb == 25
    assert fresh.max_upload_bytes == 25 * MB
    assert fresh.max_check_rows == 1_000_000
    assert fresh.max_check_columns == 100
    assert fresh.max_check_cells == 6_000_000
    assert fresh.max_reference_files == 3
    assert fresh.max_incident_rows == 25_000
    assert fresh.max_document_chunks == 1_000
    assert fresh.max_concurrent_uploads == 2


def test_an_ordinary_25_mb_file_fits_inside_the_defaults(monkeypatch):
    """About 100+ bytes a row (a dozen ordinary columns) is about 250,000 rows, and rows x columns is under the cell cap."""
    fresh = _default_settings(monkeypatch)
    rows = fresh.max_upload_bytes // 105
    assert rows <= fresh.max_check_rows
    assert rows * 12 <= fresh.max_check_cells


def test_a_25_mb_file_of_very_short_rows_fits_the_defaults_too(monkeypatch):
    """27-byte rows (an id, a short value, a flag) put about 970,000 of them in 25 MB - measured at a million: +196 MB, 22 s."""
    fresh = _default_settings(monkeypatch)
    rows = fresh.max_upload_bytes // 27
    assert rows <= fresh.max_check_rows
    assert rows * 4 <= fresh.max_check_cells


def test_a_25_mb_file_of_forty_number_columns_fits_the_defaults_too(monkeypatch):
    """Forty columns of 4-digit numbers (200 bytes a row) is about 131,000 rows x 40 = 5.2 million cells - measured: +97 MB."""
    fresh = _default_settings(monkeypatch)
    rows = fresh.max_upload_bytes // 200
    assert rows <= fresh.max_check_rows
    assert rows * 40 <= fresh.max_check_cells


def test_the_page_says_the_same_numbers_the_server_enforces(monkeypatch):
    """The page shows the limit before the server has answered GET /limits, and decides which data file to attach."""
    fresh = _default_settings(monkeypatch)
    js = (STATIC / "app.js").read_text()
    html = (STATIC / "index.html").read_text()

    fallback = f"max_upload_bytes: {fresh.max_upload_mb} * 1024 * 1024, max_upload_mb: {fresh.max_upload_mb}, max_check_rows: {fresh.max_check_rows}"
    assert fallback in js
    assert f"CSV files up to {fresh.max_upload_mb} MB or {fresh.max_check_rows:,} rows." in html

    saved_rows = runs_router._MAX_SAVED_DATA_BYTES
    assert saved_rows % MB == 0
    assert f"const MAX_SAVED_DATA_BYTES = {saved_rows // MB} * 1024 * 1024;" in js


def test_saved_rows_may_be_as_big_as_a_checked_file(monkeypatch):
    assert runs_router._MAX_SAVED_DATA_BYTES >= _default_settings(monkeypatch).max_upload_bytes


# ---------------------------------------------------------------- reading only the columns a file is used for


def test_only_the_asked_for_columns_are_read():
    contents = _table(50, columns=8)
    full = limits.read_csv_limited(contents, "f.csv", max_rows=100)
    thin = limits.read_csv_limited(contents, "f.csv", max_rows=100, columns={"c3", "id"})

    assert list(thin.columns) == ["id", "c3"]  # the file's order, whatever the order asked for
    assert thin.equals(full[["id", "c3"]])  # same values, same types


def test_asking_for_all_the_columns_is_the_same_as_asking_for_none_in_particular():
    contents = _table(20, columns=4)
    everything = limits.read_csv_limited(contents, "f.csv", max_rows=100)
    named = limits.read_csv_limited(contents, "f.csv", max_rows=100, columns={"id", "c1", "c2", "c3"})
    assert named.equals(everything)


def test_a_column_is_found_by_position_so_a_repeated_name_cannot_confuse_it():
    contents = b"id,id,x\n1,9,a\n2,8,b\n"
    first = limits.read_csv_limited(contents, "f.csv", max_rows=10, columns={"id"})
    assert first.columns.tolist() == ["id"] and first["id"].tolist() == [1, 2]

    second = limits.read_csv_limited(contents, "f.csv", max_rows=10, columns={"id.1"})  # how pandas names the second
    assert second.columns.tolist() == ["id.1"] and second["id.1"].tolist() == [9, 8]


def test_the_cell_limit_counts_what_is_read_not_what_is_skipped():
    contents = _table(100, columns=10)  # 1,000 cells in all
    with pytest.raises(HTTPException) as exc:
        limits.read_csv_limited(contents, "f.csv", max_rows=1_000, max_cells=500)
    assert exc.value.status_code == 413

    thin = limits.read_csv_limited(contents, "f.csv", max_rows=1_000, max_cells=500, columns={"c1"})  # 100 cells
    assert len(thin) == 100 and thin.columns.tolist() == ["c1"]

    with pytest.raises(HTTPException) as exc:  # one column, but 501 rows of it
        limits.read_csv_limited(_table(501, columns=10), "f.csv", max_rows=1_000, max_cells=500, columns={"c1"})
    assert exc.value.status_code == 413
    assert "with 1 columns it can have up to 500 rows" in exc.value.detail


def test_the_column_limit_still_counts_every_column_in_the_file():
    with pytest.raises(HTTPException) as exc:
        limits.read_csv_limited(_table(3, columns=6), "f.csv", max_rows=10, max_columns=5, columns={"id"})
    assert exc.value.status_code == 413
    assert "has 6 columns - a check handles up to 5" in exc.value.detail


def test_a_file_with_none_of_the_asked_for_columns_still_has_its_rows_counted():
    """Reading nothing would make a 10-million-row file look empty, and the row limit would never fire."""
    for columns in (set(), {"not_there"}):
        df = limits.read_csv_limited(_table(20), "f.csv", max_rows=20, columns=columns)
        assert df.columns.tolist() == ["id"] and len(df) == 20  # the first column stands in

        with pytest.raises(HTTPException) as exc:
            limits.read_csv_limited(_table(21), "f.csv", max_rows=20, columns=columns)
        assert exc.value.status_code == 413
        assert "has more than 20 rows" in exc.value.detail


def test_columns_are_found_by_their_exact_names_capitals_and_spaces_included():
    contents = b"Customer ID,E-mail Address,notes\nC-1,a@x.com,hello\nC-2,b@x.com,bye\n"
    got = limits.read_csv_limited(contents, "f.csv", max_rows=10, columns={"E-mail Address"})
    assert got.columns.tolist() == ["E-mail Address"] and got["E-mail Address"].tolist() == ["a@x.com", "b@x.com"]

    # names are not matched loosely: a different case is a different column, so the first stands in
    other = limits.read_csv_limited(contents, "f.csv", max_rows=10, columns={"e-mail address"})
    assert other.columns.tolist() == ["Customer ID"]


def test_a_relationship_rule_finds_a_related_files_column_by_its_exact_name(client, monkeypatch):
    seen = _spy_on_the_checks(monkeypatch)
    related = b"Customer ID,Region\nR-000000,north\nR-000001,south\n"
    rule = '[{"column": "id", "reference_index": 0, "reference_column": "Customer ID"}]'
    resp = _post_run(client, "mixed-case", _table(4), extra=[("reference_files", ("ref.csv", related, "text/csv"))], relationships=rule)
    assert resp.status_code == 200, resp.text
    assert seen["reference_datasets"][0][1].columns.tolist() == ["Customer ID"]
    assert resp.json()["breakdown"]["broken_relationships"]["affected_rows"] == 2  # R-000002 and R-000003 aren't in it


def test_asking_for_columns_does_not_change_which_files_are_not_csvs():
    for contents in (b"", b'"never closed\n'):
        with pytest.raises(HTTPException) as exc:
            limits.read_csv_limited(contents, "f.csv", max_rows=10, columns={"id"})
        assert exc.value.status_code == 400


@pytest.mark.parametrize(
    "rules, n_files, expected",
    [
        ([{"column": "a", "reference_index": 0, "reference_column": "id"}], 1, [{"id"}]),
        (
            [
                {"column": "a", "reference_index": 0, "reference_column": "id"},
                {"column": "b", "reference_index": 0, "reference_column": "code"},
                {"column": "c", "reference_index": 1, "reference_column": "sku"},
            ],
            3,
            [{"id", "code"}, {"sku"}, set()],
        ),
        ([{"column": "a", "reference_index": -1, "reference_column": "id"}], 2, [set(), {"id"}]),  # counted from the end
        ([{"column": "a", "reference_index": 1, "reference_column": "id"}], 1, [set()]),  # no such file: left for the checks to say
        ([{"column": "a", "reference_index": -2, "reference_column": "id"}], 1, [set()]),
        ([{"column": "a", "reference_index": True, "reference_column": "id"}], 2, [set(), {"id"}]),  # True is 1 when the checks pick the file
        ([{"column": "a", "reference_index": 0, "reference_column": 7}], 1, [set()]),
        ([{"column": "a", "reference_index": "0", "reference_column": "id"}], 1, [set()]),
        ([{"column": "a", "reference_column": "id"}, {"reference_index": 0}, "nope", 3, None], 1, [set()]),
        ({"column": "a", "reference_index": 0, "reference_column": "id"}, 1, [set()]),  # not a list of rules at all
        (None, 2, [set(), set()]),
        ([], 2, [set(), set()]),
    ],
)
def test_each_related_file_is_read_for_the_columns_the_rules_use(rules, n_files, expected):
    assert runs_router._reference_columns_wanted(rules, n_files) == expected


def _related_file(rows: int, key_start: int = 0, columns: int = 12) -> bytes:
    """A related file with `columns` columns whose first is `id` (R-000000...) - starting at key_start."""
    header = ",".join(["id"] + [f"extra_{i}" for i in range(1, columns)])
    body = "".join(
        ",".join([f"R-{key_start + r:06d}"] + [f"value {r} {c}" for c in range(1, columns)]) + "\n" for r in range(rows)
    )
    return (header + "\n" + body).encode()


def _spy_on_the_checks(monkeypatch) -> dict:
    seen = {}
    real = runs_router.run_all_checks

    def spy(df, *args, **kwargs):
        seen["reference_datasets"] = kwargs.get("reference_datasets")
        return real(df, *args, **kwargs)

    monkeypatch.setattr(runs_router, "run_all_checks", spy)
    return seen


def test_a_run_reads_only_the_key_column_of_a_related_file_and_gets_the_same_answer(client, monkeypatch):
    seen = _spy_on_the_checks(monkeypatch)
    related = _related_file(rows=3)  # R-000000 .. R-000002 - the main file's R-000003 and R-000004 aren't in it
    rule = '[{"column": "id", "reference_index": 0, "reference_column": "id"}]'

    resp = _post_run(client, "lookups", _table(5), extra=[("reference_files", ("ref.csv", related, "text/csv"))], relationships=rule)
    assert resp.status_code == 200, resp.text
    run = resp.json()

    label, frame = seen["reference_datasets"][0]
    assert label == "ref.csv"
    assert frame.columns.tolist() == ["id"]  # not the other eleven
    assert len(frame) == 3

    assert run["breakdown"]["broken_relationships"]["affected_rows"] == 2
    assert run["relationship_feedback"][0]["status"] == "applied"
    issues = client.get(f"/datasets/lookups/runs/{run['id']}/issues/broken_relationships").json()
    assert sorted(issues["row_keys"]) == ["R-000003", "R-000004"]


def test_the_answer_with_a_wide_related_file_is_the_same_as_with_a_narrow_one(client):
    rule = '[{"column": "id", "reference_index": 0, "reference_column": "id"}]'

    def breakdown(dataset, related):
        resp = _post_run(client, dataset, _table(30), extra=[("reference_files", ("ref.csv", related, "text/csv"))], relationships=rule)
        assert resp.status_code == 200, resp.text
        return resp.json()["breakdown"]["broken_relationships"]

    narrow = breakdown("narrow", _related_file(rows=20, columns=1))
    wide = breakdown("wide", _related_file(rows=20, columns=40))
    assert narrow == wide
    assert narrow["affected_rows"] == 10


def test_a_related_file_nothing_looks_up_costs_one_column(client, monkeypatch):
    seen = _spy_on_the_checks(monkeypatch)
    resp = _post_run(client, "unused", _table(5), extra=[("reference_files", ("ref.csv", _related_file(rows=4), "text/csv"))])
    assert resp.status_code == 200, resp.text
    assert seen["reference_datasets"][0][1].columns.tolist() == ["id"]


def test_a_rule_that_names_a_column_the_related_file_lacks_is_still_reported(client):
    rule = '[{"column": "id", "reference_index": 0, "reference_column": "nope"}]'
    resp = _post_run(client, "typo", _table(5), extra=[("reference_files", ("ref.csv", _related_file(rows=4), "text/csv"))], relationships=rule)
    assert resp.status_code == 200, resp.text
    feedback = resp.json()["relationship_feedback"][0]
    assert feedback["status"] == "skipped"
    assert feedback["reason"] == "column 'nope' not found in 'ref.csv'"


def test_several_related_files_are_each_read_for_their_own_column(client, monkeypatch):
    seen = _spy_on_the_checks(monkeypatch)
    first = b"code,other\nR-000000,x\nR-000001,y\n"
    second = b"padding,id\nz,R-000002\nz,R-000003\n"
    rules = (
        '[{"column": "id", "reference_index": 0, "reference_column": "code"},'
        ' {"column": "id", "reference_index": 1, "reference_column": "id"}]'
    )
    extra = [("reference_files", ("a.csv", first, "text/csv")), ("reference_files", ("b.csv", second, "text/csv"))]
    resp = _post_run(client, "two", _table(4), extra=extra, relationships=rules)
    assert resp.status_code == 200, resp.text
    frames = [frame for _, frame in seen["reference_datasets"]]
    assert [f.columns.tolist() for f in frames] == [["code"], ["id"]]
    assert [f["code" if "code" in f else "id"].tolist() for f in frames] == [["R-000000", "R-000001"], ["R-000002", "R-000003"]]


def test_a_related_file_over_the_column_limit_is_refused_even_though_few_columns_are_read(client, monkeypatch):
    monkeypatch.setattr(settings, "max_check_columns", 5)
    rule = '[{"column": "id", "reference_index": 0, "reference_column": "id"}]'
    resp = _post_run(client, "wide-ref", _table(5, columns=3), extra=[("reference_files", ("ref.csv", _related_file(3, columns=6), "text/csv"))], relationships=rule)
    assert resp.status_code == 413
    assert "'ref.csv' has 6 columns - a check handles up to 5" in resp.json()["detail"]


# ---------------------------------------------------------------- the checks don't copy the data


def _messy_frame(rows: int = 300) -> pd.DataFrame:
    """Duplicates, blanks, bad formats, outliers: every check has something to find."""
    rng = random.Random(7)
    data = {
        "customer_id": [f"CUST-{i:06d}" for i in range(rows)],
        "name": [f"Person {i % 40}" for i in range(rows)],
        "email": [f"person{i}@x.com" if i % 9 else "not-an-email" for i in range(rows)],
        "status": [rng.choice(["active", "paused", "closed"]) if i % 50 else "zzz" for i in range(rows)],
        "amount": [round(rng.uniform(10, 100), 2) if i % 60 else 9999.0 for i in range(rows)],
    }
    df = pd.DataFrame(data)
    df.loc[5, "email"] = None
    return pd.concat([df, df.iloc[:6]], ignore_index=True)  # six duplicated rows


def test_the_checks_leave_the_data_they_are_given_exactly_as_it_was():
    """The checks work on a view of the frame, not a copy - so none of them may write into it."""
    before = _messy_frame()
    earlier = _messy_frame(200)
    df = before.copy(deep=True)

    results = run_all_checks(
        df,
        previous_schema=schema_of(earlier),
        previous_distributions=compute_distribution_snapshot(earlier),
        reference_datasets=[("ref.csv", pd.DataFrame({"id": ["CUST-000001", "CUST-000002"]}))],
        relationship_rules=[{"column": "customer_id", "reference_index": 0, "reference_column": "id"}],
        relationship_feedback=[],
    )
    compute_distribution_snapshot(df)

    pd.testing.assert_frame_equal(df, before)
    assert df.columns.tolist() == before.columns.tolist()  # no helper column left behind
    assert {r.check_name for r in results} >= {"missing_values", "duplicates", "invalid_formats", "outliers", "schema_changes"}


def test_a_file_with_no_key_column_gets_its_row_numbers_without_touching_the_frame():
    df = pd.DataFrame({"a": ["x", "y", "x", "x"], "b": [1.0, None, 3.0, 1.0]})
    before = df.copy(deep=True)
    results = run_all_checks(df)
    pd.testing.assert_frame_equal(df, before)
    assert "_row_number" not in df.columns
    missing = next(r for r in results if r.check_name == "missing_values")
    assert missing.affected_row_keys == ["2"]  # the blank in the second row, named by its 1-based row number


# ---------------------------------------------------------------- Knowledge Search reads text only where it needs it


def test_listing_documents_does_not_read_their_text(client, statements):
    _upload(client, "a.txt", "alpha text " * 50)
    _upload(client, "b.csv", "id,name\n1,Ada\n", level="team")
    statements.clear()

    listed = client.get("/documents", params={"role": "admin"}).json()
    assert [d["filename"] for d in listed] == ["b.csv", "a.txt"]
    assert listed[0]["char_count"] == len("id,name\n1,Ada")
    assert _reads_document_text(statements) == []


def test_a_documents_history_does_not_read_any_version_text(client, statements):
    first = _upload(client, "notes.txt", "version one")
    second = _upload(client, "notes.txt", "version two, a bit longer")
    statements.clear()

    history = client.get(f"/documents/{second}/history", params={"role": "admin"}).json()
    assert [(h["id"], h["version"], h["is_latest"]) for h in history] == [(second, 2, True), (first, 1, False)]
    assert _reads_document_text(statements) == []


def test_opening_a_document_and_its_table_still_returns_its_text(client):
    doc_id = _upload(client, "people.csv", "id,name\n1,Ada\n2,Grace\n")
    detail = client.get(f"/documents/{doc_id}", params={"role": "admin"}).json()
    assert detail["text_content"] == "id,name\n1,Ada\n2,Grace"
    assert client.get(f"/documents/{doc_id}/table", params={"role": "admin"}).json()["total_rows"] == 2


def test_storing_a_new_version_does_not_read_the_old_versions_text_or_its_own_back(statements):
    db = SessionLocal()
    try:
        store_document(db, "data.csv", "csv", "a,b\n" + "1,2\n" * 100, "public", embed=False)
        statements.clear()
        second = store_document(db, "data.csv", "csv", "a,b\n" + "3,4\n" * 100, "public", embed=False)

        assert _reads_document_text(statements) == []
        assert second.version == 2 and second.is_latest is True
        assert second.char_count == len("a,b\n" + "3,4\n" * 100)
        assert second.uploaded_at is not None
        assert "text_content" in inspect(second).unloaded  # not held a second time; read again only if someone asks
        described = {"id", "filename", "content_type", "char_count", "access_level", "version", "is_latest", "uploaded_at"}
        assert not (described & inspect(second).unloaded)  # everything a caller reports on is already there
        assert db.query(models.Document.text_content).filter_by(id=second.id).scalar().startswith("a,b\n3,4")
    finally:
        db.close()


# ---- text that could never be indexed is turned away before it is decoded


def _doc(client, filename, content):
    return client.post("/documents", files={"file": (filename, io.BytesIO(content), "application/octet-stream")})


@pytest.mark.parametrize("name", ["big.txt", "big.csv"])
def test_plain_text_with_more_bytes_than_the_index_could_ever_hold_is_refused_at_once(client, monkeypatch, name):
    monkeypatch.setattr(settings, "max_document_chunks", 1)  # 2,000 characters of budget: 8,000 bytes is the most that could fit
    one_word = lambda n: b"x" * n  # noqa: E731 - a single word is a single chunk, so only its size can turn it away

    assert _doc(client, name, one_word(8_000)).status_code == 200
    over = _doc(client, "over-" + name, one_word(8_001))
    assert over.status_code == 413
    assert "too long to index for search" in over.json()["detail"]


def test_that_early_refusal_never_turns_away_text_the_index_would_have_taken(client, monkeypatch):
    """Measured in bytes it can only be later than the count of characters, never earlier: 1 byte is at most 1 character."""
    monkeypatch.setattr(settings, "max_document_chunks", 2)
    two_chunks = " ".join(f"word{i}" for i in range(270)).encode()  # 270 words = 2 chunks, about 2,000 bytes
    assert len(two_chunks) < 2 * 2_000 * 4
    assert _doc(client, "fits.txt", two_chunks).status_code == 200


def test_a_checks_rows_are_kept_however_long_they_are(client, monkeypatch):
    """Rows saved with a check are never indexed, so the search index's length limit is not theirs."""
    monkeypatch.setattr(settings, "max_document_chunks", 3)  # room for the report; 24,000 bytes is the most text that could be indexed
    rows = "customer_id,name,email\n" + "".join(f"CUST-{i:06d},Person{i},person{i}@x.com\n" for i in range(1, 1_001))
    assert len(rows) > 24_000

    run = client.post("/datasets/customers/runs", files={"file": ("data.csv", io.BytesIO(rows.encode()), "text/csv")}).json()
    resp = client.post(
        f"/datasets/customers/runs/{run['id']}/knowledge-report",
        data={"access_level": "team"},
        files={"data_file": ("data.csv", io.BytesIO(rows.encode()), "text/csv")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data_error"] is None and resp.json()["data"]["created"] is True


def test_extract_text_takes_a_long_table_when_it_is_not_going_to_be_indexed(monkeypatch):
    monkeypatch.setattr(settings, "max_document_chunks", 1)
    long_table = ("a,b\n" + "1,2\n" * 5_000).encode()
    with pytest.raises(TextTooLong):
        extract_text("t.csv", long_table)
    assert extract_text("t.csv", long_table, index=False)[0] == "csv"


def test_a_stored_document_still_reports_everything_a_caller_needs(client):
    resp = client.post("/documents", files={"file": ("n.txt", io.BytesIO(b"hello there"), "text/plain")}, data={"access_level": "team"})
    body = resp.json()
    assert (body["filename"], body["content_type"], body["char_count"]) == ("n.txt", "text", 11)
    assert (body["access_level"], body["version"], body["is_latest"]) == ("team", 1, True)
    assert body["uploaded_at"]


# ---- keyword search: the same answers as before, one document's text at a time


def _old_keyword_matches(db, query, limit):
    """The previous implementation, kept here as the reference: it loaded every matching document whole."""
    from sqlalchemy import or_

    pattern = f"%{query}%"
    candidates = (
        db.query(models.Document)
        .filter(
            models.Document.is_latest.is_(True),
            or_(models.Document.filename.ilike(pattern), models.Document.text_content.ilike(pattern)),
        )
        .all()
    )

    def relevance(doc):
        lower_query = query.lower()
        return doc.filename.lower().count(lower_query) * 5 + doc.text_content.lower().count(lower_query)

    ranked = sorted(candidates, key=relevance, reverse=True)[:limit]
    results = []
    for doc in ranked:
        idx = doc.text_content.lower().find(query.lower())
        if idx == -1:
            window = doc.text_content[: documents_router._SNIPPET_RADIUS * 2]
            snippet = window + ("…" if len(doc.text_content) > len(window) else "")
            citation = documents_router.Citation(None, None, None)
        else:
            snippet = documents_router._snippet_from_index(doc.text_content, idx, len(query))
            citation = documents_router._citation_for_char_index(db, doc.id, idx)
        results.append((doc, snippet, citation))
    return results


_CORPUS = {
    "refund-policy.txt": "Refunds are issued within 14 days. A refund needs the receipt. REFUND requests go to billing.",
    "billing.txt": "Billing runs monthly. Ask billing about a refund. " + "filler words here " * 40 + "refund again at the end.",
    "notes.txt": "Nothing relevant: lunch menus and parking. " * 30,
    "REFUND-form.docx.txt": "A short form.",  # matches by filename only
    "table.csv": "id,item,note\n1,pen,refund\n2,ink,ok\n3,pad,refund\n4,cap,refund\n",
    "tie-a.txt": "one refund here",
    "tie-b.txt": "one refund there",
    "tie-c.txt": "refund (also exactly one)",
    "unicode.txt": "Zażółć gęślą jaźń — grüße, RÉFUND ünïcode İstanbul refund",
    "long.txt": " ".join(f"word{i}" for i in range(400)) + " refund policy at the very end",  # several chunks: the match is in the last
}


def _seed_corpus(client):
    for name, text in _CORPUS.items():
        _upload(client, name, text, level="team" if name in {"billing.txt", "table.csv"} else "public")
    # a second version of one file: only the latest may be searched
    _upload(client, "notes.txt", "Notes, second version: mentions refund once.")


@pytest.mark.parametrize("query", ["refund", "REFUND", "Refund", "billing", "form", "one", "pen,", "grüße", "xyzzy", "e", "İ", ",", "a"])
@pytest.mark.parametrize("limit", [1, 2, 3, 20])
def test_keyword_search_gives_the_same_answers_as_the_version_that_loaded_everything(client, query, limit):
    _seed_corpus(client)
    def describe(results):
        return [(d.id, d.filename, d.access_level, snippet, c.chunk_index, c.char_start, c.char_end) for d, snippet, c in results]

    db = SessionLocal()
    try:
        new = describe(documents_router._keyword_matches(db, query, limit))
        db.expire_all()
        old = describe(_old_keyword_matches(db, query, limit))
    finally:
        db.close()

    assert new == old


def test_a_match_late_in_a_long_document_is_cited_to_the_chunk_it_is_in(client):
    _seed_corpus(client)
    hit = next(h for h in client.get("/documents/search", params={"q": "refund policy", "role": "admin"}).json() if h["filename"] == "long.txt")
    assert hit["chunk_index"] >= 2
    assert hit["citation"] == f"long.txt, chunk {hit['chunk_index'] + 1}"
    assert hit["char_start"] <= len(" ".join(f"word{i}" for i in range(400))) < hit["char_end"] + 40
    assert "refund policy at the very end" in hit["snippet"]


def test_keyword_search_ranks_more_mentions_and_filename_matches_first(client):
    _seed_corpus(client)
    hits = client.get("/documents/search", params={"q": "refund", "role": "admin"}).json()
    names = [h["filename"] for h in hits]
    assert names[0] == "refund-policy.txt"  # a filename match counts five times a body match, plus three in the text
    assert "notes.txt" in names and names.count("notes.txt") == 1  # one document, its latest text
    assert next(h for h in hits if h["filename"] == "notes.txt")["snippet"].startswith("Notes, second version")
    assert [n for n in names if n.startswith("tie-")] == ["tie-a.txt", "tie-b.txt", "tie-c.txt"]  # ties keep their stored order


def test_keyword_search_reads_each_text_on_its_own_and_keeps_none(client, statements):
    _seed_corpus(client)
    statements.clear()
    db = SessionLocal()
    try:
        matches = documents_router._keyword_matches(db, "refund", 20)
        assert len(matches) >= 8

        reads = _reads_document_text(statements)
        # the matching documents are found without their text, and each text is then read by itself:
        assert all(_selected_columns(sql).strip() == "SELECT documents.text_content AS documents_text_content" for sql, _ in reads)
        assert len(reads) == len(matches)

        for doc, _, _ in matches:
            assert "text_content" in inspect(doc).unloaded  # nothing is left holding a copy
    finally:
        db.close()


def test_a_filename_only_match_shows_the_start_of_the_document(client):
    _upload(client, "invoice-2026.txt", "Totals and terms. " * 20)
    hit = client.get("/documents/search", params={"q": "invoice", "role": "admin"}).json()[0]
    assert hit["citation"] == "invoice-2026.txt"
    assert hit["chunk_index"] is None
    assert hit["snippet"].startswith("Totals and terms.") and hit["snippet"].endswith("…")


def test_a_locked_match_still_shows_nothing_from_inside(anonymous_client, client):
    _upload(client, "secret-plan.txt", "the launch date is the 14th", level="confidential")
    hits = anonymous_client.get("/documents/search", params={"q": "launch"}).json()
    assert [(h["filename"], h["locked"]) for h in hits] == [("secret-plan.txt", True)]
    assert "launch date" not in str(hits) and "snippet" not in hits[0]


# ---- the table viewer


def _old_csv_page(text, offset, limit):
    """The previous implementation: the same loop, reading lines out of a StringIO (a second copy of the text)."""
    header, page, total, width = None, [], 0, 0
    try:
        for record in csv.reader(io.StringIO(text, newline="")):
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
        header[0] = header[0].lstrip("﻿")
    return {
        "columns": header + [""] * (width - len(header)),
        "rows": [row + [""] * (width - len(row)) for row in page],
        "total_rows": total,
        "offset": offset,
        "limit": limit,
    }


def _page_or_error(fn, text, offset, limit):
    try:
        out = fn(text, offset, limit)
    except HTTPException as exc:
        return ("error", exc.status_code, exc.detail)
    return out if isinstance(out, dict) else out.model_dump()


_TRICKY_TEXTS = [
    "",
    "\n",
    "\r\n\r\n",
    "a,b\n1,2\n",
    "a,b\r\n1,2\r\n3,4\r\n",
    "a,b\r1,2\r3,4\r",  # old Mac line endings
    "a,b\n1,2\r\n3,4\r5,6",  # all three at once, last line unterminated
    "a,b\n\n\n1,2\n\n",  # blank lines
    "﻿id,name\n1,Ada\n",  # byte-order mark
    'a,b\n"x\ny",2\n3,4\n',  # a quoted field with a line break in it
    'a,b\n"x\r\ny",2\r\n3,4\r\n',
    'a,b\n"x\ry",2\r3,4\r',
    'a,b\n"never closed,2\n3,4\n',
    'a,b\n"x""y",2\n',
    "a,b,c\n1,2\n1,2,3,4\n5\n",  # ragged
    "only one line",
    "x\n" * 5000,
    "é,ü\n日本,語\n",
    "a,b\n1,2\n\r\n3,4\n",
    "a b,c\n1,2\n",  # a line separator that is NOT a line ending for CSV
    "a,b\x0b\x0c\n1,2\n",
]


@pytest.mark.parametrize("text", _TRICKY_TEXTS)
def test_the_viewer_splits_lines_exactly_as_it_did(text):
    assert list(documents_router._lines(text)) == list(io.StringIO(text, newline=""))
    for offset, limit in [(0, 200), (1, 1), (2, 3), (5000, 10)]:
        assert _page_or_error(documents_router._csv_page, text, offset, limit) == _page_or_error(_old_csv_page, text, offset, limit)


def test_the_viewer_gives_the_same_pages_as_before_on_random_text():
    rng = random.Random(20261017)
    pieces = ["a", "b", "1", " ", ",", ",", '"', '""', "\n", "\r", "\r\n", "\n\n", "é", "﻿", "x" * 30, ";", "\t"]
    for _ in range(4_000):
        text = "".join(rng.choice(pieces) for _ in range(rng.randint(0, 60)))
        offset, limit = rng.randint(0, 6), rng.randint(1, 8)
        mine = _page_or_error(documents_router._csv_page, text, offset, limit)
        theirs = _page_or_error(_old_csv_page, text, offset, limit)
        assert mine == theirs, repr(text)
        assert list(documents_router._lines(text)) == list(io.StringIO(text, newline="")), repr(text)


def test_the_viewer_reads_a_long_table_a_line_at_a_time():
    rows = 20_000
    text = "id,name,note\n" + "".join(f"{i},Person {i},a note that is not short\n" for i in range(rows))
    page = documents_router._csv_page(text, 19_990, 50)
    assert page.total_rows == rows
    assert [r[0] for r in page.rows] == [str(i) for i in range(19_990, 20_000)]


def test_the_lines_of_a_text_are_not_all_built_up_front():
    lines = documents_router._lines("a\nb\nc\n")
    assert iter(lines) is lines  # a generator: one line at a time, nothing held but the text itself
    assert next(lines) == "a\n"


# ---------------------------------------------------------------- saving a check's rows


def _customers_csv() -> str:
    rows = [f"CUST-{i:06d},Person{i},person{i}@x.com" for i in range(1, 21)]
    return "customer_id,name,email\n" + "\n".join(rows) + "\n"


def _run_and_get_id(client, csv_text):
    resp = client.post("/datasets/customers/runs", files={"file": ("data.csv", io.BytesIO(csv_text.encode()), "text/csv")})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _save(client, run_id, data=None, level="team"):
    files = {"data_file": ("data.csv", io.BytesIO(data.encode()), "text/csv")} if data is not None else None
    return client.post(f"/datasets/customers/runs/{run_id}/knowledge-report", data={"access_level": level}, files=files)


def test_saving_to_knowledge_search_takes_its_turn_like_a_check_does(client, monkeypatch):
    csv_text = _customers_csv()
    run_id = _run_and_get_id(client, csv_text)

    taken = threading.BoundedSemaphore(1)
    taken.acquire()  # somebody else's upload is being worked on
    monkeypatch.setattr(limits, "_slots", taken)
    monkeypatch.setattr(limits, "_SLOT_WAIT_SECONDS", 0.05)

    resp = _save(client, run_id, data=csv_text)
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "10"
    assert "busy" in resp.json()["detail"]
    assert client.get("/documents", params={"role": "admin"}).json() == []  # nothing was half-saved

    taken.release()
    assert _save(client, run_id, data=csv_text).status_code == 200


def test_the_place_is_given_back_after_a_save_whether_it_works_or_not(client, monkeypatch):
    csv_text = _customers_csv()
    run_id = _run_and_get_id(client, csv_text)
    monkeypatch.setattr(limits, "_slots", threading.BoundedSemaphore(1))
    monkeypatch.setattr(limits, "_SLOT_WAIT_SECONDS", 0.2)

    assert _save(client, run_id, data=csv_text).status_code == 200
    assert _save(client, 9_999, data=csv_text).status_code == 404  # no such run
    assert _save(client, run_id, level="nonsense").status_code == 400
    assert _save(client, run_id, data=csv_text).status_code == 200  # a leaked place would make this a 503


def test_the_same_rows_again_are_recognised_without_being_stored_twice(client, statements):
    csv_text = _customers_csv()
    run_id = _run_and_get_id(client, csv_text)
    first = _save(client, run_id, data=csv_text).json()
    statements.clear()

    again = _save(client, run_id, data=csv_text).json()
    assert again["data"]["created"] is False and again["data"]["document_id"] == first["data"]["document_id"]
    assert [sql for sql, _ in statements if sql.lstrip().startswith("INSERT INTO documents")] == []


def test_rows_of_the_same_length_but_different_values_are_a_new_version(client):
    """Only the length is compared first - it must never be what decides that two texts are the same."""
    rows = [f"CUST-{i:06d},Person{i},person{i}@x.com" for i in range(1, 21)]
    one = "customer_id,name,email\n" + "\n".join(rows) + "\n"
    two = "customer_id,name,email\n" + "\n".join(rows[:-1] + [rows[-1].replace("person20", "person2o")]) + "\n"
    assert len(one) == len(two) and one != two
    run_id = _run_and_get_id(client, one)

    first = _save(client, run_id, data=one).json()
    second = _save(client, run_id, data=two).json()
    assert second["data"]["created"] is True and second["data"]["version"] == 2
    assert second["data"]["document_id"] != first["data"]["document_id"]
    text = client.get(f"/documents/{second['data']['document_id']}", params={"role": "admin"}).json()["text_content"]
    assert text == two.strip()


def test_rows_of_a_different_length_are_a_new_version_without_reading_the_old_text(client, statements):
    one = _customers_csv()
    two = one + "CUST-000021,Person21,person21@x.com\n"
    run_id = _run_and_get_id(client, one)
    first = _save(client, run_id, data=one).json()
    statements.clear()

    second = _save(client, run_id, data=two).json()
    assert second["data"]["version"] == 2
    old_rows_id = first["data"]["document_id"]
    # (the report saved beside the rows is the same text again, so that one is compared in full - it is small)
    asked_about_the_rows = [
        params for _, params in _reads_document_text(statements) if old_rows_id in tuple(params) or data_filename("customers") in tuple(params)
    ]
    assert asked_about_the_rows == []


def test_the_same_rows_at_a_different_level_are_still_a_new_version(client):
    csv_text = _customers_csv()
    run_id = _run_and_get_id(client, csv_text)
    _save(client, run_id, data=csv_text, level="team")
    moved = _save(client, run_id, data=csv_text, level="confidential").json()
    assert moved["data"]["created"] is True and moved["data"]["version"] == 2


def test_the_saved_rows_limit_is_a_ceiling_of_its_own(client, monkeypatch):
    """Raising the upload limit lets bigger files be checked; it does not let bigger ones be stored."""
    monkeypatch.setattr(settings, "max_upload_mb", 200)
    assert runs_router._MAX_SAVED_DATA_BYTES == 25 * MB
    csv_text = _customers_csv()
    run_id = _run_and_get_id(client, csv_text)
    monkeypatch.setattr(runs_router, "_MAX_SAVED_DATA_BYTES", len(csv_text.encode()) - 1)
    body = _save(client, run_id, data=csv_text).json()
    assert body["data"] is None and "only the report was saved" in body["data_error"]


# ---------------------------------------------------------------- the database waits its turn


def test_sqlite_waits_for_a_busy_database_instead_of_giving_up_after_five_seconds():
    """
    Two huge uploads finishing at the same moment: the second one's first write
    came while the first was still writing down its millions of flagged rows.
    SQLite's own patience is 5 seconds, then "database is locked" - a 500 for
    the visitor. Writing a big file's problems down takes longer than that, so
    the connection is told to wait.
    """
    if engine.dialect.name != "sqlite":
        pytest.skip("the wait is an SQLite setting")
    with engine.connect() as connection:
        waits_ms = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
    assert waits_ms >= 30_000


# ---------------------------------------------------------------- a Word file's size in the message


def _word_file_with_a_part_of(n_bytes: int, part: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(part, b"a" * n_bytes)
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("part", "n_bytes", "message_says"),
    [
        ("word/document.xml", 10 * MB + 1, "text inside this Word file unpacks to 10.1 MB, more than the 10 MB the server will open"),
        ("word/document.xml", 10 * MB + MB // 2, "text inside this Word file unpacks to 10.5 MB, more than the 10 MB the server will open"),
        ("word/document.xml", 12 * MB, "text inside this Word file unpacks to 12 MB, more than the 10 MB the server will open"),
        ("word/media/scan.bin", 50 * MB + 1, "This Word file unpacks to 50.1 MB, more than the 50 MB the server will open"),
        ("word/media/scan.bin", 51 * MB, "This Word file unpacks to 51 MB, more than the 50 MB the server will open"),
    ],
)
def test_the_word_message_never_shows_a_size_that_looks_like_the_limit(part, n_bytes, message_says):
    """A file a byte over the limit used to read "10 MB, more than the 10 MB"."""
    with pytest.raises(FileTooLarge) as exc:
        extract_text("big.docx", _word_file_with_a_part_of(n_bytes, part))
    assert message_says in str(exc.value)


def test_the_megabytes_in_a_message_round_up_and_drop_a_trailing_point_zero():
    from app.document_extraction import _mb_text

    assert [_mb_text(n) for n in (0, MB, 10 * MB, 10 * MB + 1, 10 * MB + MB // 10, 10 * MB + MB // 10 + 1, 1234 * MB)] == [
        "0", "1", "10", "10.1", "10.1", "10.2", "1,234",
    ]


# ---------------------------------------------------------------- listing the rows a check flagged


def _blank_note_csv(rows: int) -> bytes:
    lines = ["id,note,n"] + [f"R-{i:05d},,{i}" for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


def _flagged_run(client, rows: int) -> dict:
    resp = _post_run(client, "listing", _blank_note_csv(rows))
    assert resp.status_code == 200, resp.text
    run = resp.json()
    assert run["breakdown"]["missing_values"]["affected_rows"] == rows
    return run


def _issues(client, run, query: str = ""):
    return client.get(f"/datasets/listing/runs/{run['id']}/issues/missing_values{query}")


def test_a_checks_flagged_rows_are_listed_up_to_the_limit_asked_for(client):
    run = _flagged_run(client, 40)
    everything = _issues(client, run, "?limit=100").json()
    assert everything["count"] == 40 and len(everything["row_keys"]) == 40

    first_five = _issues(client, run, "?limit=5").json()
    assert first_five["count"] == 40  # still says how many rows were flagged
    assert first_five["row_keys"] == everything["row_keys"][:5] == [f"R-{i:05d}" for i in range(5)]  # the first ones, in order
    assert first_five["check_name"] == "missing_values"


def test_the_list_is_cut_at_a_sensible_number_unless_more_is_asked_for(client):
    """A check on a big file can flag a million rows; listing all of them took 10 MB and 200+ MB of memory."""
    assert runs_router._ISSUES_LISTED_BY_DEFAULT == 5_000
    assert runs_router._ISSUES_LISTED_AT_MOST == 100_000
    run = _flagged_run(client, 5_200)

    by_default = _issues(client, run).json()
    assert by_default["count"] == 5_200 and len(by_default["row_keys"]) == 5_000

    asked_for_all = _issues(client, run, "?limit=100000").json()
    assert asked_for_all["count"] == 5_200 and len(asked_for_all["row_keys"]) == 5_200
    assert asked_for_all["row_keys"][:5_000] == by_default["row_keys"]


@pytest.mark.parametrize("limit", ["0", "-1", "100001", "many"])
def test_a_limit_that_makes_no_sense_is_refused(client, limit):
    run = _flagged_run(client, 3)
    assert _issues(client, run, f"?limit={limit}").status_code == 422


def test_only_the_rows_asked_for_are_read_from_the_database(client, statements):
    run = _flagged_run(client, 30)
    statements.clear()
    body = _issues(client, run, "?limit=3").json()
    assert len(body["row_keys"]) == 3 and body["count"] == 30

    listing = [sql for sql, _ in statements if sql.lstrip().startswith("SELECT") and "check_issues.row_key" in _selected_columns(sql)]
    assert len(listing) == 1 and "LIMIT" in listing[0]  # cut in the database, not after fetching them all
    assert "check_issues.id" not in _selected_columns(listing[0])  # nothing but the key column


def test_the_page_asks_for_only_as_many_rows_as_it_shows():
    js = (STATIC / "app.js").read_text()
    assert "const AFFECTED_ROWS_SHOWN = 50;" in js
    assert "/issues/${encodeURIComponent(checkName)}?limit=${AFFECTED_ROWS_SHOWN}" in js
    assert "data.row_keys.slice(0, AFFECTED_ROWS_SHOWN)" in js
