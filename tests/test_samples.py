"""
"Try a sample": the made-up business datasets a visitor can check with one click.

What is under test: what the API hands the page (the CSV plus its first rows),
that the same seed always gives the same file, that a sample runs through the
real checks and each one finds the problems it was built to have - and nothing
else, since a check flagging a column nobody put a problem in would look like
the checker being wrong. A sample is a real run, so repeat runs of one kind
must not set off the drift and schema checks, and old sample runs are pruned.
"""
import functools
import io
import re

import pandas as pd
import pytest

from app.pipeline import run_all_checks
from app.sample_datasets import PREVIEW_ROWS, SAMPLES, build_sample, other_kind
from app.scoring import compute_score

KINDS = sorted(SAMPLES)

# The columns each kind is meant to have trouble in. A check may flag these (a
# date column isn't odd on every seed) but no other column...
FORMAT_COLUMNS = {
    "orders": {"order_id", "customer_email", "order_date"},
    "customers": {"customer_id", "email", "phone", "signup_date"},
    "employees": {"employee_id", "work_email", "hire_date"},
    "invoices": {"invoice_id", "invoice_date", "due_date"},
    "inventory": {"product_id", "last_counted"},
    "tickets": {"ticket_id", "opened_at", "customer_email"},
}
OUTLIER_COLUMNS = {
    "orders": {"order_total"},
    "customers": {"lifetime_value"},
    "employees": {"salary"},
    "invoices": {"amount"},
    "inventory": {"units_in_stock", "unit_price"},
    "tickets": {"resolution_minutes"},
}
# Blanks are put in exactly these columns (the Missing Values check counts them per column).
MISSING_COLUMNS = {
    "orders": {"customer_email", "order_date"},
    "customers": {"phone", "email"},
    "employees": {"work_email", "hire_date"},
    "invoices": {"vendor", "due_date"},
    "inventory": {"product_name", "last_counted"},
    "tickets": {"customer_email", "agent"},
}
MONEY_COLUMNS = {"orders": "order_total", "customers": "lifetime_value", "invoices": "amount", "inventory": "unit_price"}
WHOLE_NUMBER_COLUMNS = {"orders": "items", "employees": "salary", "inventory": "units_in_stock", "tickets": "resolution_minutes"}
# ...except unexpected values, where these columns - exactly these - are flagged.
UNEXPECTED_COLUMNS = {
    "orders": {"status", "channel"},
    "customers": {"plan", "country"},
    "employees": {"department", "status"},
    "invoices": {"currency", "status"},
    "inventory": {"category", "warehouse"},
    "tickets": {"priority", "status"},
}
# What each kind's description promises, so these checks find something whatever the seed.
ALWAYS_FOUND = {
    "orders": {"missing_values", "invalid_formats", "unexpected_values"},
    "customers": {"missing_values", "invalid_formats", "duplicates"},
    "employees": {"outliers", "invalid_formats", "unexpected_values"},
    "invoices": {"duplicates", "outliers", "unexpected_values"},
    "inventory": {"missing_values", "outliers", "unexpected_values"},
    "tickets": {"missing_values", "outliers", "unexpected_values"},
}
SEEDS = (1, 2, 3, 4)


def _flagged_columns(result) -> set[str]:
    return {column for column, found in result.details["per_column"].items() if found["affected_rows"] > 0}


@functools.lru_cache(maxsize=None)
def _checked(kind, seed):
    """(each check's result by name, the score) for one sample - computed once, the checks are the slow part."""
    df = pd.read_csv(io.StringIO(build_sample(kind, seed).to_csv()))
    results = run_all_checks(df)
    return {result.check_name: result for result in results}, compute_score(results)["score"]


def _run(client, dataset, sample):
    files = {"file": (sample.filename, sample.to_csv().encode(), "text/csv")}
    return client.post(f"/datasets/{dataset}/runs", files=files)


# ---------------------------------------------------------------- what the page gets


def test_the_kinds_are_listed(client):
    resp = client.get("/samples")
    assert resp.status_code == 200
    kinds = resp.json()
    assert sorted(k["kind"] for k in kinds) == KINDS
    assert len(KINDS) >= 5, "enough variety that a visitor doesn't see the same thing twice"
    for k in kinds:
        assert k["title"] and k["description"]


