import os
import tempfile
from pathlib import Path

import pytest

# A fresh home per session so tests never touch a developer's real install.
_TMP = tempfile.mkdtemp(prefix="galeqea-tests-")
os.environ["GALEQEA_HOME"] = _TMP
os.environ["GALEQEA_DATABASE_URL"] = f"sqlite:///{Path(_TMP) / 'test.db'}"


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
