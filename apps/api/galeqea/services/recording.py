"""Recording session lifecycle.

Start a session and a headed browser opens; a person uses the application; the
interactions arrive as runner events and are accumulated here. When the person
closes the window the accumulated stream is compressed into a proposal.

The proposal is a *proposal*. Nothing about a recording bypasses the approval
gate: the person who recorded the session is not thereby the person who approved
the test it produced, and a recorded step list has exactly the same standing as
one an agent wrote.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..core import audit
from ..core.events import Ev, Event, bus
from ..db import session_scope
from ..engine import record as compiler
from ..models import Project, RecordingSession, TestCase
from ..models.base import utcnow
from .requirements import persist_proposals

log = logging.getLogger("galeqea.recording")

#: Live capture buffers keyed by session id. Held in process because a recording
#: is one continuous browser session: writing 300 individual actions through the
#: database as they arrive would add nothing but contention.
_BUFFERS: dict[str, list[dict]] = {}
_TASKS: dict[str, asyncio.Task] = {}


async def start(
    db: Session,
    *,
    project_id: str,
    start_url: str = "",
    environment: str = "",
    title: str = "",
    max_actions: int = 300,
    max_minutes: int = 30,
    started_by: str | None = None,
) -> RecordingSession:
    project = db.get(Project, project_id)
    if project is None:
        raise ValueError(f"unknown project {project_id}")

    env = environment or project.default_environment or "default"
    base_url = (project.environments or {}).get(env, "")
    target = start_url or base_url
    if not target:
        raise ValueError(
            f"environment {env!r} has no URL configured and no start URL was given — "
            "recording needs somewhere to open"
        )

    session = RecordingSession(
        project_id=project_id,
        title=title,
        environment=env,
        base_url=base_url or target,
        start_url=target,
        status="starting",
        started_by=started_by,
        max_actions=max(5, min(max_actions, 2000)),
        max_minutes=max(1, min(max_minutes, 240)),
    )
    db.add(session)
    db.flush()

    audit.record(
        db, action="recording.started", actor_id=started_by, actor_kind="user",
        project_id=project_id, resource_type="recording_session", resource_id=session.id,
        detail={"start_url": target, "environment": env,
                "max_actions": session.max_actions, "max_minutes": session.max_minutes},
    )
    db.commit()

    _BUFFERS[session.id] = []
    _TASKS[session.id] = asyncio.create_task(_drive(session.id, project_id))
    return session


async def _drive(session_id: str, project_id: str) -> None:
    from ..engine.supervisor import RunSupervisor

    artifacts = Path(settings.artifacts_dir) / f"record-{session_id}"
    artifacts.mkdir(parents=True, exist_ok=True)

    with session_scope() as db:
        session = db.get(RecordingSession, session_id)
        plan = {
            "runId": f"record:{session_id}",
            "baseUrl": session.base_url,
            "browsers": ["chromium"],
            # Headed is not a preference here, it is the definition: there is a
            # person in this browser.
            "headless": False,
            "artifactsDir": str(artifacts),
            "tests": [],
            "record": {
                "id": session_id,
                "startUrl": session.start_url,
                "maxActions": session.max_actions,
                "maxMinutes": session.max_minutes,
                "discover": True,
            },
        }

    supervisor = RunSupervisor(provider=None)
    try:
        await supervisor.record(plan, session_id=session_id, project_id=project_id)
    except Exception as exc:  # noqa: BLE001
        log.exception("recording %s failed", session_id)
        with session_scope() as db:
            record = db.get(RecordingSession, session_id)
            if record:
                record.status = "error"
                record.stop_reason = f"{type(exc).__name__}: {exc}"
                record.finished_at = utcnow()
    finally:
        # Finalise defensively: a runner killed mid-session never emits
        # `record_end`, and losing an hour of someone's recording because the
        # process died is not an acceptable failure mode.
        await _finalise(session_id, reason="process ended")
        _BUFFERS.pop(session_id, None)
        _TASKS.pop(session_id, None)


# --------------------------------------------------------------------------- #
# Runner event handlers, called by the supervisor
# --------------------------------------------------------------------------- #
async def on_start(event: dict, *, project_id: str) -> None:
    session_id = event.get("sessionId", "")
    with session_scope() as db:
        session = db.get(RecordingSession, session_id)
        if session:
            session.status = "recording"
    await bus.publish(Event(
        type=Ev.RUN_LOG, project_id=project_id, run_id=f"record:{session_id}",
        payload={"level": "info", "message": f"recording started at {event.get('startUrl', '')}"},
    ))


async def on_action(event: dict, *, project_id: str) -> None:
    session_id = event.get("sessionId") or ""
    buffer = _BUFFERS.setdefault(session_id, [])
    buffer.append(event)
    # Persist periodically rather than per action: frequent enough that a crash
    # loses seconds of work, rare enough that typing does not become a write
    # storm on the database.
    if len(buffer) % 10 == 0:
        _persist_buffer(session_id, buffer)


async def on_end(event: dict, *, project_id: str, session_id: str | None = None) -> None:
    await _finalise(event.get("sessionId") or session_id or "",
                    reason=event.get("reason", "finished"))


def _persist_buffer(session_id: str, buffer: list[dict]) -> None:
    with session_scope() as db:
        session = db.get(RecordingSession, session_id)
        if session:
            session.actions = buffer[-2000:]


async def _finalise(session_id: str, *, reason: str) -> None:
    """Compress the captured stream into a proposal. Idempotent."""
    buffer = _BUFFERS.get(session_id) or []
    with session_scope() as db:
        session = db.get(RecordingSession, session_id)
        if session is None or session.status in {"finished", "promoted", "error"}:
            return
        actions = buffer or list(session.actions or [])
        events = compiler.parse_events(actions)
        proposal = compiler.build_proposal(
            events, base_url=session.base_url, title=session.title
        )
        session.actions = actions[-2000:]
        session.proposal = proposal
        session.stats = proposal["stats"]
        session.title = session.title or proposal["title"]
        session.status = "finished"
        session.stop_reason = reason
        session.finished_at = utcnow()
        project_id = session.project_id

        audit.record(
            db, action="recording.finished", actor_id=session.started_by, actor_kind="user",
            project_id=project_id, resource_type="recording_session", resource_id=session_id,
            detail={"reason": reason, **proposal["stats"]},
        )

    await bus.publish(Event(
        type=Ev.RUN_LOG, project_id=project_id, run_id=f"record:{session_id}",
        payload={"level": "info",
                 "message": f"recording finished ({reason}): "
                            f"{proposal['stats']['captured']} captured → "
                            f"{proposal['stats']['steps']} steps"},
    ))


# --------------------------------------------------------------------------- #
def stop(session_id: str) -> bool:
    """Ask a live recording to end. Returns whether there was one to stop."""
    task = _TASKS.get(session_id)
    if task is None or task.done():
        return False
    task.cancel()
    return True


def promote(db: Session, *, session_id: str, project_id: str, actor_id: str | None = None) -> TestCase:
    """Turn a finished recording into a PROPOSED test case.

    Idempotent by design: double-clicking Promote must not produce two tests, so
    a session that already has one returns it rather than creating another.
    """
    session = db.get(RecordingSession, session_id)
    if session is None or session.project_id != project_id:
        raise ValueError(f"unknown recording session {session_id}")
    if session.test_case_id:
        existing = db.get(TestCase, session.test_case_id)
        if existing is not None:
            return existing
    if not session.proposal:
        raise ValueError("this recording has not been compiled into a proposal yet")

    created = persist_proposals(
        db, project_id=project_id, proposals=[session.proposal], author_kind="user"
    )
    case = created[0]
    case.provenance = {**(case.provenance or {}), "recording_session_id": session.id}
    session.test_case_id = case.id
    session.status = "promoted"

    audit.record(
        db, action="recording.promoted", actor_id=actor_id, actor_kind="user",
        project_id=project_id, resource_type="test_case", resource_id=case.id,
        detail={"recording_session_id": session.id, "steps": len(case.steps),
                "status": case.status},
    )
    db.flush()
    return case


def list_sessions(db: Session, project_id: str, limit: int = 50) -> list[dict]:
    rows = db.execute(
        select(RecordingSession)
        .where(RecordingSession.project_id == project_id)
        .order_by(RecordingSession.created_at.desc())
        .limit(limit)
    ).scalars()
    return [summarise(r) for r in rows]


def summarise(session: RecordingSession, *, include_actions: bool = False) -> dict:
    out = {
        "id": session.id,
        "title": session.title,
        "status": session.status,
        "stop_reason": session.stop_reason,
        "environment": session.environment,
        "start_url": session.start_url,
        "base_url": session.base_url,
        "stats": session.stats or {},
        "proposal": session.proposal or {},
        "test_case_id": session.test_case_id,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "finished_at": session.finished_at.isoformat() if session.finished_at else None,
        "live_actions": len(_BUFFERS.get(session.id) or []),
    }
    if include_actions:
        out["actions"] = session.actions or []
    return out
