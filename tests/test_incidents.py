import io


def _csv_file(content: str, filename: str = "data.csv"):
    return {"file": (filename, io.BytesIO(content.encode()), "text/csv")}


def _upload_logs(client, content: str):
    return client.post("/incidents/logs/upload", files=_csv_file(content))


def _upload_metrics(client, content: str):
    return client.post("/incidents/metrics/upload", files=_csv_file(content))


_VALID_LOGS_CSV = """timestamp,service,level,message
2026-01-01T00:00:00Z,checkout-api,INFO,Order placed successfully
2026-01-01T00:01:00Z,payments-service,ERROR,Connection timeout to payment gateway
2026-01-01T00:02:00Z,payments-service,error,Circuit breaker opened
"""

_VALID_METRICS_CSV = """timestamp,service,metric_name,value
2026-01-01T00:00:00Z,checkout-api,latency_ms,120.5
2026-01-01T00:01:00Z,payments-service,latency_ms,950.2
2026-01-01T00:02:00Z,payments-service,error_rate,22.1
"""


def test_upload_logs_end_to_end(client):
    resp = _upload_logs(client, _VALID_LOGS_CSV)
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows_ingested"] == 3
    assert set(body["services"]) == {"checkout-api", "payments-service"}


def test_upload_logs_normalizes_level_case(client):
    _upload_logs(client, _VALID_LOGS_CSV)
    resp = client.get("/incidents/logs", params={"level": "error"})
    assert resp.status_code == 200
    assert len(resp.json()) == 2  # ERROR and error both normalize to ERROR


def test_upload_logs_rejects_missing_column(client):
    csv = "timestamp,service,level\n2026-01-01T00:00:00Z,checkout-api,INFO\n"
    resp = _upload_logs(client, csv)
    assert resp.status_code == 400
    assert "message" in resp.json()["detail"]


def test_upload_logs_rejects_unknown_level(client):
    csv = "timestamp,service,level,message\n2026-01-01T00:00:00Z,checkout-api,TRACE,Hello\n"
    resp = _upload_logs(client, csv)
    assert resp.status_code == 400
    assert "TRACE" in resp.json()["detail"]


def test_upload_logs_rejects_unparseable_timestamp(client):
    csv = "timestamp,service,level,message\nnot-a-date,checkout-api,INFO,Hello\n"
    resp = _upload_logs(client, csv)
    assert resp.status_code == 400


def test_upload_logs_rejects_empty_file(client):
    resp = client.post("/incidents/logs/upload", files={"file": ("empty.csv", io.BytesIO(b""), "text/csv")})
    assert resp.status_code == 400


def test_upload_metrics_end_to_end(client):
    resp = _upload_metrics(client, _VALID_METRICS_CSV)
    assert resp.status_code == 200
    body = resp.json()
    assert body["rows_ingested"] == 3
    assert set(body["services"]) == {"checkout-api", "payments-service"}


def test_upload_metrics_rejects_missing_column(client):
    csv = "timestamp,service,value\n2026-01-01T00:00:00Z,checkout-api,120.5\n"
    resp = _upload_metrics(client, csv)
    assert resp.status_code == 400
    assert "metric_name" in resp.json()["detail"]


def test_upload_metrics_rejects_non_numeric_value(client):
    csv = "timestamp,service,metric_name,value\n2026-01-01T00:00:00Z,checkout-api,latency_ms,not-a-number\n"
    resp = _upload_metrics(client, csv)
    assert resp.status_code == 400


def test_list_logs_filters_by_service(client):
    _upload_logs(client, _VALID_LOGS_CSV)
    resp = client.get("/incidents/logs", params={"service": "payments-service"})
    assert resp.status_code == 200
    assert all(r["service"] == "payments-service" for r in resp.json())
    assert len(resp.json()) == 2


def test_list_logs_orders_newest_first(client):
    _upload_logs(client, _VALID_LOGS_CSV)
    resp = client.get("/incidents/logs").json()
    timestamps = [r["timestamp"] for r in resp]
    assert timestamps == sorted(timestamps, reverse=True)


def test_list_metrics_filters_by_metric_name(client):
    _upload_metrics(client, _VALID_METRICS_CSV)
    resp = client.get("/incidents/metrics", params={"metric_name": "error_rate"})
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["value"] == 22.1


