from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.database import Base, engine, SessionLocal
from app import models
from app.checks.schema_changes import schema_of
from app.limits import BodySizeLimitMiddleware, public_limits
from app.pipeline import run_all_checks, compute_distribution_snapshot
from app.scoring import compute_score
from app.routers import runs, documents, incidents, samples


def _save_run(db, dataset, df, results):
    scored = compute_score(results)
    run = models.CheckRun(
        dataset_id=dataset.id,
        score=scored["score"],
        breakdown=scored["breakdown"],
        row_count=len(df),
        schema_snapshot=schema_of(df),
        distribution_snapshot=compute_distribution_snapshot(df),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    issue_rows = [
        models.CheckIssue(run_id=run.id, check_name=result.check_name, row_key=key)
        for result in results
        for key in result.affected_row_keys
    ]
    if issue_rows:
        db.bulk_save_objects(issue_rows)
        db.commit()
    return run


def seed_demo_data_if_empty():
    """
    Runs once at startup. If the datasets table is already non-empty (real
    data, local or on a persistent disk), this does nothing - it never
    touches existing data.

    If it's empty - a brand new deploy, or a free-tier instance that lost
    its disk on restart - this seeds enough to demonstrate all eight checks,
    not just the ones a single upload can show on its own:
      - customers, run 1: missing values, duplicates, invalid formats,
        outliers, and unexpected values - everything a single upload can
        show by itself.
      - customers, run 2: a later upload of the same dataset with a real
        added column and a shifted lifetime_value, so schema_changes and
        drift have a prior run to actually compare against and catch.
      - orders: a second, related dataset with some order rows pointing at
        a customer_id that doesn't exist - broken_relationships needs a
        second dataset to check against, so it can't be shown on customers
        alone.
    """
    from scripts.seed_dataset import generate_clean_customers, generate_orders, corrupt_dataset

    db = SessionLocal()
    try:
        if db.query(models.Dataset).count() > 0:
            return

        # Smaller than the standalone seed script's default (20k rows takes
        # ~5s to generate via Faker) - this only needs to prove the demo
        # works on a cold start, not produce the biggest possible numbers.
        clean = generate_clean_customers(n=3000)
        corrupted, _ = corrupt_dataset(clean)

        customers_dataset = models.Dataset(name="customers")
        db.add(customers_dataset)
        db.commit()
        db.refresh(customers_dataset)

        run_1_results = run_all_checks(corrupted)
        run_1 = _save_run(db, customers_dataset, corrupted, run_1_results)

        changed = corrupted.copy()
        changed["loyalty_score"] = (changed["lifetime_value"] / 10).round(1)
        changed["lifetime_value"] = changed["lifetime_value"] + 250

        run_2_results = run_all_checks(
            changed,
            previous_schema=run_1.schema_snapshot,
            previous_distributions=run_1.distribution_snapshot,
        )
        _save_run(db, customers_dataset, changed, run_2_results)

        orders = generate_orders(clean, n=500)
        orders_dataset = models.Dataset(name="orders")
        db.add(orders_dataset)
        db.commit()
        db.refresh(orders_dataset)

        orders_results = run_all_checks(
            orders,
            reference_datasets=[("customers", clean)],
            relationship_rules=[
                {"column": "customer_id", "reference_index": 0, "reference_column": "customer_id"}
            ],
        )
        _save_run(db, orders_dataset, orders, orders_results)
    finally:
        db.close()


def seed_incident_demo_data_if_empty():
    """Same pattern as seed_demo_data_if_empty(): only runs on a genuinely
    empty log_entries table, so it never touches real ingested data."""
    from scripts.seed_incidents import generate_incident_demo_data

    db = SessionLocal()
    try:
        if db.query(models.LogEntry).count() > 0:
            return

        log_rows, metric_rows = generate_incident_demo_data()
        db.bulk_save_objects(models.LogEntry(**row) for row in log_rows)
        db.bulk_save_objects(models.MetricPoint(**row) for row in metric_rows)
        db.commit()
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    seed_demo_data_if_empty()
    seed_incident_demo_data_if_empty()
    yield


app = FastAPI(
    title="Enterprise Data Quality Platform",
    version="0.1.0",
    description="Runs configurable data-quality checks against uploaded datasets and scores the result.",
    lifespan=lifespan,
)

# Refuses a request body bigger than any upload form could send, before the
# file is read - see app/limits.py for this and the per-file limits.
app.add_middleware(BodySizeLimitMiddleware)

app.include_router(runs.router)
app.include_router(documents.router)
app.include_router(incidents.router)
app.include_router(samples.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/limits")
def limits():
    """The upload limits, so the page can turn a too-big file away before sending it."""
    return public_limits()


# Registered last, and at "/": Starlette matches routes in registration
# order, so /health and /datasets/... above are always matched first.
# Only requests that don't match an API route fall through to serve the
# site's static files - this is what makes the whole thing (marketing
# page + embedded dashboard) the thing you land on at the root URL.
app.mount("/", StaticFiles(directory="app/static", html=True), name="site")
