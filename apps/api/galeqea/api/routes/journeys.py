"""Golden Path journey state: what the UI reads to draw the progress rail.

A reload calls ``GET /journey`` and resumes exactly where the target left off; the
chat drives the stages, this just reports them.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import approvals
from ...core.approvals import ApprovalError, SelfApprovalError
from ...core.security import authorize
from ...db import get_db
from ...models import Journey, JourneyStatus, Project, Role, User
from ...services import access, journeys, plan_approval
from ..deps import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["journeys"])


@router.post("/journeys", status_code=201)
def propose_plan(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(authorize(role=Role.AUTHOR)),
):
    """Deterministically propose a Golden Path plan for a target and file it for
    approval. No model required. Returns the journey and the pending approval id."""
    target = (payload.get("target") or "").strip()
    if not target:
        raise HTTPException(400, "a target URL is required")
    try:
        journey, req = plan_approval.propose(db, project, target)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"journey": journeys.describe(journey), "approval_id": req.id,
            "plan_version": journey.plan_version, "summary": req.summary}


@router.post("/journeys/{journey_id}/plan/approve")
def approve_plan(
    journey_id: str,
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(authorize(role=Role.AUTHOR, scope="approvals:decide")),
):
    """Approve a journey's pending Golden Path plan, the one gate every surface
    shares. Builds the plan's tests and makes them runnable by `galeqea test`."""
    journey = db.get(Journey, journey_id)
    if journey is None or journey.project_id != project.id:
        raise HTTPException(404, "journey not found")
    req = plan_approval.pending_for_journey(db, journey)
    if req is None:
        raise HTTPException(404, "no plan is pending approval for this journey")
    try:
        outcome = approvals.approve(db, req.id, user, comment=(payload or {}).get("comment", ""))
    except SelfApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {"status": outcome.request.status, "applied": outcome.applied, "result": outcome.result}


@router.post("/journeys/{journey_id}/plan/reject")
def reject_plan(
    journey_id: str,
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(authorize(role=Role.AUTHOR, scope="approvals:decide")),
):
    journey = db.get(Journey, journey_id)
    if journey is None or journey.project_id != project.id:
        raise HTTPException(404, "journey not found")
    req = plan_approval.pending_for_journey(db, journey)
    if req is None:
        raise HTTPException(404, "no plan is pending approval for this journey")
    try:
        approvals.reject(db, req.id, user, comment=(payload or {}).get("comment", ""))
    except SelfApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ApprovalError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    return {"status": "rejected"}


@router.post("/journey/credentials")
def store_credentials(
    payload: dict, db: Session = Depends(get_db), project: Project = Depends(get_project)
):
    """Seal login credentials for the active journey's target, the secure path the
    Access panel posts to. The password goes straight into the vault: it is never
    echoed back, logged, or stored in the clear."""
    journey = journeys.active_journey(db, project.id)
    if journey is None:
        raise HTTPException(404, "no active journey to attach credentials to")
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""
    if not username or not password:
        raise HTTPException(400, "username and password are required")
    result = access.store_credentials(
        db, journey, username=username, password=password,
        kind=payload.get("kind", "form"), login_url=payload.get("login_url"),
        success_text=payload.get("success_text"),
    )
    return {"stored": True, **result}  # note: no password, only a masked hint


@router.delete("/journey/credentials")
def forget_credentials(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    journey = journeys.active_journey(db, project.id)
    if journey is None:
        raise HTTPException(404, "no active journey")
    return {"forgotten": access.forget(db, journey)}


@router.get("/journey")
def active_journey(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    """The current in-flight journey, or ``{active: false}`` if there is none."""
    journey = journeys.active_journey(db, project.id)
    if journey is None:
        return {"active": False}
    return {"active": True, **journeys.describe(journey)}


@router.get("/journeys")
def list_journeys(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    rows = db.execute(
        select(Journey).where(
            Journey.project_id == project.id, Journey.status == JourneyStatus.ACTIVE
        ).order_by(Journey.updated_at.desc())
    ).scalars()
    return {"journeys": [journeys.describe(j) for j in rows]}
