from fastapi import APIRouter, HTTPException, Query

from app import schemas
from app.sample_datasets import PREVIEW_ROWS, SAMPLES, Sample, build_sample, other_kind

router = APIRouter(prefix="/samples", tags=["samples"])

_MAX_SEED = 2**31 - 1


def _sample_out(sample: Sample) -> schemas.SampleOut:
    return schemas.SampleOut(
        kind=sample.kind.kind,
        title=sample.kind.title,
        description=sample.kind.description,
        dataset_name=sample.dataset_name,
        filename=sample.filename,
        seed=sample.seed,
        row_count=len(sample.rows),
        columns=sample.columns,
        preview_rows=sample.rows[:PREVIEW_ROWS],
        csv=sample.to_csv(),
    )


@router.get("", response_model=list[schemas.SampleKindOut])
def list_samples():
    """The kinds of sample dataset there are."""
    return [schemas.SampleKindOut(kind=s.kind, title=s.title, description=s.description) for s in SAMPLES.values()]


@router.get("/random", response_model=schemas.SampleOut)
def random_sample(exclude: str | None = None):
    """
    A freshly generated sample of a random kind, as the CSV plus its first rows
    for the page's "View data" panel. `exclude` is the kind just shown, so
    pressing the button twice never gives the same kind twice in a row.
    """
    return _sample_out(build_sample(other_kind(exclude)))


@router.get("/{kind}", response_model=schemas.SampleOut)
def get_sample(kind: str, seed: int | None = Query(default=None, ge=1, le=_MAX_SEED)):
    """A sample of one kind. The same seed always gives the same file."""
    if kind not in SAMPLES:
        raise HTTPException(status_code=404, detail=f"No sample called '{kind}'. Try one of {sorted(SAMPLES)}.")
    return _sample_out(build_sample(kind, seed))
