import os
import sys

# Must happen before any `app.*` import: app.config reads DATABASE_URL at
# import time, so this keeps the test suite off your real dqp.db file.
os.environ["DATABASE_URL"] = "sqlite:///./test_dqp.db"
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    """
    Holds the confidential access code and its ID number, so it may view as any
    role - which is what every test written before access codes existed
    assumed, since they pick a role with ?role=. Tests about the codes
    themselves use anonymous_client and send exactly the code they mean to.
    """
    return TestClient(
        app,
        headers={
            "X-Access-Code": settings.confidential_access_code,
            "X-Access-Id": settings.confidential_id_number,
        },
    )


@pytest.fixture
def anonymous_client():
    """A visitor who hasn't entered any access code."""
    return TestClient(app)
