"""Database session management.

SQLite by default so ``galeqea up`` works on a laptop with no services running;
PostgreSQL (+ pgvector) when ``GALEQEA_DATABASE_URL`` points at one.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from .config import settings


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
    from . import models  # noqa: F401  (import registers mappers)

    Base.metadata.create_all(bind=engine)
