"""In-process queue: asyncio tasks on the API event loop (WO#4 P2-1).

This IS the pre-queue behaviour: enqueueing a run schedules an ``asyncio.create_task``
exactly as before. It is the zero-config SQLite default and needs no external broker.
"""

from __future__ import annotations

import asyncio
import contextlib
import secrets

from .base import Queue, get_task


class InProcessQueue(Queue):
    kind = "inprocess"

    def __init__(self) -> None:
        self._jobs: dict[str, dict] = {}
        self._order: list[str] = []          # enqueue order, for position()

    async def enqueue(self, task_name: str, /, **kwargs) -> str:
        job_id = "job_" + secrets.token_hex(8)
        fn = get_task(task_name)
        self._jobs[job_id] = {
            "id": job_id, "task": task_name, "status": "queued",
            "run_id": kwargs.get("run_id"), "task_obj": None,
        }
        self._order.append(job_id)
        self._jobs[job_id]["task_obj"] = asyncio.create_task(
            self._run(job_id, fn, kwargs))
        return job_id

    async def _run(self, job_id: str, fn, kwargs: dict) -> None:
        self._jobs[job_id]["status"] = "running"
        try:
            await fn(**kwargs)
            self._jobs[job_id]["status"] = "done"
        except asyncio.CancelledError:
            self._jobs[job_id]["status"] = "cancelled"
            raise
        except Exception:  # noqa: BLE001 (the task logs its own detail)
            self._jobs[job_id]["status"] = "failed"
        finally:
            with contextlib.suppress(ValueError):
                self._order.remove(job_id)

    async def cancel(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job or job["status"] not in ("queued", "running"):
            return False
        obj = job.get("task_obj")
        if obj and not obj.done():
            obj.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await obj
            return True
        return False

    def job_status(self, job_id: str) -> dict | None:
        job = self._jobs.get(job_id)
        if not job:
            return None
        return {"id": job_id, "task": job["task"], "status": job["status"],
                "position": self.position(job_id)}

    def depth(self) -> int:
        return sum(1 for j in self._jobs.values() if j["status"] in ("queued", "running"))

    def position(self, job_id: str) -> int | None:
        waiting = [jid for jid in self._order
                   if self._jobs.get(jid, {}).get("status") == "queued"]
        return waiting.index(job_id) + 1 if job_id in waiting else None

    def running_run_ids(self) -> list[str]:
        return [j["run_id"] for j in self._jobs.values()
                if j.get("run_id") and j["status"] in ("queued", "running")]

    def run_task(self, run_id: str):
        """The asyncio Task executing a run, so a caller can await it (in-process)."""
        for j in self._jobs.values():
            if j.get("run_id") == run_id and j["status"] in ("queued", "running"):
                return j.get("task_obj")
        return None

    def run_job_id(self, run_id: str) -> str | None:
        for jid, j in self._jobs.items():
            if j.get("run_id") == run_id and j["status"] in ("queued", "running"):
                return jid
        return None

    async def stop(self) -> None:
        for job in list(self._jobs.values()):
            obj = job.get("task_obj")
            if obj and not obj.done():
                obj.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await obj
        self._jobs.clear()
        self._order.clear()
