import io

from docx import Document as DocxFile

from app.document_extraction import extract_text, UnsupportedFileType


def _docx_bytes(paragraphs: list[str]) -> bytes:
    doc = DocxFile()
    for p in paragraphs:
        doc.add_paragraph(p)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_text_from_txt():
    content_type, text = extract_text("notes.txt", b"Hello, this is plain text.")
    assert content_type == "text"
    assert text == "Hello, this is plain text."


def test_extract_text_from_csv():
    content_type, text = extract_text("data.csv", b"a,b\n1,2\n")
    assert content_type == "csv"
    assert text == "a,b\n1,2"


def test_extract_text_from_docx():
    content_type, text = extract_text("report.docx", _docx_bytes(["First paragraph.", "Second one."]))
    assert content_type == "docx"
    assert "First paragraph." in text
    assert "Second one." in text


def test_unsupported_extension_raises():
    try:
        extract_text("archive.zip", b"whatever")
        assert False, "should have raised"
    except UnsupportedFileType:
        pass


def _upload_txt(client, filename, text, access_level=None):
    files = {"file": (filename, io.BytesIO(text.encode()), "text/plain")}
    data = {"access_level": access_level} if access_level else None
    return client.post("/documents", files=files, data=data)


def test_upload_document_end_to_end(client):
    resp = _upload_txt(client, "policy.txt", "Company travel policy: book economy class.")
    assert resp.status_code == 200
    body = resp.json()
    assert body["filename"] == "policy.txt"
    assert body["content_type"] == "text"
    assert body["char_count"] == len("Company travel policy: book economy class.")


def test_list_documents_includes_uploaded_file(client):
    _upload_txt(client, "handbook.txt", "Employee handbook contents.")
    resp = client.get("/documents")
    assert resp.status_code == 200
    filenames = [d["filename"] for d in resp.json()]
    assert "handbook.txt" in filenames


