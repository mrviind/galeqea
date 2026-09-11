"""Job-queue package (WO#4 P2-1): one Queue interface, in-process or Procrastinate."""

from __future__ import annotations

from ..config import settings
from . import tasks as _tasks  # noqa: F401 (importing registers the tasks)
from .base import Queue, registered_tasks

_queue: Queue | None = None


def get_queue() -> Queue:
    """The process-wide queue, chosen from settings (SQLite → in-process,
    Postgres → Procrastinate). Built once."""
    global _queue
    if _queue is None:
        _queue = _build()
    return _queue


def _build() -> Queue:
    if settings.queue_kind == "procrastinate":
        from .procrastinate_queue import ProcrastinateQueue
        return ProcrastinateQueue()
    from .inprocess import InProcessQueue
    return InProcessQueue()


def reset_queue() -> None:
    """Drop the cached queue (tests)."""
    global _queue
    _queue = None


__all__ = ["Queue", "get_queue", "reset_queue", "registered_tasks"]
