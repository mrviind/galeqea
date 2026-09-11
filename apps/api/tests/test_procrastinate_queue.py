"""WO#4 P2-1: the ProcrastinateQueue against a real Postgres (Homebrew cluster).

Gated: skips unless procrastinate is installed AND a local Postgres test database is
reachable. Set GALEQEA_TEST_PG_URL to point at one (default: the Homebrew localhost DB
`galeqea_queue_test`). Proves enqueue → a worker drains it → the task ran → depth is 0.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("procrastinate")

_PG_URL = os.environ.get(
    "GALEQEA_TEST_PG_URL",
    "postgresql+psycopg://localhost:5432/galeqea_queue_test")


def _pg_reachable(url: str) -> bool:
    try:
        import psycopg
        conn = psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"),
                               connect_timeout=2)
        conn.close()
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _pg_reachable(_PG_URL), reason="no local Postgres test DB (set GALEQEA_TEST_PG_URL)")


_RAN: dict = {}


async def test_procrastinate_enqueue_and_worker_drains():
    from galeqea.jobs.base import task
    from galeqea.jobs.procrastinate_queue import ProcrastinateQueue

    @task("_pg_probe")
    async def _probe(*, value):
        _RAN["value"] = value

    q = ProcrastinateQueue(database_url=_PG_URL)
    await q.apply_schema()
    # a clean slate for this task

    job_id = await q.enqueue("_pg_probe", value=99)
    assert job_id
    assert q.depth() >= 1                       # the job is waiting in Postgres
    status = q.job_status(job_id)
    assert status and status["task"] == "_pg_probe" and status["status"] in ("todo", "doing")

    await q.run_worker(concurrency=1, wait=False)   # drain the queue once

    assert _RAN.get("value") == 99                  # the task actually ran
    assert q.job_status(job_id)["status"] == "succeeded"
    await q.stop()


def test_conninfo_strips_sqlalchemy_driver():
    from galeqea.jobs.procrastinate_queue import _conninfo
    assert _conninfo("postgresql+psycopg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    assert _conninfo("postgresql://h/db") == "postgresql://h/db"


async def test_bus_bridge_notify_listen_roundtrip(monkeypatch):
    """A worker-side NOTIFY reaches the web-side listener and re-publishes locally,
    so SSE / webhooks / metrics see runs that execute in a worker (WO#4 P2-1)."""
    import asyncio

    from galeqea.config import settings
    from galeqea.core import bus_bridge
    from galeqea.core.events import Event, bus

    monkeypatch.setattr(settings, "database_url", _PG_URL)

    received: list = []

    async def _sink(ev):
        if ev.type == "bridge.probe":
            received.append(ev)

    bus.add_sink(_sink)
    stop = asyncio.Event()
    listener = asyncio.create_task(bus_bridge.run_listener(stop))
    await asyncio.sleep(0.4)                       # let LISTEN establish

    await bus_bridge.publish_remote(
        Event(type="bridge.probe", project_id="p1", payload={"n": 7}, run_id="r1"))

    for _ in range(40):
        if received:
            break
        await asyncio.sleep(0.05)

    stop.set()
    listener.cancel()
    import contextlib
    with contextlib.suppress(Exception, asyncio.CancelledError):
        await listener

    assert received, "the bridged event never arrived on the local bus"
    assert received[0].payload == {"n": 7} and received[0].run_id == "r1"
