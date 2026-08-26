from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import audit
from ...db import get_db
from ...models import Project, Role, User
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectIn(BaseModel):
    key: str = Field(min_length=2, max_length=16)
    name: str
    description: str = ""
    environments: dict[str, str] = Field(default_factory=dict)
    default_environment: str = "default"
    settings: dict = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: str
    key: str
    name: str
    description: str
    environments: dict
    default_environment: str
    settings: dict
    archived: bool


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.execute(select(Project).where(Project.archived.is_(False))).scalars()
    return [ProjectOut(**{c.name: getattr(p, c.name) for c in Project.__table__.columns
                          if c.name in ProjectOut.model_fields}) for p in rows]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(
    payload: ProjectIn, db: Session = Depends(get_db), user: User = Depends(current_user)
):
    if not user.at_least(Role.AUTHOR):
        raise HTTPException(403, "creating a project requires the author role or above")
    key = payload.key.upper()
    if db.execute(select(Project).where(Project.key == key)).scalar_one_or_none():
        raise HTTPException(409, f"project key {key} is already in use")

    project = Project(
        key=key, name=payload.name, description=payload.description,
        environments=payload.environments, default_environment=payload.default_environment,
        settings=payload.settings,
    )
    db.add(project)
    db.flush()
    audit.record(db, action="project.created", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="project", resource_id=project.id,
                 detail={"key": key, "name": payload.name})
    db.commit()
    return ProjectOut(**{c.name: getattr(project, c.name) for c in Project.__table__.columns
                         if c.name in ProjectOut.model_fields})


@router.get("/{project_id}", response_model=ProjectOut)
def read_project(project: Project = Depends(get_project)):
    return ProjectOut(**{c.name: getattr(project, c.name) for c in Project.__table__.columns
                         if c.name in ProjectOut.model_fields})


@router.patch("/{project_id}", response_model=ProjectOut)
def update_project(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.AUTHOR):
        raise HTTPException(403, "requires the author role or above")
    for field in ("name", "description", "environments", "default_environment", "settings"):
        if field in payload:
            setattr(project, field, payload[field])
    audit.record(db, action="project.updated", actor_id=user.id, project_id=project.id,
                 resource_type="project", resource_id=project.id,
                 detail={"fields": sorted(payload)})
    db.commit()
    return ProjectOut(**{c.name: getattr(project, c.name) for c in Project.__table__.columns
                         if c.name in ProjectOut.model_fields})


@router.get("/{project_id}/overview")
def overview(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    """Everything the dashboard needs in one round trip."""
    from sqlalchemy import func

    from ...intelligence.coverage import compute
    from ...models import (
        ApprovalRequest,
        ApprovalStatus,
        Run,
        RunStatus,
        TestCase,
        TestStatus,
    )

    cases = list(db.execute(select(TestCase).where(TestCase.project_id == project.id)).scalars())
    runs = list(
        db.execute(
            select(Run).where(Run.project_id == project.id)
            .order_by(Run.created_at.desc()).limit(20)
        ).scalars()
    )
    pending = db.execute(
        select(func.count()).select_from(ApprovalRequest).where(
            ApprovalRequest.project_id == project.id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    ).scalar_one()

    by_category: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for case in cases:
        by_category[case.category] = by_category.get(case.category, 0) + 1
        by_status[case.status] = by_status.get(case.status, 0) + 1

    recent = [
        {
            "id": r.id, "number": r.number, "status": r.status, "title": r.title,
            "totals": r.totals, "duration_ms": r.duration_ms,
            "environment": r.environment, "trigger": r.trigger,
            "headline": (r.triage or {}).get("headline", ""),
            "created_at": r.created_at.isoformat(),
        }
        for r in runs
    ]
    finished = [r for r in runs if r.status in {RunStatus.PASSED, RunStatus.FAILED, RunStatus.FLAKY}]
    pass_rate = (
        sum((r.totals or {}).get("passed", 0) for r in finished)
        / max(1, sum((r.totals or {}).get("total", 0) for r in finished))
    ) if finished else 0.0

    return {
        "project": {"id": project.id, "key": project.key, "name": project.name,
                    "environments": project.environments},
        "tests": {"total": len(cases), "by_category": by_category, "by_status": by_status,
                  "awaiting_review": by_status.get(TestStatus.PROPOSED, 0),
                  "quarantined": sum(1 for c in cases if c.quarantined)},
        "runs": {"recent": recent, "pass_rate": round(pass_rate, 4)},
        "approvals_pending": pending,
        "coverage": compute(db, project.id, persist=False),
        "flaky": sorted(
            [{"key": c.key, "title": c.title, "score": round(c.flake_score, 3)}
             for c in cases if c.flake_score > 0.3],
            key=lambda r: r["score"], reverse=True,
        )[:10],
    }
