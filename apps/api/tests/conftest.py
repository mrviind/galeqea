import os
import tempfile
from pathlib import Path

import pytest

# A fresh home per session so tests never touch a developer's real install.
_TMP = tempfile.mkdtemp(prefix="galeqea-tests-")
os.environ["GALEQEA_HOME"] = _TMP
# Default to an isolated temp SQLite DB. CI overrides GALEQEA_DATABASE_URL to point
# at a throwaway Postgres so the same suite runs on both dialects; we honour a
# preset URL as long as it clearly isn't a developer's real install.
_PRESET_DB = os.environ.get("GALEQEA_DATABASE_URL", "")
if not _PRESET_DB or _PRESET_DB.startswith("sqlite"):
    os.environ["GALEQEA_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"


@pytest.fixture(scope="session", autouse=True)
def _enforce_db_isolation():
    """Fail the whole suite loudly if it resolved to a real install rather than
    the temp home: isolation by convention becomes isolation by assertion, so a
    stray ``~/.galeqea`` write (the source of junk approvals) can't slip in."""
    from galeqea.config import settings

    assert str(settings.home).startswith(_TMP), (
        f"tests must run against the isolated temp home, not {settings.home}")
    if settings.database_url.startswith("sqlite"):
        assert _TMP in settings.database_url, (
            f"the test DB must live under the temp home, not {settings.database_url}")
    else:
        # A CI Postgres URL is allowed, but never a developer's real install.
        assert ".galeqea" not in settings.database_url, "refusing to run tests against a real DB"
    yield


@pytest.fixture()
def db():
    from galeqea.db import SessionLocal, init_db

    init_db()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture()
def project(db):
    from galeqea.models import Project
    from galeqea.models.base import new_id

    record = Project(key=f"P{new_id()[:6].upper()}", name="Test project",
                     environments={"local": "http://localhost:8765"},
                     default_environment="local")
    db.add(record)
    db.commit()
    return record


@pytest.fixture()
def humans(db):
    """An author, an approver and a machine principal, unique per test.

    The database persists across tests in a session, so fixed emails would
    collide on the unique index and fail the *second* test that used them -
    a failure that looks like a product bug but is a fixture bug.
    """
    from galeqea.models import Role, User
    from galeqea.models.base import new_id

    suffix = new_id()[:8]
    author = User(email=f"author-{suffix}@x.io", name="Author", role=Role.AUTHOR.value)
    approver = User(email=f"approver-{suffix}@x.io", name="Approver", role=Role.APPROVER.value)
    agent = User(email=f"agent-{suffix}@x.io", name="Agent", role=Role.AGENT.value, is_machine=True)
    db.add_all([author, approver, agent])
    db.commit()
    return {"author": author, "approver": approver, "agent": agent}
