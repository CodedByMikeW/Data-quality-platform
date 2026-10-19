import json
from typing import List, Optional

import pandas as pd
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException, Query
from sqlalchemy.orm import Session, defer

from app.config import settings
from app.database import get_db, insert_in_batches
from app import models, schemas
from app.checks.schema_changes import schema_of
from app.document_extraction import extract_text
from app.document_store import store_document, validated_access_level
from app.limits import read_csv_limited, read_upload, too_large, upload_slot
from app.pipeline import run_all_checks, compute_distribution_snapshot
from app.quality_report import build_report, data_filename, report_filename
from app.sample_datasets import SAMPLE_DATASET_PREFIX
from app.scoring import compute_score

router = APIRouter(prefix="/datasets", tags=["runs"])

# Saved rows are their own ceiling, not the upload limit: raising MAX_UPLOAD_MB lets bigger files be CHECKED, but
# what is kept in the database (and read back whole to open, search and download it) stays at the size that was measured.
# Bigger files still get their report, just not their rows. Keep MAX_SAVED_DATA_BYTES in app.js in step.
_MAX_SAVED_DATA_BYTES = 25 * 1024 * 1024
_SAMPLE_RUNS_KEPT = 10  # a sample can be run any number of times; only the latest few runs are kept
_ISSUES_LISTED_BY_DEFAULT = 5_000  # the affected rows of a check that one answer lists, unless asked for more
_ISSUES_LISTED_AT_MOST = 100_000


def _get_or_create_dataset(db: Session, name: str) -> models.Dataset:
    dataset = db.query(models.Dataset).filter_by(name=name).first()
    if dataset:
        return dataset
    dataset = models.Dataset(name=name)
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


def _read_check_csv(
    file: UploadFile, parse_error: str = "Could not parse CSV", columns: set[str] | None = None
) -> pd.DataFrame:
    """One uploaded CSV, within the size, row, column and cell limits (see app/limits.py)."""
    return read_csv_limited(
        read_upload(file),
        file.filename,
        max_rows=settings.max_check_rows,
        max_columns=settings.max_check_columns,
        max_cells=settings.max_check_cells,
        parse_error=parse_error,
        columns=columns,
    )


def _reference_columns_wanted(rules: object, n_files: int) -> list[set[str]]:
    """
    For each related file, the columns the relationship rules look it up by.

    A relationship check only asks a related file for the values in one column,
    so that is all that gets read: a related file with forty columns costs one
    column of memory, and a run with three of them costs little more than a run
    with none. A rule that doesn't make sense here (not a list of objects, an
    index that points at no file) is skipped and left for the checks to report,
    exactly as before.
    """
    wanted: list[set[str]] = [set() for _ in range(n_files)]
    if not isinstance(rules, list):
        return wanted
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        index, column = rule.get("reference_index"), rule.get("reference_column")
        # A negative index counts from the end, as it does when the checks pick the file (and True / False are 1 / 0 there too).
        if isinstance(index, int) and -n_files <= index < n_files and isinstance(column, str):
            wanted[index].add(column)
    return wanted


def _latest_document(db: Session, filename: str) -> models.Document | None:
    """
    The newest version of `filename`, without its text: a saved data file is up
    to 25 MB, and most of the time all that is wanted is its number and level.
    """
    return (
        db.query(models.Document)
        .options(defer(models.Document.text_content))
        .filter_by(filename=filename, is_latest=True)
        .first()
    )


def _already_saved(db: Session, latest: models.Document | None, text: str, access_level: str) -> bool:
    """
    True when `latest` is this very text at this very level, so saving it again
    would only make a copy. The lengths are compared first: the text itself
    (25 MB, for a saved data file) is only read back when they already agree.
    """
    if not latest or latest.access_level != access_level or latest.char_count != len(text):
        return False
    saved_text = db.query(models.Document.text_content).filter(models.Document.id == latest.id).scalar()
    return saved_text == text


def _forget_old_sample_runs(db: Session, dataset: models.Dataset) -> None:
    """
    Anyone can press "Try a sample" as often as they like, and every press is a
    run with its list of flagged rows. Only the latest few are kept, so the
    trend chart stays readable and the database doesn't fill up with them.
    """
    old_ids = [
        run_id
        for (run_id,) in db.query(models.CheckRun.id)
        .filter(models.CheckRun.dataset_id == dataset.id)
        .order_by(models.CheckRun.id.desc())
        .offset(_SAMPLE_RUNS_KEPT)
    ]
    if not old_ids:
        return
    db.query(models.CheckIssue).filter(models.CheckIssue.run_id.in_(old_ids)).delete(synchronize_session=False)
    db.query(models.CheckRun).filter(models.CheckRun.id.in_(old_ids)).delete(synchronize_session=False)
    db.commit()


