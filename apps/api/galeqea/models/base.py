from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


def new_id(prefix: str = "") -> str:
    raw = uuid.uuid4().hex[:24]
    return f"{prefix}_{raw}" if prefix else raw


def utcnow() -> datetime:
    return datetime.now(UTC)


class JSONish(TypeDecorator):
    """JSON column that degrades to TEXT on backends without a JSON type."""

    impl = JSON
    cache_ok = True


class UTCDateTime(TypeDecorator):
    """Timestamps that are always timezone-aware UTC, in both directions.

    SQLite has no native timestamp type and hands back naive datetimes even for
    a ``DateTime(timezone=True)`` column. Mixing those with aware values raises
    "can't subtract offset-naive and offset-aware datetimes" at the worst
    possible moment - in the middle of finishing a run. Normalising at the
    column boundary means no call site ever has to think about it.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class IdMixin:
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id())