def test_get_document_returns_full_text(client):
    created = _upload_txt(client, "memo.txt", "This is the full memo text.").json()
    resp = client.get(f"/documents/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["text_content"] == "This is the full memo text."


def test_unsupported_file_type_is_rejected(client):
    files = {"file": ("archive.zip", io.BytesIO(b"not a real zip"), "application/zip")}
    resp = client.post("/documents", files=files)
    assert resp.status_code == 400


def test_empty_file_is_rejected(client):
    files = {"file": ("empty.txt", io.BytesIO(b""), "text/plain")}
    resp = client.post("/documents", files=files)
    assert resp.status_code == 400


def test_unknown_document_returns_404(client):
    resp = client.get("/documents/999999")
    assert resp.status_code == 404


def test_search_finds_matching_document_by_content(client):
    _upload_txt(client, "vacation_policy.txt", "Employees get 15 days of paid time off per year.")
    _upload_txt(client, "expense_policy.txt", "Submit receipts within 30 days of purchase.")

    resp = client.get("/documents/search", params={"q": "paid time off"})
    assert resp.status_code == 200
    body = resp.json()

    filenames = [r["filename"] for r in body]
    assert "vacation_policy.txt" in filenames
    assert "expense_policy.txt" not in filenames
    assert "paid time off" in body[0]["snippet"].lower()


def test_search_is_case_insensitive(client):
    _upload_txt(client, "onboarding.txt", "Welcome to the Engineering team!")
    resp = client.get("/documents/search", params={"q": "engineering"})
    assert resp.status_code == 200
    assert any(r["filename"] == "onboarding.txt" for r in resp.json())


def test_search_matches_by_filename_too(client):
    _upload_txt(client, "quarterly-report-q3.txt", "Nothing relevant in the body text here.")
    resp = client.get("/documents/search", params={"q": "quarterly-report"})
    assert resp.status_code == 200
    assert any(r["filename"] == "quarterly-report-q3.txt" for r in resp.json())


def test_search_with_no_matches_returns_empty_list(client):
    _upload_txt(client, "irrelevant.txt", "Some completely unrelated content.")
    resp = client.get("/documents/search", params={"q": "xyzzy-not-present-anywhere"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_search_rejects_empty_query(client):
    resp = client.get("/documents/search", params={"q": "   "})
    assert resp.status_code == 400


def test_search_result_includes_precise_citation(client):
    text = "Employees get 15 days of paid time off per year."
    _upload_txt(client, "vacation_policy.txt", text)

    resp = client.get("/documents/search", params={"q": "paid time off"})
    assert resp.status_code == 200
    result = resp.json()[0]

    assert result["chunk_index"] == 0
    assert result["char_start"] is not None and result["char_end"] is not None
    assert text[result["char_start"] : result["char_end"]] == text  # single chunk covers the whole short doc
    assert result["citation"] == "vacation_policy.txt, chunk 1"


def test_search_match_by_filename_only_falls_back_to_document_citation(client):
    _upload_txt(client, "quarterly-report-q3.txt", "Nothing relevant in the body text here.")
    resp = client.get("/documents/search", params={"q": "quarterly-report"})
    assert resp.status_code == 200
    result = resp.json()[0]

    assert result["chunk_index"] is None
    assert result["citation"] == "quarterly-report-q3.txt"


def test_semantic_search_finds_conceptually_similar_document(client):
    _upload_txt(client, "pet_story.txt", "The cat sat quietly on the mat all afternoon.")
    _upload_txt(client, "finance_news.txt", "Stock prices fell sharply today amid inflation fears.")

    resp = client.get("/documents/semantic-search", params={"q": "a feline resting on a rug"})
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) > 0
    assert body[0]["filename"] == "pet_story.txt"
    assert 0 <= body[0]["similarity"] <= 1


def test_semantic_search_result_includes_precise_citation(client):
    _upload_txt(client, "pet_story.txt", "The cat sat quietly on the mat all afternoon.")

    resp = client.get("/documents/semantic-search", params={"q": "a feline resting on a rug"})
    assert resp.status_code == 200
    result = resp.json()[0]

    assert result["chunk_index"] == 0
    assert result["char_start"] == 0
    assert result["char_end"] > 0
    assert result["citation"] == "pet_story.txt, chunk 1"


def test_semantic_search_rejects_empty_query(client):
    resp = client.get("/documents/semantic-search", params={"q": "  "})
    assert resp.status_code == 400


def test_semantic_search_with_no_documents_returns_empty_list(client):
    resp = client.get("/documents/semantic-search", params={"q": "anything at all"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_hybrid_search_includes_literal_keyword_matches(client):
    _upload_txt(client, "literal_cat.txt", "The cat knocked a mug off the table.")

    resp = client.get("/documents/hybrid-search", params={"q": "cat"})
    assert resp.status_code == 200
    by_name = {r["filename"]: r for r in resp.json()}

    assert "literal_cat.txt" in by_name
    assert "keyword" in by_name["literal_cat.txt"]["sources"]


def test_hybrid_search_includes_semantic_only_matches(client):
    # No literal word overlap with the query at all - only findable via
    # semantic similarity, proving hybrid actually merges both channels
    # rather than just running keyword search.
    _upload_txt(client, "pet_story.txt", "The cat sat quietly on the mat all afternoon.")

    resp = client.get("/documents/hybrid-search", params={"q": "a feline resting on a rug"})
    assert resp.status_code == 200
    by_name = {r["filename"]: r for r in resp.json()}

    assert "pet_story.txt" in by_name
    assert "semantic" in by_name["pet_story.txt"]["sources"]


def test_hybrid_search_ranks_double_matches_above_single_matches(client):
    # Matches both literally (contains "cat") and scores well semantically,
    # since it's actually about a cat.
    _upload_txt(client, "both.txt", "A cat is a small domesticated feline.")
    # Matches literally only - "cat" is a substring of "Category", but the
    # document is about home security, not remotely close semantically.
    _upload_txt(client, "keyword_only.txt", "Category: home security cameras and electronics.")

    resp = client.get("/documents/hybrid-search", params={"q": "cat"})
    assert resp.status_code == 200
    body = resp.json()

    by_name = {r["filename"]: r for r in body}
    assert "keyword" in by_name["both.txt"]["sources"]
    assert "keyword" in by_name["keyword_only.txt"]["sources"]
    assert by_name["both.txt"]["score"] > by_name["keyword_only.txt"]["score"]


def test_hybrid_search_result_includes_citation(client):
    _upload_txt(client, "literal_cat.txt", "The cat knocked a mug off the table.")

    resp = client.get("/documents/hybrid-search", params={"q": "cat"})
    assert resp.status_code == 200
    by_name = {r["filename"]: r for r in resp.json()}

    assert by_name["literal_cat.txt"]["citation"] == "literal_cat.txt, chunk 1"


def test_hybrid_search_rejects_empty_query(client):
    resp = client.get("/documents/hybrid-search", params={"q": "   "})
    assert resp.status_code == 400


def test_hybrid_search_with_no_documents_returns_empty_list(client):
    resp = client.get("/documents/hybrid-search", params={"q": "anything"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_upload_defaults_to_public_access_level(client):
    resp = _upload_txt(client, "notes.txt", "Some notes.")
    assert resp.json()["access_level"] == "public"


def test_upload_accepts_explicit_access_level(client):
    resp = _upload_txt(client, "salary_bands.txt", "Confidential salary data.", access_level="confidential")
    assert resp.status_code == 200
    assert resp.json()["access_level"] == "confidential"


def test_upload_rejects_unknown_access_level(client):
    resp = _upload_txt(client, "notes.txt", "Some notes.", access_level="top-secret")
    assert resp.status_code == 400


def test_list_documents_locks_documents_above_role_clearance(client):
    _upload_txt(client, "handbook.txt", "Employee handbook.", access_level="public")
    _upload_txt(client, "salary_bands.txt", "Confidential salary data.", access_level="confidential")

    employee_view = {d["filename"]: d for d in client.get("/documents", params={"role": "employee"}).json()}
    admin_view = {d["filename"]: d for d in client.get("/documents", params={"role": "admin"}).json()}

    # Everything is listed; what's above the role's clearance comes back locked.
    assert set(employee_view) == {"handbook.txt", "salary_bands.txt"}
    assert employee_view["handbook.txt"]["locked"] is False
    assert employee_view["salary_bands.txt"]["locked"] is True
    assert admin_view["salary_bands.txt"]["locked"] is False


def test_list_documents_rejects_unknown_role(client):
    resp = client.get("/documents", params={"role": "hacker"})
    assert resp.status_code == 400


def test_get_document_denies_role_below_clearance(client):
    created = _upload_txt(client, "salary_bands.txt", "Confidential salary data.", access_level="confidential").json()

    resp = client.get(f"/documents/{created['id']}", params={"role": "employee"})
    assert resp.status_code == 403

    resp = client.get(f"/documents/{created['id']}", params={"role": "admin"})
    assert resp.status_code == 200


def test_manager_role_can_open_team_but_not_confidential(client):
    _upload_txt(client, "team_notes.txt", "Team-only planning notes.", access_level="team")
    _upload_txt(client, "salary_bands.txt", "Confidential salary data.", access_level="confidential")

    manager_view = {d["filename"]: d["locked"] for d in client.get("/documents", params={"role": "manager"}).json()}

    assert manager_view == {"team_notes.txt": False, "salary_bands.txt": True}


def _assert_locked_for_employee_open_for_admin(employee_hits, admin_hits):
    """An employee still finds the document - as a locked hit with no content - and an admin gets it in full."""
    assert [(h["filename"], h["locked"]) for h in employee_hits] == [("salary_bands.txt", True)]
    assert "snippet" not in employee_hits[0]

    admin = [h for h in admin_hits if h["filename"] == "salary_bands.txt"]
    assert admin and admin[0]["locked"] is False and admin[0]["snippet"]


def test_keyword_search_locks_confidential_document_for_employee(client):
    _upload_txt(client, "salary_bands.txt", "Confidential salary bands for engineering.", access_level="confidential")

    employee_resp = client.get("/documents/search", params={"q": "salary", "role": "employee"})
    admin_resp = client.get("/documents/search", params={"q": "salary", "role": "admin"})

    _assert_locked_for_employee_open_for_admin(employee_resp.json(), admin_resp.json())


def test_semantic_search_locks_confidential_document_for_employee(client):
    _upload_txt(
        client,
        "salary_bands.txt",
        "Confidential compensation ranges for the engineering department.",
        access_level="confidential",
    )

    employee_resp = client.get("/documents/semantic-search", params={"q": "employee pay ranges", "role": "employee"})
    admin_resp = client.get("/documents/semantic-search", params={"q": "employee pay ranges", "role": "admin"})

    _assert_locked_for_employee_open_for_admin(employee_resp.json(), admin_resp.json())
    assert "similarity" not in employee_resp.json()[0]


def test_hybrid_search_locks_confidential_document_for_employee(client):
    _upload_txt(client, "salary_bands.txt", "Confidential salary bands for engineering.", access_level="confidential")

    employee_resp = client.get("/documents/hybrid-search", params={"q": "salary", "role": "employee"})
    admin_resp = client.get("/documents/hybrid-search", params={"q": "salary", "role": "admin"})

    _assert_locked_for_employee_open_for_admin(employee_resp.json(), admin_resp.json())
    assert "score" not in employee_resp.json()[0]


def test_search_rejects_unknown_role(client):
    resp = client.get("/documents/search", params={"q": "anything", "role": "hacker"})
    assert resp.status_code == 400


def test_reuploading_a_filename_creates_a_new_version(client):
    first = _upload_txt(client, "handbook.txt", "Version one of the handbook.").json()
    second = _upload_txt(client, "handbook.txt", "Version two of the handbook.").json()

    assert first["version"] == 1
    assert second["version"] == 2
    assert second["is_latest"] is True


def test_document_list_only_shows_latest_version(client):
    _upload_txt(client, "handbook.txt", "Version one.")
    _upload_txt(client, "handbook.txt", "Version two.")

    resp = client.get("/documents").json()
    handbook_entries = [d for d in resp if d["filename"] == "handbook.txt"]

    assert len(handbook_entries) == 1
    assert handbook_entries[0]["version"] == 2


def test_search_only_matches_latest_version_content(client):
    _upload_txt(client, "handbook.txt", "This mentions unicorns exclusively.")
    _upload_txt(client, "handbook.txt", "This version has no mythical creatures.")

    resp = client.get("/documents/search", params={"q": "unicorns"}).json()
    assert resp == []

    resp = client.get("/documents/search", params={"q": "mythical"}).json()
    assert any(r["filename"] == "handbook.txt" for r in resp)


def test_get_document_history_lists_all_versions_newest_first(client):
    _upload_txt(client, "handbook.txt", "Version one.")
    second = _upload_txt(client, "handbook.txt", "Version two.").json()
    third = _upload_txt(client, "handbook.txt", "Version three.").json()

    resp = client.get(f"/documents/{third['id']}/history")
    assert resp.status_code == 200
    versions = [d["version"] for d in resp.json()]
    assert versions == [3, 2, 1]

    # A prior version's own id can also be used to look up the same history.
    resp = client.get(f"/documents/{second['id']}/history")
    assert [d["version"] for d in resp.json()] == [3, 2, 1]


def test_get_document_history_filters_versions_above_role_clearance(client):
    _upload_txt(client, "handbook.txt", "Version one.", access_level="public")
    second = _upload_txt(client, "handbook.txt", "Version two, now confidential.", access_level="confidential").json()

    employee_resp = client.get(f"/documents/{second['id']}/history", params={"role": "employee"})
    assert employee_resp.status_code == 403  # the target version itself is above clearance

    first_id = second["id"] - 1
    employee_resp = client.get(f"/documents/{first_id}/history", params={"role": "employee"})
    assert employee_resp.status_code == 200
    assert [d["version"] for d in employee_resp.json()] == [1]  # v2 hidden, only v1 shown


def test_get_document_history_rejects_unknown_document(client):
    resp = client.get("/documents/999999/history")
    assert resp.status_code == 404


def test_search_analytics_requires_manager_or_admin_role(client):
    _upload_txt(client, "notes.txt", "Some notes here.")
    client.get("/documents/search", params={"q": "notes"})

    employee_resp = client.get("/documents/analytics/summary", params={"role": "employee"})
    assert employee_resp.status_code == 403

    manager_resp = client.get("/documents/analytics/summary", params={"role": "manager"})
    assert manager_resp.status_code == 200


def test_search_analytics_counts_searches_by_mode(client):
    _upload_txt(client, "notes.txt", "Some notes here.")
    client.get("/documents/search", params={"q": "notes"})
    client.get("/documents/search", params={"q": "notes"})
    client.get("/documents/semantic-search", params={"q": "notes"})
    client.get("/documents/hybrid-search", params={"q": "notes"})

    resp = client.get("/documents/analytics/summary", params={"role": "admin"}).json()
    assert resp["total_searches"] == 4
    assert resp["searches_by_mode"] == {"keyword": 2, "semantic": 1, "hybrid": 1}


def test_search_analytics_top_queries_are_case_insensitive(client):
    _upload_txt(client, "notes.txt", "Some notes here.")
    client.get("/documents/search", params={"q": "Notes"})
    client.get("/documents/search", params={"q": "notes"})
    client.get("/documents/search", params={"q": "NOTES"})

    resp = client.get("/documents/analytics/summary", params={"role": "admin"}).json()
    assert resp["top_queries"] == [{"query": "notes", "count": 3}]


def test_search_analytics_flags_zero_result_queries(client):
    _upload_txt(client, "notes.txt", "Some notes here.")
    client.get("/documents/search", params={"q": "notes"})  # 1 hit
    client.get("/documents/search", params={"q": "xyzzy-nonexistent"})  # 0 hits

    resp = client.get("/documents/analytics/summary", params={"role": "admin"}).json()
    assert resp["zero_result_queries"] == [{"query": "xyzzy-nonexistent", "count": 1}]


def test_delete_search_logs_requires_manager_or_admin_role(client):
    _upload_txt(client, "notes.txt", "Some notes here.")
    client.get("/documents/search", params={"q": "notes"})

    resp = client.delete("/documents/search-logs", params={"role": "employee"})
    assert resp.status_code == 403

    resp = client.delete("/documents/search-logs", params={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["rows_deleted"] == 1


def test_delete_search_logs_clears_analytics(client):
    _upload_txt(client, "notes.txt", "Some notes here.")
    client.get("/documents/search", params={"q": "notes"})
    client.delete("/documents/search-logs", params={"role": "admin"})

    resp = client.get("/documents/analytics/summary", params={"role": "admin"}).json()
    assert resp["total_searches"] == 0
    assert resp["top_queries"] == []


def test_delete_search_logs_with_no_data_reports_zero(client):
    resp = client.delete("/documents/search-logs", params={"role": "admin"})
    assert resp.status_code == 200
    assert resp.json()["rows_deleted"] == 0
