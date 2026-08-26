"""In-process pub/sub for live streaming.

The chat window, the log pane and the dashboards all read from one ordered
stream per project. Events are fanned out to WebSocket clients and, for
reconnects, replayed from a bounded ring buffer so a browser refresh mid-run
does not lose the timeline.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, field
from typing import Any

from ..models.base import new_id, utcnow

RING_SIZE = 500


@dataclass(slots=True)
class Event:
    type: str
    project_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("ev"))
    ts: str = field(default_factory=lambda: utcnow().isoformat())
    # Correlation handles so the UI can attach an event to the right surface.
    run_id: str | None = None
    session_id: str | None = None
    trace_id: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
        self._history: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=RING_SIZE))
        self._lock = asyncio.Lock()

    async def publish(self, event: Event) -> None:
        self._history[event.project_id].append(event)
        for queue in list(self._subscribers.get(event.project_id, ())):
            # A stalled browser tab must never block the test runner.
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)

    def publish_soon(self, event: Event) -> None:
        """Publish from synchronous code (the runner supervisor, appliers)."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._history[event.project_id].append(event)
            return
        loop.create_task(self.publish(event))

    async def subscribe(self, project_id: str, *, replay: int = 50) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers[project_id].add(queue)
        for event in list(self._history[project_id])[-replay:]:
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        return queue

    async def unsubscribe(self, project_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers[project_id].discard(queue)

    def history(self, project_id: str, *, limit: int = 100) -> list[Event]:
        return list(self._history[project_id])[-limit:]

    def subscriber_count(self, project_id: str) -> int:
        return len(self._subscribers.get(project_id, ()))


bus = EventBus()


# --------------------------------------------------------------------------- #
# Canonical event names. The UI switches on these, so they are part of the API.
# --------------------------------------------------------------------------- #
class Ev:
    CHAT_MESSAGE = "chat.message"
    CHAT_DELTA = "chat.delta"           # token stream
    CHAT_STATUS = "chat.status"         # timestamped step-by-step progress
    CHAT_BLOCK = "chat.block"           # rich card appended to a message

    AGENT_STARTED = "agent.started"
    AGENT_STEP = "agent.step"
    AGENT_TOOL_CALL = "agent.tool_call"
    AGENT_FINISHED = "agent.finished"
    AGENT_ERROR = "agent.error"

    RUN_QUEUED = "run.queued"
    RUN_STARTED = "run.started"
    RUN_PROGRESS = "run.progress"
    RUN_LOG = "run.log"                 # raw line for the live log pane
    RUN_TEST_STARTED = "run.test.started"
    RUN_TEST_FINISHED = "run.test.finished"
    RUN_STEP = "run.step"
    RUN_ARTIFACT = "run.artifact"
    RUN_FINISHED = "run.finished"
    RUN_HANDOFF = "run.handoff"         # browser paused, waiting for a human

    APPROVAL_REQUESTED = "approval.requested"
    APPROVAL_DECIDED = "approval.decided"

    HEAL_PROPOSED = "heal.proposed"
    RCA_READY = "rca.ready"
    ANOMALY = "anomaly.detected"
    NOTIFICATION = "notification"