def test_list_services_returns_union_of_log_and_metric_services(client):
    _upload_logs(client, _VALID_LOGS_CSV)  # checkout-api, payments-service
    metrics_csv = "timestamp,service,metric_name,value\n2026-01-01T00:00:00Z,inventory-service,cpu_pct,25.0\n"
    _upload_metrics(client, metrics_csv)

    resp = client.get("/incidents/services")
    assert resp.status_code == 200
    assert resp.json() == ["checkout-api", "inventory-service", "payments-service"]


def test_list_logs_with_no_data_returns_empty_list(client):
    resp = client.get("/incidents/logs")
    assert resp.status_code == 200
    assert resp.json() == []


def _metrics_csv(rows: list[tuple[str, str, str, float]]) -> str:
    lines = ["timestamp,service,metric_name,value"]
    lines += [f"{ts},{service},{metric_name},{value}" for ts, service, metric_name, value in rows]
    return "\n".join(lines) + "\n"


def test_anomalies_endpoint_flags_a_spike(client):
    baseline = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 101.0]
    rows = [
        (f"2026-01-01T00:{i:02d}:00Z", "payments-service", "latency_ms", value) for i, value in enumerate(baseline)
    ]
    rows.append(("2026-01-01T00:11:00Z", "payments-service", "latency_ms", 900.0))
    _upload_metrics(client, _metrics_csv(rows))

    resp = client.get("/incidents/anomalies")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["value"] == 900.0
    assert body[0]["service"] == "payments-service"


