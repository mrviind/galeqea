"""WebSocket and SSE streaming.

One ordered event stream per project feeds the chat timeline, the live log pane
and the dashboards. Reconnects replay from a bounded ring buffer, so refreshing
the browser mid-run does not lose the run's history.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ...core.events import bus
from ...db import get_db

router = APIRouter(tags=["stream"])

HEARTBEAT_SECONDS = 25


@router.websocket("/ws/projects/{project_id}")
async def project_socket(websocket: WebSocket, project_id: str, replay: int = 50):
    await websocket.accept()
    queue = await bus.subscribe(project_id, replay=replay)

    async def pump() -> None:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event.as_dict(), default=str))

    async def heartbeat() -> None:
        # Keeps proxies from reaping an idle socket between runs.
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await websocket.send_text(json.dumps({"type": "ping"}))

    tasks = [asyncio.create_task(pump()), asyncio.create_task(heartbeat())]
    try:
        while True:
            # Client messages are only used for liveness today; reading keeps the
            # disconnect detectable.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001 - never let a socket error surface as a 500
        pass
    finally:
        for task in tasks:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await bus.unsubscribe(project_id, queue)


@router.get("/api/projects/{project_id}/events")
async def sse(project_id: str, replay: int = Query(default=25)):
    """Server-sent events, for clients that cannot hold a WebSocket open."""
    queue = await bus.subscribe(project_id, replay=replay)

    async def generator():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                    yield f"event: {event.type}\ndata: {json.dumps(event.as_dict(), default=str)}\n\n"
                except TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            await bus.unsubscribe(project_id, queue)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/projects/{project_id}/events/history")
def history(project_id: str, limit: int = 100, db: Session = Depends(get_db)):
    return {"events": [e.as_dict() for e in bus.history(project_id, limit=limit)]}
