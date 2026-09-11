"""Admin bootstrap. A multi-user deployment is loginable on first boot: a password
from GALEQEA_ADMIN_PASSWORD, or a one-time setup link."""

from __future__ import annotations

import hashlib
import secrets

from fastapi.testclient import TestClient

from galeqea.config import settings
from galeqea.core.security import hash_password, verify_password
from galeqea.main import _setup_token_file, app
from galeqea.models import Role, User


def _owner(db, project):
    from galeqea.models.base import new_id
    o = User(email=f"admin-{new_id()[:8]}@corp.example", name="Admin",
             role=Role.OWNER, password_hash="")
    db.add(o)
    db.commit()
    return o


def _mint_token() -> str:
    token = secrets.token_urlsafe(16)
    _setup_token_file().write_text(hashlib.sha256(token.encode()).hexdigest())
    return token


def test_admin_password_from_env_is_verifiable():
    # The bootstrap path sets this hash directly; a login then verifies against it.
    encoded = hash_password("Sup3r-Secret!")
    assert verify_password("Sup3r-Secret!", encoded)
    assert not verify_password("wrong", encoded)


def test_setup_link_sets_the_admin_password_once(db, project):
    owner = _owner(db, project)
    token = _mint_token()
    client = TestClient(app)

    r = client.post("/api/setup", json={"token": token, "password": "corp-admin-pw"})
    assert r.status_code == 200 and r.json()["email"] == owner.email
    db.refresh(owner)
    assert owner.password_hash and verify_password("corp-admin-pw", owner.password_hash)
    assert not _setup_token_file().exists()  # the link is spent

    # A second use is refused: one-time only.
    again = client.post("/api/setup", json={"token": token, "password": "another-pw"})
    assert again.status_code == 410


def test_setup_rejects_bad_token_and_short_password(db, project):
    _owner(db, project)
    _mint_token()
    client = TestClient(app)
    assert client.post("/api/setup", json={"token": "nope", "password": "longenough"}).status_code == 403
    assert client.post("/api/setup", json={"token": "x", "password": "short"}).status_code == 400
    _setup_token_file().unlink(missing_ok=True)


def test_setup_page_serves_a_form_only_while_pending(db, project):
    client = TestClient(app)
    _setup_token_file().unlink(missing_ok=True)
    assert "already complete" in client.get("/setup").text
    _mint_token()
    assert "Set the admin password" in client.get("/setup").text
    _setup_token_file().unlink(missing_ok=True)


def test_migration_advisory_lock_key_is_stable():
    from galeqea.db import _MIGRATION_LOCK_KEY
    assert isinstance(_MIGRATION_LOCK_KEY, int)  # a fixed key so all processes serialise
    _ = settings  # config import smoke


def test_alembic_url_keeps_the_password():
    # Regression: str(engine.url) masks the password as ***, which alembic would
    # then use verbatim and fail auth on any Postgres. The migration path must use
    # the real password.
    from sqlalchemy import create_engine

    from galeqea.db import _alembic_url

    eng = create_engine("postgresql+psycopg://galeqea:s3cr3t@db:5432/galeqea")
    rendered = _alembic_url(eng)
    assert "s3cr3t" in rendered and "***" not in rendered
