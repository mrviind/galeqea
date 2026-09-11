"""Cross-process event bridge over Postgres LISTEN/NOTIFY (WO#4 P2-1).

When a run executes in a ``galeqea worker`` process, its ``RUN_*`` events land on that
process's in-memory bus, out of reach of the web process that serves the SSE stream,
delivers webhooks, and updates metrics. This bridges them: the **worker** NOTIFYs each
event to a Postgres channel, and the **web** process LISTENs and re-publishes them on
its own bus, so live progress, webhooks and metrics work exactly as they do in-process.

No loop is possible: only the worker installs the NOTIFY sink, and only the web process
runs the listener; the two roles never overlap. Only used on the Postgres ``full``
profile; the SQLite default runs everything in one process and needs no bridge.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from ..config import settings
from ..jobs.procrastinate_queue import _conninfo
from .events import Event, bus

log = logging.getLogger("galeqea.bus_bridge")

_CHANNEL = "galeqea_events"
_MAX_PAYLOAD = 7900  # Postgres NOTIFY caps the payload at 8000 bytes.


async def publish_remote(event: Event) -> None:
    """Bus sink installed on the **worker**: NOTIFY the event to the web process."""
    try:
        import psycopg
        body = json.dumps(event.as_dict())
        if len(body) > _MAX_PAYLOAD:
            slim = event.as_dict()
            slim["payload"] = {"_truncated": True}   # the SSE client refetches detail
            body = json.dumps(slim)
        conn = await psycopg.AsyncConnection.connect(
            _conninfo(settings.database_url), autocommit=True)
        try:
            await conn.execute("SELECT pg_notify(%s, %s)", (_CHANNEL, body))
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 - the run must never fail because a notify did
        log.debug("bus bridge: notify failed: %s", exc)


def install_worker_sink() -> None:
    """Call on worker start: every bus event is mirrored to the web process."""
    bus.add_sink(publish_remote)


def _event_from_dict(d: dict) -> Event:
    return Event(
        type=d["type"], project_id=d.get("project_id", ""), payload=d.get("payload", {}),
        id=d.get("id") or Event(type="", project_id="").id,
        ts=d.get("ts") or Event(type="", project_id="").ts,
        run_id=d.get("run_id"), session_id=d.get("session_id"), trace_id=d.get("trace_id"))


async def run_listener(stop: asyncio.Event) -> None:
    """Run on the **web** process: receive worker events and re-publish locally."""
    import psycopg
    try:
        conn = await psycopg.AsyncConnection.connect(
            _conninfo(settings.database_url), autocommit=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("bus bridge: listener could not connect: %s", exc)
        return
    await conn.execute(f"LISTEN {_CHANNEL}")
    log.info("bus bridge: web process listening on %s", _CHANNEL)
    try:
        async for note in conn.notifies():
            if stop.is_set():
                break
            try:
                await bus.publish(_event_from_dict(json.loads(note.payload)))
            except Exception as exc:  # noqa: BLE001
                log.debug("bus bridge: could not re-publish: %s", exc)
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