def test_a_random_sample_comes_with_everything_the_page_needs(client):
    resp = client.get("/samples/random")
    assert resp.status_code == 200
    body = resp.json()

    assert body["kind"] in SAMPLES
    assert body["dataset_name"] == f"sample-{body['kind']}"
    assert body["filename"] == f"sample-{body['kind']}.csv"
    assert body["title"] == SAMPLES[body["kind"]].title
    assert body["description"] == SAMPLES[body["kind"]].description

    # The CSV is the whole dataset; the page checks that very text.
    table = pd.read_csv(io.StringIO(body["csv"]), dtype=str, keep_default_na=False)
    assert list(table.columns) == body["columns"] == list(SAMPLES[body["kind"]].columns)
    assert len(table) == body["row_count"]
    assert SAMPLES[body["kind"]].rows[0] <= body["row_count"] <= 3000

    # The "View data" panel shows the first rows, exactly as they are in the CSV (blanks stay blank).
    assert len(body["preview_rows"]) == min(PREVIEW_ROWS, body["row_count"])
    assert body["preview_rows"] == table.head(PREVIEW_ROWS).values.tolist()


def test_the_preview_is_the_first_hundred_rows_of_a_bigger_file(client):
    body = client.get("/samples/orders", params={"seed": 5}).json()
    assert body["row_count"] > PREVIEW_ROWS
    assert len(body["preview_rows"]) == PREVIEW_ROWS == 100


def test_the_same_seed_gives_the_same_file(client):
    first = client.get("/samples/invoices", params={"seed": 7}).json()
    again = client.get("/samples/invoices", params={"seed": 7}).json()
    other = client.get("/samples/invoices", params={"seed": 8}).json()
    assert first["seed"] == 7
    assert first["csv"] == again["csv"]
    assert first["csv"] != other["csv"]


def test_a_random_sample_is_a_different_file_each_time(client):
    seeds = {client.get("/samples/random").json()["seed"] for _ in range(4)}
    assert len(seeds) > 1


def test_the_next_sample_is_never_the_kind_just_shown():
    for just_shown in KINDS:
        assert all(other_kind(just_shown) != just_shown for _ in range(200))


def test_every_kind_comes_up_and_an_unknown_kind_to_skip_is_ignored():
    assert {other_kind(None) for _ in range(300)} == set(KINDS)
    assert other_kind("nonsense") in KINDS


def test_the_random_endpoint_skips_the_kind_it_is_told_to(client, monkeypatch):
    asked = []

    def small_sample(kind, seed=None, rows=None, today=None):
        asked.append(kind)
        return build_sample(kind, 1, rows=40)  # small, so sixty of them take no time

    monkeypatch.setattr("app.routers.samples.build_sample", small_sample)
    for _ in range(60):
        assert client.get("/samples/random", params={"exclude": "orders"}).status_code == 200

    assert "orders" not in asked
    assert len(set(asked)) >= 3  # and the rest do come up


def test_an_unknown_kind_is_a_404_that_names_the_real_ones(client):
    resp = client.get("/samples/not-a-sample")
    assert resp.status_code == 404
    assert "orders" in resp.json()["detail"]


@pytest.mark.parametrize("seed", ["0", "-3", "abc", "2147483648"])
def test_a_bad_seed_is_refused(client, seed):
    assert client.get("/samples/orders", params={"seed": seed}).status_code == 422


# ---------------------------------------------------------------- what the checks find


@pytest.mark.parametrize("kind", KINDS)
def test_each_kind_has_its_problems_and_nothing_flagged_by_accident(kind):
    for seed in SEEDS:
        found, _ = _checked(kind, seed)

        for name in ALWAYS_FOUND[kind]:
            assert found[name].affected_rows > 0, f"{kind} seed {seed}: {name} found nothing"

        blanks = {c for c, n in found["missing_values"].details["null_counts_by_column"].items() if n > 0}
        assert blanks == MISSING_COLUMNS[kind], f"{kind} seed {seed}: blanks in {sorted(blanks)}"
        assert _flagged_columns(found["invalid_formats"]) <= FORMAT_COLUMNS[kind], f"{kind} seed {seed}"
        assert _flagged_columns(found["outliers"]) <= OUTLIER_COLUMNS[kind], f"{kind} seed {seed}"
        assert _flagged_columns(found["unexpected_values"]) == UNEXPECTED_COLUMNS[kind], f"{kind} seed {seed}"


@pytest.mark.parametrize("kind", KINDS)
def test_each_problem_touches_a_few_percent_of_the_rows_not_a_big_part_of_them(kind):
    for seed in SEEDS:
        found, _ = _checked(kind, seed)
        for name, result in found.items():
            share = result.affected_rows / len(build_sample(kind, seed).rows)
            assert share <= 0.10, f"{kind} seed {seed}: {name} flags {share:.0%} of the rows"


