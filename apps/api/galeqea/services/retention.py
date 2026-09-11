"""Artifact retention.

A project can cap how long its run artifacts (screenshots / video / trace / logs)
are kept: set ``retention_days`` in the project's settings and a daily sweep drops
the bytes (from disk or S3) and the ``Artifact`` rows older than that. Runs, results
and reports are unaffected; only the heavy binary evidence expires, which is what
storage cost and data-retention policy are actually about.

A project with no ``retention_days`` (the default) keeps its artifacts forever.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Artifact, Project, Run
from ..models.base import utcnow
from .artifacts import delete_artifact_bytes


def retention_days(project: Project) -> int | None:
    """The project's artifact retention in days, or None to keep forever."""
    raw = (project.settings or {}).get("retention_days")
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return None
    return days if days > 0 else None


def sweep(db: Session, *, now=None) -> dict:
    """Delete artifacts past every project's retention window. Returns a summary."""
    now = now or utcnow()
    summary: dict[str, int] = {}
    total = 0
    projects = db.execute(select(Project)).scalars().all()
    for project in projects:
        days = retention_days(project)
        if days is None:
            continue
        cutoff = now - timedelta(days=days)
        run_ids = select(Run.id).where(Run.project_id == project.id).scalar_subquery()
        stale = db.execute(
            select(Artifact).where(
                Artifact.run_id.in_(run_ids), Artifact.created_at < cutoff
            )
        ).scalars().all()
        for artifact in stale:
            delete_artifact_bytes(artifact)
            db.delete(artifact)
        if stale:
            summary[project.key] = len(stale)
            total += len(stale)
    db.commit()
    summary["_total"] = total
    return summary


def sweep_now() -> dict:
    """Entry point for the scheduler / CLI. Opens its own session."""
    from ..db import session_scope

    with session_scope() as db:
        return sweep(db)
