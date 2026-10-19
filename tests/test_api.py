import io


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def _upload(client, dataset_name, csv_text):
    files = {"file": ("test.csv", io.BytesIO(csv_text.encode()), "text/csv")}
    return client.post(f"/datasets/{dataset_name}/runs", files=files)


def test_run_pipeline_end_to_end(client):
    # 20 well-formed rows plus 2 deliberate issues, so the format-shape
    # detector has enough signal to trust "CUST-######" as the dominant
    # pattern (19/20 = 95%, above the confidence threshold a single
    # anomaly in a 5-row sample wouldn't clear).
    rows = []
    for i in range(1, 21):
        cust_id = f"CUST-{i:06d}"
        email = f"person{i}@x.com"
        if i == 2:
            email = ""  # missing value
        if i == 3:
            cust_id = "000003"  # breaks the dominant shape
        rows.append(f"{cust_id},Person{i},{email}")
    rows.append("CUST-000004,Person4,person4@x.com")  # exact duplicate of row 4

    csv_text = "customer_id,name,email\n" + "\n".join(rows) + "\n"
    resp = _upload(client, "test-customers", csv_text)

    assert resp.status_code == 200
    body = resp.json()

    assert body["row_count"] == 21
    assert body["breakdown"]["missing_values"]["affected_rows"] == 1
    assert body["breakdown"]["invalid_formats"]["affected_rows"] == 1
    assert body["breakdown"]["duplicates"]["affected_rows"] == 2
    assert 0 <= body["score"] <= 100


def test_issues_endpoint_returns_the_affected_keys(client):
    csv_text = "customer_id,name,email\nCUST-000001,Alice,\nCUST-000002,Bob,bob@x.com\n"
    run_resp = _upload(client, "issue-test", csv_text)
    run_id = run_resp.json()["id"]

    resp = client.get(f"/datasets/issue-test/runs/{run_id}/issues/missing_values")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 1
    assert body["row_keys"] == ["CUST-000001"]


def test_empty_csv_is_rejected(client):
    resp = _upload(client, "empty-test", "customer_id,name,email\n")
    assert resp.status_code == 400


def test_arbitrary_schema_is_handled_generically(client):
    # A completely different schema from the demo data - no customer_id,
    # email, or name at all. This used to crash with an unhandled 500
    # (KeyError, hardcoded to the demo columns). The pipeline now profiles
    # whatever columns actually exist, so this succeeds instead.
    csv_text = (
        "product_id,product_name,price\n"
        "PROD-001,Widget,9.99\n"
        "PROD-002,Gadget,19.99\n"
        "PROD-002,Gadget,19.99\n"
        "PROD-003,Gizmo,\n"
    )
    resp = _upload(client, "generic-schema-test", csv_text)

    assert resp.status_code == 200
    body = resp.json()
    assert body["row_count"] == 4
    assert body["breakdown"]["duplicates"]["affected_rows"] == 2
    assert body["breakdown"]["missing_values"]["affected_rows"] == 1


def test_duplicate_rows_are_found_in_a_file_with_no_id_column(client):
    # Nothing here looks like an id, so rows are named by their row number. The made-up number used to be
    # part of the comparison, which made every row different and let a file full of repeats score 100.
    csv_text = "city,product,amount\nParis,Shoes,10\nParis,Shoes,10\nRome,Hat,5\nOslo,Bag,7\n"
    resp = _upload(client, "no-id-column", csv_text)

    assert resp.status_code == 200
    body = resp.json()
    assert body["breakdown"]["duplicates"]["affected_rows"] == 2
    assert body["breakdown"]["duplicates"]["pass_rate"] == 0.5
    assert body["score"] < 100

    issues = client.get(f"/datasets/no-id-column/runs/{body['id']}/issues/duplicates")
    assert issues.status_code == 200
    assert issues.json()["count"] == 2
    assert issues.json()["row_keys"] == ["1", "2"]


def test_a_file_with_no_id_column_and_no_repeated_rows_has_no_duplicates(client):
    # same city and product twice, but not the same row: the amount differs
    csv_text = "city,product,amount\nParis,Shoes,10\nParis,Shoes,11\nRome,Hat,5\n"
    resp = _upload(client, "no-id-column-clean", csv_text)

    assert resp.status_code == 200
    body = resp.json()
    assert body["breakdown"]["duplicates"]["affected_rows"] == 0
    assert body["score"] == 100


def test_unknown_dataset_returns_404(client):
    resp = client.get("/datasets/does-not-exist/runs")
    assert resp.status_code == 404