def test_anomalies_endpoint_filters_by_service(client):
    baseline = [10.0, 11.0, 9.0, 10.0, 11.0, 9.0, 10.0, 11.0, 9.0, 10.0, 11.0]
    rows_a = [(f"2026-01-01T00:{i:02d}:00Z", "checkout-api", "error_rate", v) for i, v in enumerate(baseline)]
    rows_a.append(("2026-01-01T00:11:00Z", "checkout-api", "error_rate", 500.0))
    rows_b = [(f"2026-01-01T01:{i:02d}:00Z", "inventory-service", "error_rate", v) for i, v in enumerate(baseline)]
    rows_b.append(("2026-01-01T01:11:00Z", "inventory-service", "error_rate", 500.0))
    _upload_metrics(client, _metrics_csv(rows_a + rows_b))

    resp = client.get("/incidents/anomalies", params={"service": "checkout-api"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["service"] == "checkout-api"


def test_anomalies_endpoint_with_no_data_returns_empty_list(client):
    resp = client.get("/incidents/anomalies")
    assert resp.status_code == 200
    assert resp.json() == []


def _logs_csv(rows: list[tuple[str, str, str, str]]) -> str:
    lines = ["timestamp,service,level,message"]
    lines += [f"{ts},{service},{level},{message}" for ts, service, level, message in rows]
    return "\n".join(lines) + "\n"


def _burst_log_rows(service: str = "payments-service") -> list[tuple[str, str, str, str]]:
    rows = [(f"2026-01-01T00:{m:02d}:10Z", service, "INFO", "heartbeat") for m in range(15)]
    for i, minute in enumerate(range(15, 21)):
        count = [5, 8, 12, 15, 18, 20][i]
        for s in range(0, min(count * 5, 60), 5):
            rows.append((f"2026-01-01T00:{minute:02d}:{s:02d}Z", service, "ERROR", "Connection timeout"))
    rows += [(f"2026-01-01T00:{m:02d}:10Z", service, "INFO", "heartbeat") for m in range(21, 23)]
    return rows


def test_log_bursts_endpoint_flags_a_burst(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))

    resp = client.get("/incidents/log-bursts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) >= 1
    assert all(b["metric_name"] == "error_log_rate" for b in body)
    assert all(b["service"] == "payments-service" for b in body)


def test_log_bursts_endpoint_filters_by_service(client):
    rows = _burst_log_rows(service="payments-service") + [
        (f"2026-01-01T01:{m:02d}:10Z", "inventory-service", "INFO", "heartbeat") for m in range(15)
    ]
    _upload_logs(client, _logs_csv(rows))

    resp = client.get("/incidents/log-bursts", params={"service": "inventory-service"})
    assert resp.status_code == 200
    assert resp.json() == []


def test_log_bursts_endpoint_with_no_data_returns_empty_list(client):
    resp = client.get("/incidents/log-bursts")
    assert resp.status_code == 200
    assert resp.json() == []


def _shifted_burst_log_rows(service: str, shift_minutes: int) -> list[tuple[str, str, str, str]]:
    rows = [(f"2026-01-01T00:{shift_minutes + m:02d}:10Z", service, "INFO", "heartbeat") for m in range(15)]
    for i, minute in enumerate(range(15, 21)):
        count = [5, 8, 12, 15, 18, 20][i]
        for s in range(0, min(count * 5, 60), 5):
            rows.append((f"2026-01-01T00:{shift_minutes + minute:02d}:{s:02d}Z", service, "ERROR", "Timeout"))
    rows += [(f"2026-01-01T00:{shift_minutes + m:02d}:10Z", service, "INFO", "heartbeat") for m in range(21, 23)]
    return rows


def test_correlated_endpoint_merges_cascading_services_into_one_incident(client):
    rows = _burst_log_rows(service="payments-service") + _shifted_burst_log_rows("checkout-api", shift_minutes=4)
    _upload_logs(client, _logs_csv(rows))

    resp = client.get("/incidents/correlated")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 1
    assert body[0]["services"] == ["checkout-api", "payments-service"]
    assert body[0]["signal_count"] >= 2
    assert {s["signal_type"] for s in body[0]["signals"]} == {"log_burst"}

    root_cause = body[0]["root_cause"]
    assert root_cause[0]["service"] == "payments-service"  # started first
    assert root_cause[0]["rank"] == 1
    assert root_cause[1]["service"] == "checkout-api"
    assert root_cause[1]["rank"] == 2


def test_correlated_endpoint_classifies_a_severe_cascade_as_critical(client):
    baseline = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 101.0]
    rows = [(f"2026-01-01T00:{i:02d}:00Z", "payments-service", "latency_ms", v) for i, v in enumerate(baseline)]
    rows.append(("2026-01-01T00:11:00Z", "payments-service", "latency_ms", 900.0))
    rows += [(f"2026-01-01T00:{i + 3:02d}:00Z", "checkout-api", "latency_ms", v) for i, v in enumerate(baseline)]
    rows.append(("2026-01-01T00:14:00Z", "checkout-api", "latency_ms", 900.0))
    _upload_metrics(client, _metrics_csv(rows))

    resp = client.get("/incidents/correlated")
    assert resp.status_code == 200
    body = resp.json()

    critical = [i for i in body if i["severity"] == "critical"]
    assert len(critical) == 1
    assert set(critical[0]["services"]) == {"payments-service", "checkout-api"}


def test_correlated_endpoint_classifies_an_isolated_blip_as_info(client):
    baseline = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 101.0]
    rows = [(f"2026-01-01T00:{i:02d}:00Z", "inventory-service", "latency_ms", v) for i, v in enumerate(baseline)]
    rows.append(("2026-01-01T00:11:00Z", "inventory-service", "latency_ms", 130.0))  # a mild, isolated blip
    _upload_metrics(client, _metrics_csv(rows))

    resp = client.get("/incidents/correlated")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 1
    assert body[0]["severity"] == "info"


def test_correlated_endpoint_keeps_distant_incidents_separate(client):
    near_future = _burst_log_rows(service="payments-service")
    far_future = [
        (f"2026-01-02T00:{m:02d}:10Z", "inventory-service", "INFO", "heartbeat") for m in range(15)
    ] + [
        (f"2026-01-02T00:{minute:02d}:{s:02d}Z", "inventory-service", "ERROR", "Timeout")
        for i, minute in enumerate(range(15, 21))
        for s in range(0, min([5, 8, 12, 15, 18, 20][i] * 5, 60), 5)
    ]
    _upload_logs(client, _logs_csv(near_future + far_future))

    resp = client.get("/incidents/correlated")
    assert resp.status_code == 200
    body = resp.json()

    assert len(body) == 2
    all_services = {service for incident in body for service in incident["services"]}
    assert all_services == {"payments-service", "inventory-service"}


def test_correlated_endpoint_with_no_data_returns_empty_list(client):
    resp = client.get("/incidents/correlated")
    assert resp.status_code == 200
    assert resp.json() == []


