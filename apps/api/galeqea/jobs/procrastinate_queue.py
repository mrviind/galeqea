"""Durable Postgres queue via Procrastinate (WO#4 P2-1, MIT).

Jobs land in a Postgres table and are picked up with ``LISTEN/NOTIFY`` + ``SKIP
LOCKED`` by one or more ``galeqea worker`` processes, so they survive a restart and
spread across workers. Only used on the Postgres ``full`` profile. The SQLite default
never imports procrastinate.
"""

from __future__ import annotations

import re

from ..config import settings
from .base import Queue, registered_tasks


def _conninfo(database_url: str) -> str:
    """SQLAlchemy URL → libpq conninfo procrastinate's psycopg connector wants."""
    return re.sub(r"^postgresql\+\w+://", "postgresql://", database_url)


class ProcrastinateQueue(Queue):
    kind = "procrastinate"

    def __init__(self, database_url: str | None = None) -> None:
        from procrastinate import App, PsycopgConnector

        self._url = database_url or settings.database_url
        self._connector = PsycopgConnector(conninfo=_conninfo(self._url))
        self._app = App(connector=self._connector)
        self._opened = False
        # Register every task under its own name so defer/worker share one registry.
        for name, fn in registered_tasks().items():
            self._app.task(name=name, pass_context=False)(_as_procrastinate_task(fn))

    async def _ensure_open(self) -> None:
        if not self._opened:
            await self._app.open_async()
            self._opened = True

    async def enqueue(self, task_name: str, /, **kwargs) -> str:
        await self._ensure_open()
        job_id = await self._app.tasks[task_name].defer_async(**kwargs)
        return str(job_id)

    async def cancel(self, job_id: str) -> bool:
        await self._ensure_open()
        try:
            ok = await self._app.job_manager.cancel_job_by_id_async(int(job_id))
            return bool(ok)
        except Exception:  # noqa: BLE001
            return False

    def _query(self, sql: str, params: tuple = ()):
        """Query the queue's OWN Postgres (not the app session), because the jobs live there."""
        import psycopg
        with psycopg.connect(_conninfo(self._url), connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchone()

    def job_status(self, job_id: str) -> dict | None:
        try:
            row = self._query(
                "SELECT id, task_name, status FROM procrastinate_jobs WHERE id = %s",
                (int(job_id),))
        except Exception:  # noqa: BLE001
            return None
        if not row:
            return None
        return {"id": str(row[0]), "task": row[1], "status": row[2]}

    def depth(self) -> int:
        try:
            row = self._query(
                "SELECT count(*) FROM procrastinate_jobs WHERE status IN ('todo','doing')")
            return int(row[0]) if row else 0
        except Exception:  # noqa: BLE001
            return 0

    def _schema_present(self) -> bool:
        try:
            row = self._query("SELECT to_regclass('public.procrastinate_jobs')")
            return bool(row and row[0])
        except Exception:  # noqa: BLE001
            return False

    async def apply_schema(self) -> None:
        """Create procrastinate's tables if they are not already there. Safe to call
        on every worker start. Procrastinate's own schema is not re-appliable, so we
        guard it with a presence check rather than catching mid-DDL errors."""
        await self._ensure_open()
        if self._schema_present():
            return
        await self._app.schema_manager.apply_schema_async()

    async def run_worker(self, concurrency: int | None = None, *, wait: bool = True) -> None:
        """Consume jobs, the body of ``galeqea worker``. ``wait=False`` drains the
        queue once and returns (used by tests)."""
        async with self._app.open_async():
            await self._app.run_worker_async(
                concurrency=concurrency or settings.worker_concurrency,
                install_signal_handlers=False, wait=wait,
            )

    async def stop(self) -> None:
        if self._opened:
            import contextlib
            with contextlib.suppress(Exception):
                await self._app.close_async()
            self._opened = False


def _as_procrastinate_task(fn):
    """Adapt a registered task (kwargs-only async fn) to procrastinate's signature."""
    async def _task(**kwargs):
        return await fn(**kwargs)
    _task.__name__ = getattr(fn, "__name__", "task")
    return _task
