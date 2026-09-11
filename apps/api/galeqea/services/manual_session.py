"""Tester-authored exploratory (SBTM) sessions.

A human runs the exploration; GaleQEA keeps the charter, a timebox, and a timestamped
trail of notes and bugs, then produces a session report. This reuses the
``ExplorationSession`` record (strategy = "manual"); the AI explorer and a hand-run
session are the same object seen two ways.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..models import ExplorationSession, Project
from ..models.base import utcnow


class ManualSessionError(RuntimeError):
    pass


def active(db: Session, project_id: str) -> ExplorationSession | None:
    return db.execute(select(ExplorationSession).where(
        ExplorationSession.project_id == project_id,
        ExplorationSession.strategy == "manual",
        ExplorationSession.status == "running")
        .order_by(ExplorationSession.created_at.desc())).scalars().first()


def start(db: Session, project: Project, *, charter: str, minutes: int = 30, actor=None) -> ExplorationSession:
    if active(db, project.id) is not None:
        raise ManualSessionError("an exploratory session is already running, end it first")
    session = ExplorationSession(
        project_id=project.id, charter=charter or "Exploratory session",
        strategy="manual", status="running", max_steps=0,
        trail=[{"kind": "meta", "timebox_minutes": int(minutes),
                "by": getattr(actor, "email", ""), "at": utcnow().isoformat()}])
    db.add(session)
    db.flush()
    audit.record(db, action="exploration.started", actor_id=getattr(actor, "id", None),
                 actor_label=getattr(actor, "email", ""), project_id=project.id,
                 resource_type="exploration_session", resource_id=session.id,
                 detail={"charter": session.charter, "strategy": "manual", "minutes": minutes})
    return session


def add_entry(db: Session, project: Project, *, kind: str, text: str) -> ExplorationSession:
    session = active(db, project.id)
    if session is None:
        raise ManualSessionError("no exploratory session is running")
    trail = list(session.trail or [])
    trail.append({"kind": kind, "text": text, "at": utcnow().isoformat()})
    session.trail = trail
    db.flush()
    return session


def end(db: Session, project: Project, *, actor=None) -> dict:
    session = active(db, project.id)
    if session is None:
        raise ManualSessionError("no exploratory session is running")
    session.status = "finished"
    session.finished_at = utcnow()
    rep = report(session)
    session.summary = (f"{rep['notes']} note(s), {rep['bugs']} bug(s) over "
                       f"{rep['elapsed_minutes']} min.")
    db.flush()
    audit.record(db, action="exploration.finished", actor_id=getattr(actor, "id", None),
                 project_id=project.id, resource_type="exploration_session",
                 resource_id=session.id, detail=rep)
    return {"ok": True, **rep}


def _meta(session: ExplorationSession) -> dict:
    for e in (session.trail or []):
        if e.get("kind") == "meta":
            return e
    return {}


def report(session: ExplorationSession) -> dict:
    entries = [e for e in (session.trail or []) if e.get("kind") in ("note", "bug")]
    meta = _meta(session)
    end_at = session.finished_at or utcnow()
    elapsed = max(int((end_at - session.created_at).total_seconds() // 60), 0)
    return {
        "session_id": session.id, "charter": session.charter,
        "status": session.status, "timebox_minutes": meta.get("timebox_minutes"),
        "elapsed_minutes": elapsed,
        "notes": sum(1 for e in entries if e["kind"] == "note"),
        "bugs": sum(1 for e in entries if e["kind"] == "bug"),
        "entries": entries,
    }


def session_card(session: ExplorationSession) -> dict:
    rep = report(session)
    return {"type": "exploratory_session_card", **rep}
