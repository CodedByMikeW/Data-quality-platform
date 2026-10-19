"""
"Save a report of this check to Knowledge Search": the report text itself,
and the endpoint that files it - and, when they're sent along, the check's
rows - at public / team / confidential.

Embeddings are stubbed (app.document_store.embed_texts) - what's under test
is the report, the versioning and the permissions, not the search model, and
this keeps the file fast and runnable without downloading anything.
"""
import io
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import models
from app.database import SessionLocal
from app.quality_report import build_report, data_filename, report_filename
from app.routers import runs as runs_router


def _stub_embed(texts):
    return [[float(len(t) % 7 + 1), 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for t in texts]


@pytest.fixture(autouse=True)
def stub_embeddings(monkeypatch):
    monkeypatch.setattr("app.document_store.embed_texts", _stub_embed)


# ----------------------------------------------------------------- helpers

# 20 tidy rows plus deliberate problems, so several checks have something to
# report. The values below (ids, emails, names) are exactly what a report
# must NOT contain.
_SENSITIVE_VALUES = ["CUST-000004", "000003", "leaky.address@secret-domain.test", "Person5", "secret-domain"]


def _customers_csv(extra_column: bool = False) -> str:
    rows = []
    for i in range(1, 21):
        cust_id = f"CUST-{i:06d}"
        email = f"person{i}@x.com"
        if i == 2:
            email = ""  # missing value
        if i == 3:
            cust_id = "000003"  # breaks the dominant id shape
        if i == 5:
            email = "leaky.address@secret-domain.test"
        suffix = ",gold" if extra_column else ""
        rows.append(f"{cust_id},Person{i},{email}{suffix}")
    rows.append("CUST-000004,Person4,person4@x.com" + (",gold" if extra_column else ""))  # duplicate of row 4
    header = "customer_id,name,email" + (",tier" if extra_column else "")
    return header + "\n" + "\n".join(rows) + "\n"


def _run(client, dataset="customers", csv_text=None):
    csv_text = csv_text or _customers_csv()
    files = {"file": ("data.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    resp = client.post(f"/datasets/{dataset}/runs", files=files)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _save(client, dataset, run_id, access_level=None, data=None):
    """`data` is the CSV text to send along as the check's rows (the page sends the file it ran on)."""
    form = {"access_level": access_level} if access_level else None
    files = {"data_file": ("data.csv", io.BytesIO(data.encode()), "text/csv")} if data is not None else None
    return client.post(f"/datasets/{dataset}/runs/{run_id}/knowledge-report", data=form, files=files)


def _doc_text(client, document_id, role="admin"):
    resp = client.get(f"/documents/{document_id}", params={"role": role})
    assert resp.status_code == 200, resp.text
    return resp.json()["text_content"]


def _breakdown(**overrides):
    base = {
        "missing_values": {"weight": 15, "pass_rate": 0.95, "affected_rows": 150, "total_rows": 3000},
        "duplicates": {"weight": 10, "pass_rate": 1.0, "affected_rows": 0, "total_rows": 3000},
        "invalid_formats": {"weight": 15, "pass_rate": 0.7, "affected_rows": 900, "total_rows": 3000},
    }
    base.update(overrides)
    return base


def _fake_run(run_id=7, score=87.4, breakdown=None, row_count=3000, schema=None, when=None):
    return SimpleNamespace(
        id=run_id,
        score=score,
        breakdown=breakdown if breakdown is not None else _breakdown(),
        row_count=row_count,
        schema_snapshot=schema,
        created_at=when or datetime(2026, 10, 16, 4, 21),
    )


# ------------------------------------------------------- the report's text


def test_report_states_the_dataset_run_score_and_verdict():
    text = build_report("customers", _fake_run(score=87.4))
    assert "DATA QUALITY REPORT: customers" in text
    assert "Run: #7 on 2026-10-16 04:21 UTC" in text
    assert "Rows checked: 3,000" in text
    assert "Quality score: 87.4 / 100 (NEEDS REVIEW)" in text

    assert "(CERTIFIED)" in build_report("customers", _fake_run(score=93.0))


def test_report_lists_each_check_with_the_dashboards_pass_warn_fail_bands():
    text = build_report("customers", _fake_run())
    assert "Missing Values: PASS - 95.0% passed, 150 of 3,000 rows affected (weight 15)" in text
    assert "Duplicates: PASS - 100.0% passed, 0 of 3,000 rows affected (weight 10)" in text
    assert "Invalid Formats: FAIL - 70.0% passed, 900 of 3,000 rows affected (weight 15)" in text
    assert "Needs attention: Invalid Formats (FAIL)" in text

    warn = build_report("customers", _fake_run(breakdown=_breakdown(duplicates={"weight": 10, "pass_rate": 0.9, "affected_rows": 300, "total_rows": 3000})))
    assert "Duplicates: WARN" in warn


def test_report_says_everything_passed_when_it_did():
    clean = {"duplicates": {"weight": 10, "pass_rate": 1.0, "affected_rows": 0, "total_rows": 50}}
    text = build_report("customers", _fake_run(score=100.0, breakdown=clean))
    assert "Needs attention: nothing - every check passed." in text


def test_column_level_checks_count_columns_not_rows():
    breakdown = _breakdown(
        schema_changes={"weight": 10, "pass_rate": 0.75, "affected_rows": 2, "total_rows": 8},
        drift={"weight": 15, "pass_rate": 0.5, "affected_rows": 1, "total_rows": 2},
    )
    text = build_report("customers", _fake_run(breakdown=breakdown))
    assert "Schema Changes: FAIL - 75.0% passed, 2 of 8 columns changed (weight 10)" in text
    assert "Data Drift: FAIL - 50.0% passed, 1 of 2 columns drifted (weight 15)" in text


def test_checks_that_did_not_run_are_listed():
    text = build_report("customers", _fake_run())
    assert "Checks not run this time: Schema Changes, Outliers, Data Drift, Broken Relationships, Unexpected Values" in text


def test_report_compares_with_the_previous_run():
    previous = _fake_run(run_id=6, score=91.2, when=datetime(2026, 10, 15, 22, 10))
    text = build_report("customers", _fake_run(score=87.4), previous)
    assert "Previous run: #6 scored 91.2 on 2026-10-15 22:10 UTC (change: -3.8)" in text

    assert "Previous run: none - this is the first run for this dataset." in build_report("customers", _fake_run())


def test_report_lists_the_columns_and_what_changed_since_the_previous_run():
    previous = _fake_run(run_id=6, schema={"id": "int64", "name": "object", "age": "int64", "legacy": "object"})
    current = _fake_run(schema={"id": "int64", "name": "object", "age": "object", "tier": "object"})
    text = build_report("customers", current, previous)

    assert "4 columns: id (int64), name (object), age (object), tier (object)" in text
    assert "Schema changes since run #6:" in text
    assert "- Added: 'tier'" in text
    assert "- Removed: 'legacy'" in text
    assert "- Changed type: 'age' (int64 -> object)" in text


def test_report_notes_when_the_schema_is_unchanged_or_has_no_baseline():
    schema = {"id": "int64", "name": "object"}
    same = build_report("customers", _fake_run(schema=schema), _fake_run(run_id=6, schema=dict(schema)))
    assert "No schema changes since run #6." in same

    first = build_report("customers", _fake_run(schema=schema))
    assert "First run for this dataset, so there is no earlier schema to compare against." in first


def test_a_very_wide_schema_is_summarised_not_dumped():
    wide = {f"col_{i}": "float64" for i in range(100)}
    text = build_report("wide", _fake_run(schema=wide))
    assert "100 columns:" in text
    assert "(+60 more)" in text
    assert "col_39" in text and "col_40" not in text


def test_the_same_run_always_produces_identical_text():
    previous = _fake_run(run_id=6, score=90.0, schema={"a": "int64"})
    current = _fake_run(schema={"a": "int64", "b": "object"})
    assert build_report("customers", current, previous) == build_report("customers", current, previous)


def test_one_filename_per_dataset_label():
    assert report_filename("customers") == "quality-report-customers.txt"
    assert report_filename("Q3  Sales\nEU") == "quality-report-Q3 Sales EU.txt"
    assert report_filename("orders") != report_filename("customers")


# ------------------------------------------------------------- the endpoint


@pytest.mark.parametrize("level", ["public", "team", "confidential"])
def test_saving_files_a_report_at_the_chosen_access_level(client, level):
    run = _run(client)
    resp = _save(client, "customers", run["id"], level)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["created"] is True
    assert body["version"] == 1
    assert body["access_level"] == level
    assert body["filename"] == "quality-report-customers.txt"

    text = _doc_text(client, body["document_id"])
    assert "DATA QUALITY REPORT: customers" in text
    assert f"Run: #{run['id']}" in text


def test_the_default_access_level_is_team(client):
    run = _run(client)
    body = _save(client, "customers", run["id"]).json()
    assert body["access_level"] == "team"


@pytest.mark.parametrize(
    "level, visible_to",
    [
        ("public", {"employee", "manager", "admin"}),
        ("team", {"manager", "admin"}),
        ("confidential", {"admin"}),
    ],
)
def test_only_roles_cleared_for_the_level_can_open_the_report(client, level, visible_to):
    run = _run(client)
    _save(client, "customers", run["id"], level)

    for role in ("employee", "manager", "admin"):
        listed = {d["filename"]: d["locked"] for d in client.get("/documents", params={"role": role}).json()}
        # Everyone can see that the report is there; only the cleared roles get it unlocked.
        assert listed == {report_filename("customers"): role not in visible_to}, role


def test_the_report_is_stored_as_searchable_chunks(client):
    run = _run(client)
    doc_id = _save(client, "customers", run["id"], "team").json()["document_id"]

    db = SessionLocal()
    try:
        chunks = db.query(models.DocumentChunk).filter_by(document_id=doc_id).all()
    finally:
        db.close()
    assert chunks
    assert all(c.embedding for c in chunks)


def test_the_saved_report_turns_up_in_keyword_search_but_only_permitted_roles_get_it_open(client):
    run = _run(client)
    _save(client, "customers", run["id"], "team")

    manager_hits = client.get("/documents/search", params={"q": "data quality report", "role": "manager"}).json()
    assert [(h["filename"], h["locked"]) for h in manager_hits] == [("quality-report-customers.txt", False)]
    assert "data quality report" in manager_hits[0]["snippet"].lower()

    # An employee's search finds it too, but all they get is its name and level.
    employee_hits = client.get("/documents/search", params={"q": "data quality report", "role": "employee"}).json()
    assert [(h["filename"], h["locked"]) for h in employee_hits] == [("quality-report-customers.txt", True)]
    assert "snippet" not in employee_hits[0]


def test_a_later_run_becomes_version_two_of_the_same_document(client):
    first_run = _run(client)
    first = _save(client, "customers", first_run["id"], "team").json()

    second_run = _run(client, csv_text=_customers_csv(extra_column=True))
    second = _save(client, "customers", second_run["id"], "team").json()

    assert second["created"] is True
    assert second["version"] == 2
    assert second["document_id"] != first["document_id"]

    # One document in the list (the latest), with the older version in its history.
    listed = [d for d in client.get("/documents", params={"role": "admin"}).json() if d["filename"] == first["filename"]]
    assert [(d["version"], d["is_latest"]) for d in listed] == [(2, True)]
    history = client.get(f"/documents/{second['document_id']}/history", params={"role": "admin"}).json()
    assert [(d["version"], d["is_latest"]) for d in history] == [(2, True), (1, False)]

    # The newer report knows what changed since the earlier run.
    text = _doc_text(client, second["document_id"])
    assert f"Previous run: #{first_run['id']}" in text
    assert "- Added: 'tier'" in text


def test_saving_the_same_run_twice_at_the_same_level_is_not_a_new_version(client):
    run = _run(client)
    first = _save(client, "customers", run["id"], "team").json()
    again = _save(client, "customers", run["id"], "team").json()

    assert again["created"] is False
    assert again["document_id"] == first["document_id"]
    assert again["version"] == 1
    db = SessionLocal()
    try:
        assert db.query(models.Document).count() == 1
    finally:
        db.close()


def test_saving_the_same_run_at_a_different_level_is_a_new_version(client):
    run = _run(client)
    _save(client, "customers", run["id"], "team")
    changed = _save(client, "customers", run["id"], "confidential").json()

    assert changed["created"] is True
    assert changed["version"] == 2
    assert changed["access_level"] == "confidential"
    # The latest is now confidential, so for a manager the report is still listed - but locked.
    manager_view = {d["filename"]: d["locked"] for d in client.get("/documents", params={"role": "manager"}).json()}
    assert manager_view["quality-report-customers.txt"] is True


def test_datasets_get_their_own_report_documents(client):
    a = _run(client, "customers")
    b = _run(client, "orders")
    first = _save(client, "customers", a["id"], "team").json()
    second = _save(client, "orders", b["id"], "team").json()

    assert first["filename"] != second["filename"]
    assert first["version"] == 1 and second["version"] == 1


def test_a_report_never_contains_row_values_or_row_ids(client):
    run = _run(client)
    # The run really did flag specific rows - the drill-down has their keys...
    issues = client.get(f"/datasets/customers/runs/{run['id']}/issues/missing_values").json()
    assert issues["count"] >= 1 and issues["row_keys"]

    doc_id = _save(client, "customers", run["id"], "public").json()["document_id"]
    text = _doc_text(client, doc_id, role="employee")

    # ...but none of them, and none of the data, leaks into the saved report.
    for secret in _SENSITIVE_VALUES + issues["row_keys"]:
        assert secret not in text, secret
    assert "CUST-0000" not in text
    # What it does say: counts and column names.
    assert "customer_id" in text and "email" in text
    assert "rows affected" in text


def test_running_a_check_on_its_own_saves_nothing_to_knowledge_search(client):
    _run(client)
    assert client.get("/documents", params={"role": "admin"}).json() == []


def test_dataset_labels_with_spaces_and_symbols_work(client):
    label = "Q3 Sales — EU & UK"
    run = _run(client, label)
    body = _save(client, label, run["id"], "team").json()
    assert body["filename"] == f"quality-report-{label}.txt"
    assert label in _doc_text(client, body["document_id"])


def test_unknown_access_level_is_rejected_and_nothing_is_saved(client):
    run = _run(client)
    resp = _save(client, "customers", run["id"], "top-secret")
    assert resp.status_code == 400
    assert "access_level" in resp.json()["detail"]
    assert client.get("/documents", params={"role": "admin"}).json() == []


def test_unknown_dataset_or_run_is_a_404(client):
    run = _run(client, "customers")
    assert _save(client, "nope", run["id"], "team").status_code == 404
    assert _save(client, "customers", 999999, "team").status_code == 404


def test_a_run_cannot_be_filed_under_a_different_dataset(client):
    customers_run = _run(client, "customers")
    _run(client, "orders")
    assert _save(client, "orders", customers_run["id"], "team").status_code == 404


def test_a_failed_save_leaves_the_previous_version_untouched(client, monkeypatch):
    first_run = _run(client)
    assert _save(client, "customers", first_run["id"], "team").status_code == 200
    second_run = _run(client)

    def broken_model(texts):
        raise RuntimeError("search model unavailable")

    monkeypatch.setattr("app.document_store.embed_texts", broken_model)
    resp = _save(client, "customers", second_run["id"], "team")

    assert resp.status_code == 500
    assert "Couldn't save the report to Knowledge Search" in resp.json()["detail"]
    assert "search model unavailable" in resp.json()["detail"]

    docs = client.get("/documents", params={"role": "admin"}).json()
    assert [(d["version"], d["is_latest"]) for d in docs] == [(1, True)]


# ------------------------------------------- uploads share the same helper


def _upload_txt(client, filename, text):
    files = {"file": (filename, io.BytesIO(text.encode()), "text/plain")}
    return client.post("/documents", files=files)


def test_a_failed_upload_is_all_or_nothing(client, monkeypatch):
    assert _upload_txt(client, "policy.txt", "first version of the policy").status_code == 200

    def broken_model(texts):
        raise RuntimeError("search model unavailable")

    monkeypatch.setattr("app.document_store.embed_texts", broken_model)
    with pytest.raises(RuntimeError):
        _upload_txt(client, "policy.txt", "second version of the policy")

    # Before this was shared, the failed second upload had already demoted v1
    # and left a latest document with no searchable chunks.
    docs = client.get("/documents").json()
    assert [(d["filename"], d["version"], d["is_latest"]) for d in docs] == [("policy.txt", 1, True)]


def test_uploads_and_reports_version_independently(client):
    _upload_txt(client, "policy.txt", "travel policy v1")
    second = _upload_txt(client, "policy.txt", "travel policy v2").json()
    assert second["version"] == 2

    run = _run(client)
    report = _save(client, "customers", run["id"], "public").json()
    assert report["version"] == 1  # unaffected by policy.txt's history


# ------------------------------------------------- saving the rows as well


def _data_id(body):
    assert body["data_error"] is None, body["data_error"]
    return body["data"]["document_id"]


def test_the_rows_are_saved_next_to_the_report_at_the_same_level(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    body = _save(client, "customers", run["id"], "confidential", data=csv_text).json()

    assert body["data_error"] is None
    assert body["data"]["filename"] == "quality-data-customers.csv" == data_filename("customers")
    assert (body["data"]["version"], body["data"]["created"]) == (1, True)

    doc = client.get(f"/documents/{_data_id(body)}", params={"role": "admin"}).json()
    assert (doc["content_type"], doc["access_level"]) == ("csv", "confidential")
    assert doc["text_content"] == csv_text.strip()


@pytest.mark.parametrize(
    "level, visible_to",
    [
        ("public", {"employee", "manager", "admin"}),
        ("team", {"manager", "admin"}),
        ("confidential", {"admin"}),
    ],
)
def test_the_rows_open_for_exactly_the_roles_that_open_the_report(client, level, visible_to):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    _save(client, "customers", run["id"], level, data=csv_text)

    for role in ("employee", "manager", "admin"):
        listed = {d["filename"]: d["locked"] for d in client.get("/documents", params={"role": role}).json()}
        locked = role not in visible_to
        assert listed == {report_filename("customers"): locked, data_filename("customers"): locked}, role


def test_saved_rows_have_no_search_chunks_but_the_report_does(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    body = _save(client, "customers", run["id"], "team", data=csv_text).json()

    db = SessionLocal()
    try:
        assert db.query(models.DocumentChunk).filter_by(document_id=body["document_id"]).count() > 0
        assert db.query(models.DocumentChunk).filter_by(document_id=_data_id(body)).count() == 0
    finally:
        db.close()


def test_the_saved_rows_open_as_a_table(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    data_id = _data_id(_save(client, "customers", run["id"], "team", data=csv_text).json())

    table = client.get(f"/documents/{data_id}/table", params={"role": "manager"}).json()
    assert table["columns"] == ["customer_id", "name", "email"]
    assert table["total_rows"] == 21
    assert table["rows"][0] == ["CUST-000001", "Person1", "person1@x.com"]


def test_saving_the_same_rows_again_is_not_a_new_version(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    first = _save(client, "customers", run["id"], "team", data=csv_text).json()
    again = _save(client, "customers", run["id"], "team", data=csv_text).json()

    assert (again["created"], again["data"]["created"]) == (False, False)
    assert again["data"]["document_id"] == first["data"]["document_id"]
    assert again["data"]["version"] == 1
    db = SessionLocal()
    try:
        assert db.query(models.Document).count() == 2  # one report, one set of rows
    finally:
        db.close()


def test_the_same_rows_at_a_different_level_are_a_new_version(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    _save(client, "customers", run["id"], "team", data=csv_text)
    moved = _save(client, "customers", run["id"], "confidential", data=csv_text).json()

    assert moved["data"]["created"] is True and moved["data"]["version"] == 2
    manager_view = {d["filename"]: d["locked"] for d in client.get("/documents", params={"role": "manager"}).json()}
    assert manager_view[data_filename("customers")] is True  # the latest rows are now confidential, so locked for a manager


def test_a_later_check_saves_new_rows_as_a_new_version_of_the_same_document(client):
    first_csv = _customers_csv()
    second_csv = _customers_csv(extra_column=True)
    _save(client, "customers", _run(client, csv_text=first_csv)["id"], "team", data=first_csv)
    second = _save(client, "customers", _run(client, csv_text=second_csv)["id"], "team", data=second_csv).json()

    assert second["data"]["version"] == 2
    table = client.get(f"/documents/{_data_id(second)}/table", params={"role": "manager"}).json()
    assert table["columns"] == ["customer_id", "name", "email", "tier"]
    history = client.get(f"/documents/{_data_id(second)}/history", params={"role": "manager"}).json()
    assert [(d["version"], d["is_latest"]) for d in history] == [(2, True), (1, False)]


def test_without_a_data_file_only_the_report_is_saved(client):
    run = _run(client)
    body = _save(client, "customers", run["id"], "team").json()

    assert body["data"] is None and body["data_error"] is None
    assert [d["filename"] for d in client.get("/documents", params={"role": "admin"}).json()] == [
        report_filename("customers")
    ]


def test_a_data_file_over_the_size_limit_keeps_the_report_only(client, monkeypatch):
    monkeypatch.setattr(runs_router, "_MAX_SAVED_DATA_BYTES", 60)
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    body = _save(client, "customers", run["id"], "team", data=csv_text).json()

    assert body["created"] is True and body["data"] is None
    assert "only the report was saved" in body["data_error"]
    assert [d["filename"] for d in client.get("/documents", params={"role": "admin"}).json()] == [
        report_filename("customers")
    ]


def test_a_data_file_exactly_at_the_limit_is_still_saved(client, monkeypatch):
    csv_text = _customers_csv()
    monkeypatch.setattr(runs_router, "_MAX_SAVED_DATA_BYTES", len(csv_text.encode()))
    run = _run(client, csv_text=csv_text)
    body = _save(client, "customers", run["id"], "team", data=csv_text).json()
    assert body["data_error"] is None and body["data"]["created"] is True


def test_an_empty_data_file_keeps_the_report_only(client):
    run = _run(client)
    body = _save(client, "customers", run["id"], "team", data="   \n").json()

    assert body["created"] is True and body["data"] is None
    assert "empty" in body["data_error"]


def test_a_problem_saving_the_rows_does_not_undo_the_report(client, monkeypatch):
    real_store = runs_router.store_document

    def rows_fail(db, filename, content_type, *args, **kwargs):
        if content_type == "csv":
            raise RuntimeError("disk full")
        return real_store(db, filename, content_type, *args, **kwargs)

    monkeypatch.setattr(runs_router, "store_document", rows_fail)
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    resp = _save(client, "customers", run["id"], "team", data=csv_text)

    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True and body["data"] is None
    assert "The report was saved, but the data wasn't" in body["data_error"]
    assert "disk full" in body["data_error"]
    assert [d["filename"] for d in client.get("/documents", params={"role": "admin"}).json()] == [
        report_filename("customers")
    ]


def test_if_the_report_cannot_be_saved_the_rows_are_not_saved_either(client, monkeypatch):
    def broken_model(texts):
        raise RuntimeError("search model unavailable")

    monkeypatch.setattr("app.document_store.embed_texts", broken_model)
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    resp = _save(client, "customers", run["id"], "team", data=csv_text)

    assert resp.status_code == 500
    assert client.get("/documents", params={"role": "admin"}).json() == []


def test_attaching_the_rows_does_not_put_them_in_the_report(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    issues = client.get(f"/datasets/customers/runs/{run['id']}/issues/missing_values").json()
    body = _save(client, "customers", run["id"], "public", data=csv_text).json()

    report = _doc_text(client, body["document_id"], role="employee")
    for secret in _SENSITIVE_VALUES + issues["row_keys"]:
        assert secret not in report, secret
    # The values are in the rows document, which has its own (here, the same) access level.
    assert "leaky.address@secret-domain.test" in _doc_text(client, _data_id(body), role="employee")


def test_saved_rows_are_found_by_keyword_search_and_shown_only_to_those_cleared(client):
    csv_text = _customers_csv()
    run = _run(client, csv_text=csv_text)
    _save(client, "customers", run["id"], "team", data=csv_text)

    hits = client.get("/documents/search", params={"q": "leaky.address", "role": "manager"}).json()
    assert [(h["filename"], h["locked"]) for h in hits] == [(data_filename("customers"), False)]
    assert "leaky.address" in hits[0]["snippet"]
    assert hits[0]["chunk_index"] is None  # a document-level citation: rows have no chunks

    # Someone without the code finds that the file matches, but sees none of the row.
    employee = client.get("/documents/search", params={"q": "leaky.address", "role": "employee"})
    assert [(h["filename"], h["locked"]) for h in employee.json()] == [(data_filename("customers"), True)]
    assert "leaky.address" not in employee.text and "secret-domain" not in employee.text