def _cells(kind, seed, column):
    sample = build_sample(kind, seed)
    position = sample.columns.index(column)
    return [row[position] for row in sample.rows if row[position] != ""]


@pytest.mark.parametrize("kind", sorted(MONEY_COLUMNS))
def test_amounts_are_written_with_two_decimals(kind):
    for seed in SEEDS:
        bad = [v for v in _cells(kind, seed, MONEY_COLUMNS[kind]) if not re.fullmatch(r"\d+\.\d{2}", v)]
        assert not bad, f"{kind} seed {seed}: {bad[:3]}"


@pytest.mark.parametrize("kind", sorted(WHOLE_NUMBER_COLUMNS))
def test_counts_and_salaries_are_whole_numbers(kind):
    for seed in SEEDS:
        bad = [v for v in _cells(kind, seed, WHOLE_NUMBER_COLUMNS[kind]) if not re.fullmatch(r"\d+", v)]
        assert not bad, f"{kind} seed {seed}: {bad[:3]}"


def test_no_sample_has_a_negative_number():
    """Stock below zero or a negative price would look like a mistake nobody put there on purpose."""
    def negatives(kind, seed):
        sample = build_sample(kind, seed)
        return [v for row in sample.rows for v in row if re.fullmatch(r"-\d+(\.\d+)?", v)]

    for kind in KINDS:
        for seed in SEEDS:
            assert not negatives(kind, seed), f"{kind} seed {seed}"
    for kind in ("inventory", "invoices"):  # cheap to build, and where a small spread over a small mean could dip below zero
        for seed in range(5, 61):
            assert not negatives(kind, seed), f"{kind} seed {seed}"


@pytest.mark.parametrize("kind", KINDS)
def test_a_sample_is_worth_showing_off(kind):
    """Problems to find, but not a wreck: the score lands where a real, slightly messy file would."""
    for seed in SEEDS:
        _, score = _checked(kind, seed)
        assert 85 <= score < 100, f"{kind} seed {seed}: score {score}"


# ---------------------------------------------------------------- running it for real


def test_a_sample_runs_through_the_api_like_any_upload(client):
    resp = _run(client, "sample-tickets", build_sample("tickets", 3))
    assert resp.status_code == 200
    run = resp.json()
    assert run["row_count"] > 1000
    assert run["breakdown"]["missing_values"]["affected_rows"] > 0
    # The flagged rows can be opened, as for any dataset.
    issues = client.get(f"/datasets/sample-tickets/runs/{run['id']}/issues/missing_values").json()
    assert issues["count"] == run["breakdown"]["missing_values"]["affected_rows"]


@pytest.mark.parametrize("kind", KINDS)
def test_running_the_same_kind_again_does_not_set_off_drift_or_schema_checks(client, kind):
    """Each click is a fresh random file under the same label, so run 2 is compared with run 1."""
    for seed in (1, 2, 3):
        run = _run(client, f"sample-{kind}", build_sample(kind, seed)).json()
        for name in ("schema_changes", "drift"):
            if name in run["breakdown"]:
                assert run["breakdown"][name]["pass_rate"] == 1.0, f"{kind} seed {seed}: {name} {run['breakdown'][name]}"


def test_a_sample_keeps_only_its_latest_ten_runs(client):
    first = _run(client, "sample-orders", build_sample("orders", 1, rows=200)).json()
    flagged = first["breakdown"]["missing_values"]["affected_rows"]
    assert flagged > 0
    assert client.get(f"/datasets/sample-orders/runs/{first['id']}/issues/missing_values").json()["count"] == flagged

    for seed in range(2, 13):
        assert _run(client, "sample-orders", build_sample("orders", seed, rows=200)).status_code == 200

    runs = client.get("/datasets/sample-orders/runs").json()
    assert len(runs) == 10
    assert first["id"] not in [r["id"] for r in runs]
    # The old run's list of flagged rows went with it.
    assert client.get(f"/datasets/sample-orders/runs/{first['id']}/issues/missing_values").json()["count"] == 0
    # What stays is the newest.
    assert runs[-1]["id"] == max(r["id"] for r in runs)


def test_only_sample_labels_are_pruned(client):
    for seed in range(1, 13):
        assert _run(client, "my-orders", build_sample("orders", seed, rows=100)).status_code == 200
    assert len(client.get("/datasets/my-orders/runs").json()) == 12