@router.post("/{dataset_name}/runs", response_model=schemas.CheckRunOut, dependencies=[Depends(upload_slot)])
def create_run(
    dataset_name: str,
    file: UploadFile = File(...),
    reference_files: Optional[List[UploadFile]] = File(default=None),
    relationships: str = Form(default="[]"),
    db: Session = Depends(get_db),
):
    reference_files = reference_files or []
    if len(reference_files) > settings.max_reference_files:
        raise too_large(
            f"{len(reference_files)} related files is more than one check takes - "
            f"the limit is {settings.max_reference_files}."
        )

    df = _read_check_csv(file)

    if df.empty:
        raise HTTPException(status_code=400, detail="Uploaded file has no rows")

    try:
        relationship_rules = json.loads(relationships)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="relationships must be valid JSON")

    reference_datasets = []
    for ref_file, wanted in zip(reference_files, _reference_columns_wanted(relationship_rules, len(reference_files))):
        ref_df = _read_check_csv(
            ref_file, parse_error=f"Could not parse reference file '{ref_file.filename}'", columns=wanted
        )
        reference_datasets.append((ref_file.filename, ref_df))

    dataset = _get_or_create_dataset(db, dataset_name)
    previous_run = dataset.runs[-1] if dataset.runs else None
    previous_schema = previous_run.schema_snapshot if previous_run else None
    previous_distributions = previous_run.distribution_snapshot if previous_run else None

    relationship_feedback: list[dict] = []
    try:
        results = run_all_checks(
            df,
            previous_schema=previous_schema,
            previous_distributions=previous_distributions,
            reference_datasets=reference_datasets,
            relationship_rules=relationship_rules,
            relationship_feedback=relationship_feedback,
        )
    except KeyError as exc:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This CSV is missing a column the checks expect: {exc}. "
                "The current checks are built for the demo schema (customer_id, "
                "email, name) - try data/customers_seeded.csv, or a CSV with "
                "matching columns."
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Couldn't run checks on this file: {exc}")

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

    # A batch at a time, not an object per flagged row: a file where every row
    # fails several checks would otherwise need hundreds of MB to list them.
    insert_in_batches(
        db,
        models.CheckIssue,
        (
            {"run_id": run.id, "check_name": result.check_name, "row_key": key}
            for result in results
            for key in result.affected_row_keys
        ),
    )
    db.commit()

    if dataset_name.startswith(SAMPLE_DATASET_PREFIX):
        _forget_old_sample_runs(db, dataset)

    # Transient, not persisted - only this response reports it, so whoever
    # just submitted the relationship rules can see what happened to each
    # one instead of a rule silently doing nothing.
    run.relationship_feedback = relationship_feedback or None
    return run


def _save_uploaded_data(
    db: Session, dataset_name: str, data_file: UploadFile, access_level: str
) -> tuple[schemas.SavedDocumentOut | None, str | None]:
    """
    Files the CSV a check ran on as its own document, next to the report and at
    the same access level. Returns (saved, error) and never raises: by the time
    this runs the report is already saved, and a problem with the rows must not
    undo that - the page tells the person what was and wasn't kept.

    Saved without embeddings (see store_document): a table's rows are shown as
    a table and found by keyword search, not by meaning.
    """
    contents = data_file.file.read(_MAX_SAVED_DATA_BYTES + 1)
    if len(contents) > _MAX_SAVED_DATA_BYTES:
        limit_mb = _MAX_SAVED_DATA_BYTES // (1024 * 1024)
        return None, f"The data file is over {limit_mb} MB, so only the report was saved."

    _, text = extract_text("data.csv", contents, index=False)  # kept as rows, not searched by meaning: no length limit
    del contents  # the text is what gets kept; holding the raw bytes as well would double the memory
    if not text:
        return None, "The data file was empty, so only the report was saved."

    filename = data_filename(dataset_name)
    latest = _latest_document(db, filename)
    if _already_saved(db, latest, text, access_level):
        saved = schemas.SavedDocumentOut(
            document_id=latest.id, filename=latest.filename, version=latest.version, created=False
        )
        return saved, None

    try:
        doc = store_document(db, filename, "csv", text, access_level, embed=False)
    except Exception as exc:
        return None, f"The report was saved, but the data wasn't: {str(exc)[:200]}"
    return schemas.SavedDocumentOut(document_id=doc.id, filename=doc.filename, version=doc.version, created=True), None