def test_correlated_incident_id_is_stable_across_repeated_calls(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))

    first = client.get("/incidents/correlated").json()
    second = client.get("/incidents/correlated").json()

    assert first[0]["id"] == second[0]["id"]
    assert first[0]["status"] == "open"


def test_correlated_incidents_start_as_open(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    body = client.get("/incidents/correlated").json()
    assert body[0]["status"] == "open"


def test_acknowledge_incident_transitions_from_open_to_acknowledged(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    incident_id = client.get("/incidents/correlated").json()[0]["id"]

    resp = client.post(f"/incidents/correlated/{incident_id}/acknowledge")
    assert resp.status_code == 200
    assert resp.json()["status"] == "acknowledged"

    # Reflected back on the live correlated list too, not just the record itself.
    body = client.get("/incidents/correlated").json()
    assert body[0]["status"] == "acknowledged"


def test_resolve_incident_records_note_and_timestamp(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    incident_id = client.get("/incidents/correlated").json()[0]["id"]

    resp = client.post(f"/incidents/correlated/{incident_id}/resolve", json={"note": "Rolled back the bad deploy."})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolution_note"] == "Rolled back the bad deploy."
    assert body["resolved_at"] is not None


def test_resolve_incident_without_note_is_allowed(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    incident_id = client.get("/incidents/correlated").json()[0]["id"]

    resp = client.post(f"/incidents/correlated/{incident_id}/resolve", json={})
    assert resp.status_code == 200
    assert resp.json()["status"] == "resolved"
    assert resp.json()["resolution_note"] is None


def test_reopen_incident_clears_resolution(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    incident_id = client.get("/incidents/correlated").json()[0]["id"]
    client.post(f"/incidents/correlated/{incident_id}/resolve", json={"note": "fixed"})

    resp = client.post(f"/incidents/correlated/{incident_id}/reopen")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "open"
    assert body["resolution_note"] is None
    assert body["resolved_at"] is None


def test_resolve_unknown_incident_returns_404(client):
    resp = client.post("/incidents/correlated/999999/resolve", json={})
    assert resp.status_code == 404


def test_acknowledge_unknown_incident_returns_404(client):
    resp = client.post("/incidents/correlated/999999/acknowledge")
    assert resp.status_code == 404


def test_history_summary_counts_incidents_by_status(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    incident_id = client.get("/incidents/correlated").json()[0]["id"]
    client.post(f"/incidents/correlated/{incident_id}/resolve", json={})

    resp = client.get("/incidents/history/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_incidents"] == 1
    assert body["counts_by_status"]["resolved"] == 1
    assert body["counts_by_status"]["open"] == 0


def test_history_summary_computes_mean_time_to_resolution(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    incident_id = client.get("/incidents/correlated").json()[0]["id"]
    client.post(f"/incidents/correlated/{incident_id}/resolve", json={})

    resp = client.get("/incidents/history/summary")
    mttr = resp.json()["mean_time_to_resolution_minutes"]
    assert mttr is not None
    assert mttr >= 0


def test_history_summary_mttr_is_none_when_nothing_resolved(client):
    _upload_logs(client, _logs_csv(_burst_log_rows()))
    client.get("/incidents/correlated")  # persist it, but never resolve it

    resp = client.get("/incidents/history/summary")
    assert resp.json()["mean_time_to_resolution_minutes"] is None


def test_history_summary_with_no_incidents(client):
    resp = client.get("/incidents/history/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_incidents"] == 0
    assert body["mean_time_to_resolution_minutes"] is None


def test_delete_logs_removes_all_entries(client):
    _upload_logs(client, _VALID_LOGS_CSV)
    resp = client.delete("/incidents/logs")
    assert resp.status_code == 200
    assert resp.json()["rows_deleted"] == 3

    assert client.get("/incidents/logs").json() == []


def test_delete_logs_does_not_touch_metrics(client):
    _upload_logs(client, _VALID_LOGS_CSV)
    _upload_metrics(client, _VALID_METRICS_CSV)
    client.delete("/incidents/logs")

    assert client.get("/incidents/logs").json() == []
    assert len(client.get("/incidents/metrics").json()) == 3


def test_delete_logs_with_no_data_reports_zero(client):
    resp = client.delete("/incidents/logs")
    assert resp.status_code == 200
    assert resp.json()["rows_deleted"] == 0
