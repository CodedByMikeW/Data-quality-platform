"""
Opening a saved CSV as a table: GET /documents/{id}/table.

The page's viewer uses it to show the rows of any CSV document (a saved
check's data, or a CSV uploaded straight to Knowledge Search) a page at a
time, under the same access rules as opening the document itself.
"""
import csv
import io

import pytest

from app.config import settings

TEAM = settings.team_access_code
CONFIDENTIAL = settings.confidential_access_code
ID = settings.confidential_id_number


def _stub_embed(texts):
    return [[float(len(t) % 7 + 1), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for t in texts]


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    monkeypatch.setattr("app.document_store.embed_texts", _stub_embed)


def _upload(client, filename, content, level="public"):
    data = content.encode("utf-8") if isinstance(content, str) else content
    files = {"file": (filename, io.BytesIO(data), "text/csv")}
    resp = client.post("/documents", files=files, data={"access_level": level})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _table(client, doc_id, **params):
    resp = client.get(f"/documents/{doc_id}/table", params={"role": "admin", **params})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _numbered(n):
    return "id,name\n" + "".join(f"{i},Person{i}\n" for i in range(1, n + 1))


# -------------------------------------------------------------- reading


def test_a_csv_comes_back_as_columns_rows_and_a_total(client):
    doc_id = _upload(client, "people.csv", "id,name,email\n1,Ada,ada@x.com\n2,Grace,grace@x.com\n")
    assert _table(client, doc_id) == {
        "columns": ["id", "name", "email"],
        "rows": [["1", "Ada", "ada@x.com"], ["2", "Grace", "grace@x.com"]],
        "total_rows": 2,
        "offset": 0,
        "limit": 200,
    }


def test_rows_come_back_a_page_at_a_time(client):
    doc_id = _upload(client, "people.csv", _numbered(5))

    middle = _table(client, doc_id, offset=1, limit=2)
    assert [row[0] for row in middle["rows"]] == ["2", "3"]
    assert (middle["total_rows"], middle["offset"], middle["limit"]) == (5, 1, 2)

    assert _table(client, doc_id, offset=4, limit=2)["rows"] == [["5", "Person5"]]
    past_the_end = _table(client, doc_id, offset=50)
    assert past_the_end["rows"] == [] and past_the_end["total_rows"] == 5


def test_the_default_page_is_200_rows_and_the_total_covers_the_whole_file(client):
    doc_id = _upload(client, "big.csv", _numbered(250))
    table = _table(client, doc_id)
    assert len(table["rows"]) == 200
    assert table["total_rows"] == 250
    assert [row[0] for row in _table(client, doc_id, offset=200)["rows"]] == [str(i) for i in range(201, 251)]


@pytest.mark.parametrize("params", [{"limit": 501}, {"limit": 0}, {"offset": -1}])
def test_page_sizes_are_bounded(client, params):
    doc_id = _upload(client, "people.csv", _numbered(3))
    assert client.get(f"/documents/{doc_id}/table", params={"role": "admin", **params}).status_code == 422


def test_the_largest_page_is_allowed(client):
    doc_id = _upload(client, "big.csv", _numbered(600))
    assert len(_table(client, doc_id, limit=500)["rows"]) == 500


def test_a_cell_with_a_line_break_in_it_is_one_row(client):
    doc_id = _upload(client, "notes.csv", 'id,note\n1,"first line\nsecond line"\n2,plain\n')
    table = _table(client, doc_id)
    assert table["total_rows"] == 2
    assert table["rows"][0] == ["1", "first line\nsecond line"]


def test_blank_lines_are_skipped(client):
    doc_id = _upload(client, "gaps.csv", "a,b\n\n1,2\n\n\n3,4\n")
    table = _table(client, doc_id)
    assert table["total_rows"] == 2
    assert table["rows"] == [["1", "2"], ["3", "4"]]


def test_windows_line_endings_read_the_same(client):
    doc_id = _upload(client, "win.csv", b"a,b\r\n1,2\r\n3,4\r\n")
    assert _table(client, doc_id)["rows"] == [["1", "2"], ["3", "4"]]


def test_uneven_rows_are_padded_so_every_page_has_the_same_columns(client):
    doc_id = _upload(client, "ragged.csv", "a,b\n1\n1,2,3\n")
    table = _table(client, doc_id)
    assert table["columns"] == ["a", "b", ""]
    assert table["rows"] == [["1", "", ""], ["1", "2", "3"]]


def test_an_excel_byte_order_mark_does_not_end_up_in_the_first_column_name(client):
    doc_id = _upload(client, "excel.csv", b"\xef\xbb\xbfid,name\n1,Ada\n")
    assert _table(client, doc_id)["columns"] == ["id", "name"]


def test_a_header_with_no_rows_is_an_empty_table(client):
    doc_id = _upload(client, "empty.csv", "id,name\n")
    table = _table(client, doc_id)
    assert table["columns"] == ["id", "name"]
    assert table["rows"] == [] and table["total_rows"] == 0


def test_a_cell_too_big_for_the_csv_reader_is_a_clear_400(client):
    doc_id = _upload(client, "huge.csv", "a,b\n1," + "x" * 5000 + "\n")
    previous = csv.field_size_limit(1000)
    try:
        resp = client.get(f"/documents/{doc_id}/table", params={"role": "admin"})
    finally:
        csv.field_size_limit(previous)
    assert resp.status_code == 400
    assert "can't be shown as a table" in resp.json()["detail"]


# ------------------------------------------------------------ the rules


def test_only_csv_documents_have_a_table(client):
    files = {"file": ("notes.txt", io.BytesIO(b"just some text"), "text/plain")}
    doc_id = client.post("/documents", files=files).json()["id"]
    resp = client.get(f"/documents/{doc_id}/table", params={"role": "admin"})
    assert resp.status_code == 400
    assert "CSV" in resp.json()["detail"]


def test_a_table_that_does_not_exist_is_a_404(client):
    assert client.get("/documents/99999/table", params={"role": "admin"}).status_code == 404


@pytest.mark.parametrize(
    "level, role, code, access_id, status",
    [
        ("public", "employee", None, None, 200),
        ("public", "employee", "000000", None, 200),  # a stale code mustn't lock out public data
        ("team", "employee", None, None, 403),
        ("team", "manager", None, None, 403),
        ("team", "manager", "000000", None, 403),
        ("team", "manager", TEAM, None, 200),
        ("team", "admin", TEAM, None, 403),
        ("team", "admin", CONFIDENTIAL, ID, 200),
        ("team", "manager", CONFIDENTIAL, None, 200),  # the confidential code alone still opens team
        ("confidential", "manager", TEAM, None, 403),
        ("confidential", "admin", TEAM, None, 403),
        ("confidential", "admin", None, None, 403),
        ("confidential", "admin", CONFIDENTIAL, None, 403),  # the code alone is not enough for confidential
        ("confidential", "manager", CONFIDENTIAL, None, 403),
        ("confidential", "admin", CONFIDENTIAL, "00000", 403),  # nor with a wrong ID
        ("confidential", "admin", None, ID, 403),  # nor the ID without the code
        ("confidential", "admin", CONFIDENTIAL, ID, 200),
    ],
)
def test_a_table_opens_only_with_the_code_for_its_level(anonymous_client, level, role, code, access_id, status):
    doc_id = _upload(anonymous_client, "secret.csv", "id,name\n1,Ada\n", level)
    headers = {"X-Access-Code": code} if code else {}
    if access_id:
        headers["X-Access-Id"] = access_id
    resp = anonymous_client.get(f"/documents/{doc_id}/table", params={"role": role}, headers=headers)
    assert resp.status_code == status
    if status == 403:
        assert "Ada" not in resp.text
