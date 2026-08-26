"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from ..core.security import current_user  # noqa: F401  (re-exported)
from ..db import get_db
from ..models import Project, User


def get_project(
    project_id: str = Path(...), db: Session = Depends(get_db)
) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        # Accept the human-facing key as well as the id - the chat, the CLI and
        # the MCP server all naturally refer to projects by key.
        from sqlalchemy import select

        project = db.execute(
            select(Project).where(Project.key == project_id.upper())
        ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown project {project_id!r}")
    return project


def require_project_access(
    project: Project = Depends(get_project), user: User = Depends(current_user)
) -> Project:
    return project
