"""Schema migrations: a fresh DB builds at head, and a legacy (create_all) DB
upgrades *in place* instead of needing a drop-and-recreate.

The second test is the one that matters for anyone who already installed: a
Slice-1-era `journeys` table without the `completed` column must gain it.
"""

from __future__ import annotations

from sqlalchemy import create_engine, inspect, text

from galeqea import models  # noqa: F401  (register tables)
from galeqea.db import Base, run_migrations


def _columns(eng, table):
    return {c["name"] for c in inspect(eng).get_columns(table)}


def test_fresh_db_builds_the_schema_at_head(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    run_migrations(eng)
    tables = set(inspect(eng).get_table_names())
    assert "journeys" in tables
    assert "alembic_version" in tables          # migrations ran, not create_all
    assert "completed" in _columns(eng, "journeys")


def test_legacy_create_all_db_upgrades_in_place(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    Base.metadata.create_all(bind=eng)          # simulate an old create_all install
    with eng.begin() as conn:
        conn.execute(text("ALTER TABLE journeys DROP COLUMN completed"))  # Slice-1-era shape
    assert "completed" not in _columns(eng, "journeys")

    run_migrations(eng)                          # legacy path: reconcile + stamp

    assert "completed" in _columns(eng, "journeys")   # column added, no data lost
    assert "alembic_version" in inspect(eng).get_table_names()
