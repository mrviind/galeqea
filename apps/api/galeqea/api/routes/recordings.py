"""Session recording: start, watch, promote."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project, RecordingSession, User
from ...services import recording as service
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/recordings", tags=["recordings"])


@router.get("")
def list_sessions(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    return service.list_sessions(db, project.id)


@router.post("")
async def start(
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    payload = payload or {}
    try:
        session = await service.start(
            db, project_id=project.id,
            start_url=payload.get("start_url", ""),
            environment=payload.get("environment", ""),
            title=payload.get("title", ""),
            max_actions=int(payload.get("max_actions") or 300),
            max_minutes=int(payload.get("max_minutes") or 30),
            started_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return service.summarise(session)


@router.get("/{session_id}")
def detail(
    session_id: str,
    include_actions: bool = False,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    session = db.get(RecordingSession, session_id)
    if session is None or session.project_id != project.id:
        raise HTTPException(404, "no such recording session")
    return service.summarise(session, include_actions=include_actions)


@router.post("/{session_id}/stop")
def stop(
    session_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    session = db.get(RecordingSession, session_id)
    if session is None or session.project_id != project.id:
        raise HTTPException(404, "no such recording session")
    stopped = service.stop(session_id)
    return {"stopped": stopped, "status": session.status}


@router.post("/{session_id}/promote")
def promote(
    session_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """File the recorded steps as a PROPOSED test case.

    Proposed, not approved. Having driven the browser is not the same as having
    reviewed the test that came out of it, and the gate does not make exceptions
    for humans who happen to have been present.
    """
    try:
        case = service.promote(db, session_id=session_id, project_id=project.id, actor_id=user.id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {
        "test": {"id": case.id, "key": case.key, "title": case.title, "status": case.status,
                 "steps": len(case.steps), "tags": case.tags, "rationale": case.rationale},
    }