@router.post(
    "/{dataset_name}/runs/{run_id}/knowledge-report",
    response_model=schemas.KnowledgeReportOut,
    dependencies=[Depends(upload_slot)],  # it can carry a 25 MB data file: saves take turns like checks do
)
def save_run_report_to_knowledge_search(
    dataset_name: str,
    run_id: int,
    access_level: str = Form(default="team"),
    data_file: Optional[UploadFile] = File(default=None),
    db: Session = Depends(get_db),
):
    """
    Saves a text summary of one run (score, per-check results, column names -
    never row values) to Knowledge Search at the chosen access level. If the
    CSV the check ran on is attached as `data_file`, its rows are saved too, as
    a second document at the same level, so whoever may open the report may
    open the data. (The run doesn't keep the rows itself, so the page sends
    them again here.)

    A separate request from the run itself, on purpose: the first save loads
    the search model, which is slow, and a check shouldn't wait on that - or
    fail because of it. Every run of the same dataset label saves under one
    filename, so re-runs show up as new versions of a single document.
    """
    access_level = validated_access_level(access_level)

    dataset = db.query(models.Dataset).filter_by(name=dataset_name).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    run = db.query(models.CheckRun).filter_by(id=run_id, dataset_id=dataset.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found for this dataset")

    previous_run = (
        db.query(models.CheckRun)
        .filter(models.CheckRun.dataset_id == dataset.id, models.CheckRun.id < run.id)
        .order_by(models.CheckRun.id.desc())
        .first()
    )

    filename = report_filename(dataset_name)
    text = build_report(dataset_name, run, previous_run)

    # Saving the same run's report twice at the same level (a double click, a
    # retry after a dropped connection) isn't a new version - hand back the
    # one already saved. A different level, or a different run, is a new version.
    latest = _latest_document(db, filename)
    if _already_saved(db, latest, text, access_level):
        doc, created = latest, False
    else:
        try:
            doc = store_document(db, filename, "text", text, access_level)
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Couldn't save the report to Knowledge Search: {str(exc)[:200]}"
            )
        created = True

    # Rows second: if the report couldn't be saved nothing is, and if the rows
    # can't be, the report stays and the response says why.
    data, data_error = None, None
    if data_file is not None:
        data, data_error = _save_uploaded_data(db, dataset_name, data_file, access_level)

    return schemas.KnowledgeReportOut(
        document_id=doc.id,
        filename=doc.filename,
        version=doc.version,
        access_level=doc.access_level,
        created=created,
        data=data,
        data_error=data_error,
    )


@router.get("/{dataset_name}/runs", response_model=list[schemas.CheckRunOut])
def list_runs(dataset_name: str, db: Session = Depends(get_db)):
    dataset = db.query(models.Dataset).filter_by(name=dataset_name).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")
    return dataset.runs


@router.get("/{dataset_name}/runs/{run_id}/issues/{check_name}", response_model=schemas.IssuesOut)
def get_issues(
    dataset_name: str,
    run_id: int,
    check_name: str,
    limit: int = Query(default=_ISSUES_LISTED_BY_DEFAULT, ge=1, le=_ISSUES_LISTED_AT_MOST),
    db: Session = Depends(get_db),
):
    # `count` is every row the check flagged; `row_keys` are the first `limit` of them. A check can flag a
    # million rows of a big file, and listing them all took 10 MB and over 200 MB of memory to build - to
    # show the first 50. Just the key column, too: an ORM object per row would cost far more than the strings.
    flagged = db.query(models.CheckIssue).filter(
        models.CheckIssue.run_id == run_id, models.CheckIssue.check_name == check_name
    )
    row_keys = [
        key
        for (key,) in flagged.with_entities(models.CheckIssue.row_key).order_by(models.CheckIssue.id).limit(limit)
    ]
    return schemas.IssuesOut(check_name=check_name, count=flagged.count(), row_keys=row_keys)
