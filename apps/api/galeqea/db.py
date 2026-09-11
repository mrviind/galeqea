"""Database session management.

SQLite by default so ``galeqea up`` works on a laptop with no services running;
PostgreSQL (+ pgvector) when ``GALEQEA_DATABASE_URL`` points at one.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings

log = logging.getLogger("galeqea.db")


class Base(DeclarativeBase):
    pass


def _make_engine():
    url = settings.database_url
    if url.startswith("sqlite"):
        eng = create_engine(
            url,
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=StaticPool if ":memory:" in url else None,
            future=True,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - driver glue
            cur = dbapi_conn.cursor()
            # WAL keeps the UI readable while a long run is writing steps.
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return eng
    return create_engine(url, pool_pre_ping=True, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope for background workers and the MCP server."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create every table from the models. Used by the test suite and as the
    build step for a brand-new DB; production goes through ``run_migrations``."""
    from . import models  # noqa: F401  (import registers mappers)

    Base.metadata.create_all(bind=engine)


def _alembic_cfg(url: str | None = None):
    from pathlib import Path

    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url or settings.database_url)
    return cfg


def run_migrations(eng=None) -> None:
    """Bring the database to head, the safe way for every starting point:

    * **Alembic-managed** (has ``alembic_version``) → upgrade to head.
    * **Fresh / empty** → upgrade to head (build the schema through migrations).
    * **Legacy** (built by an old ``create_all``, no ``alembic_version``) → add any
      missing tables and columns, then stamp head, so an existing install upgrades
      in place instead of needing a drop-and-recreate.
    """
    from . import models  # noqa: F401  (register tables)

    eng = eng or engine
    # On Postgres a whole fleet (web + workers + a migration Job) may boot at once;
    # a session-level advisory lock makes exactly one apply the migrations while the
    # rest wait and then see head. SQLite is single-writer and needs no lock.
    if eng.dialect.name == "postgresql":
        _run_pg_migrations(eng)
    else:
        _apply_migrations(eng)


def _run_pg_migrations(eng, *, attempts: int = 90, delay: float = 2.0) -> None:
    """Apply migrations under the advisory lock, tolerating a database that is not
    accepting authenticated connections yet.

    A freshly-created Postgres does its whole initdb (plus, for the pgvector image,
    the extension setup) with only a socket-only bootstrap server up; the role and
    its password aren't usable over TCP until that finishes, which on a cold first
    boot can take well over a minute. A managed database can likewise refuse
    connections for a while on cold start. Both surface as OperationalError, so we
    wait (here ~3 min) rather than crash the process; the alternative is a restart
    loop until the database happens to be ready."""
    import time

    from sqlalchemy.exc import OperationalError

    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with eng.connect() as conn:
                conn.exec_driver_sql("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_KEY,))
                try:
                    _apply_migrations(eng)
                finally:
                    conn.exec_driver_sql("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_KEY,))
            if attempt:
                log.info("database ready after %d attempt(s); migrations applied.", attempt + 1)
            return
        except OperationalError as exc:
            last = exc
            if attempt % 10 == 0:
                log.warning("database not ready yet (attempt %d/%d), retrying: %s",
                            attempt + 1, attempts, str(exc).splitlines()[0])
            time.sleep(delay)
    raise last  # type: ignore[misc]


#: A stable, arbitrary key for the migration advisory lock. Every process uses the
#: same one so they serialise against each other.
_MIGRATION_LOCK_KEY = 0x6A11_9EA2


def _alembic_url(eng) -> str:
    """The engine's URL for alembic, WITH the password.

    ``str(engine.url)`` masks the password as ``***``. Hand that to alembic and it
    connects with a literal ``***`` and fails auth on any password-protected
    Postgres. (Programmatic ``set_main_option`` values aren't %-interpolated, so a
    password containing ``%`` is safe.)"""
    return eng.url.render_as_string(hide_password=False)


def _apply_migrations(eng) -> None:
    from alembic import command
    from sqlalchemy import inspect

    tables = set(inspect(eng).get_table_names())
    cfg = _alembic_cfg(_alembic_url(eng))

    if "alembic_version" in tables:
        command.upgrade(cfg, "head")
    elif not (tables - {"alembic_version"}):
        command.upgrade(cfg, "head")
    else:
        Base.metadata.create_all(bind=eng)  # missing tables
        _reconcile_columns(eng)             # missing columns
        command.stamp(cfg, "head")


def _reconcile_columns(eng=None) -> None:
    """Add columns the models declare that a legacy table lacks (create_all never
    ALTERs existing tables). Added nullable, so the ORM's Python default fills new
    rows, and existing rows read as absent, which every caller already tolerates."""
    from sqlalchemy import inspect, text

    eng = eng or engine
    insp = inspect(eng)
    tables = set(insp.get_table_names())
    with eng.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                coltype = col.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'))
