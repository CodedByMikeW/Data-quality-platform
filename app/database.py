from sqlalchemy import create_engine, insert
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import settings

# SQLite lets one writer in at a time, and by default a second one gives up after 5 seconds with "database is
# locked" - which the page sees as a 500. Writing down the problems found in a big file takes longer than that,
# so a second upload finishing meanwhile (or anyone loading a page the writer is holding up) waits its turn.
SQLITE_WAIT_SECONDS = 60

connect_args = (
    {"check_same_thread": False, "timeout": SQLITE_WAIT_SECONDS} if settings.database_url.startswith("sqlite") else {}
)

engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def insert_in_batches(db, model, rows, batch_size: int = 5_000) -> int:
    """
    Inserts `rows` (dicts of column values) into `model`'s table a batch at a
    time, and returns how many went in. Doesn't commit - the caller does.

    The batching is the point. Building one ORM object per row first costs about
    a kilobyte each, so a file that flags every row in every check needed
    hundreds of megabytes just to write down its own problems; a batch at a
    time keeps that flat however many rows there are.
    """
    total, batch = 0, []
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            db.execute(insert(model), batch)
            total += len(batch)
            batch = []
    if batch:
        db.execute(insert(model), batch)
        total += len(batch)
    return total
