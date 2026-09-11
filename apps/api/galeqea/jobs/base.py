"""The job-queue abstraction (WO#4 P2-1).

One interface, two backends: an **in-process** queue (asyncio tasks on the API event
loop, the zero-config SQLite default, byte-for-byte the pre-queue behaviour) and a
durable **Procrastinate** queue (Postgres LISTEN/NOTIFY + SKIP LOCKED) for the multi-
worker `full` profile. Background work (runs, retention, PDF render, webhook delivery)
is *enqueued* rather than fire-and-forgotten, so on Postgres it survives a restart
and spreads across workers, while on SQLite nothing external is ever required.
"""

from __future__ import annotations

import abc
from collections.abc import Awaitable, Callable

TaskFn = Callable[..., Awaitable]

#: task name -> async callable. Tasks take only JSON-serialisable kwargs and open
#: their own DB session, so the same function runs in-process or in a worker.
_REGISTRY: dict[str, TaskFn] = {}


def task(name: str) -> Callable[[TaskFn], TaskFn]:
    """Register an async function as an enqueueable task."""
    def register(fn: TaskFn) -> TaskFn:
        _REGISTRY[name] = fn
        return fn
    return register


def registered_tasks() -> dict[str, TaskFn]:
    return dict(_REGISTRY)


def get_task(name: str) -> TaskFn:
    if name not in _REGISTRY:
        raise KeyError(f"no such job task: {name!r} (have {sorted(_REGISTRY)})")
    return _REGISTRY[name]


class Queue(abc.ABC):
    """enqueue / cancel / status / depth. Fire-and-forget by contract: a task's
    result is its side effects, never a return value the caller awaits."""

    kind: str = "base"

    @abc.abstractmethod
    async def enqueue(self, task_name: str, /, **kwargs) -> str:
        """Schedule a task to run as soon as a worker is free. Returns a job id."""

    @abc.abstractmethod
    async def cancel(self, job_id: str) -> bool:
        """Best-effort cancel. True if the job was cancelled before completing."""

    @abc.abstractmethod
    def job_status(self, job_id: str) -> dict | None:
        """{id, task, status, position?} or None if unknown."""

    @abc.abstractmethod
    def depth(self) -> int:
        """Jobs waiting or running right now (the queue-depth metric)."""

    def position(self, job_id: str) -> int | None:
        """1-based place in line among still-queued jobs, or None."""
        return None

    async def start(self) -> None:  # noqa: B027 - optional lifecycle hook, default no-op
        """Begin consuming (a no-op for in-process; starts the worker for procrastinate)."""

    async def stop(self) -> None:  # noqa: B027 - optional lifecycle hook, default no-op
        """Stop consuming and release resources."""
