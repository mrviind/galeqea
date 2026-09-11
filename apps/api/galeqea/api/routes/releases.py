"""Release-management HTTP surface: milestones, environments, plans, cycles,
readiness, sign-off and metrics. Creates are author+; sign-off is approver+ human."""

from __future__ import annotations

from datetime import UTC

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.approvals import SelfApprovalError
from ...core.security import authorize
from ...db import get_db
from ...models import Cycle, Environment, Milestone, Project, Role, TestPlan, User
from ...services import release, release_metrics
from ..deps import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["releases"])


def _parse_date(value: str | None):
    if not value:
        return None
    from datetime import datetime
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None


# --- milestones ------------------------------------------------------------
@router.get("/milestones")
def list_milestones(db: Session = Depends(get_db), project: Project = Depends(get_project),
                    include_archived: bool = False):
    q = select(Milestone).where(Milestone.project_id == project.id)
    if not include_archived:
        q = q.where(Milestone.status != "archived")
    rows = db.execute(q.order_by(Milestone.created_at.desc())).scalars()
    return {"milestones": [release.milestone_card(m) for m in rows]}


@router.post("/milestones/{milestone_id}/archive")
def archive_milestone(milestone_id: str, project: Project = Depends(get_project),
                      db: Session = Depends(get_db),
                      user: User = Depends(authorize(role=Role.AUTHOR))):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    release.archive_milestone(db, m)
    db.commit()
    return release.milestone_card(m)


@router.delete("/milestones/{milestone_id}")
def delete_milestone(milestone_id: str, project: Project = Depends(get_project),
                     db: Session = Depends(get_db),
                     user: User = Depends(authorize(role=Role.ADMIN))):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    release.delete_milestone(db, m)
    db.commit()
    return {"deleted": milestone_id}


@router.post("/milestones", status_code=201)
def create_milestone(payload: dict, response: Response, project: Project = Depends(get_project),
                     db: Session = Depends(get_db),
                     user: User = Depends(authorize(role=Role.AUTHOR))):
    if not payload.get("version"):
        raise HTTPException(400, "a version is required")
    # Idempotent per project+version: a live milestone with that version is returned
    # as-is (200) rather than colliding with the unique index.
    existing = release.milestone_for(db, project.id, payload["version"])
    if existing is not None:
        response.status_code = 200
        return release.milestone_card(existing)
    m = release.create_milestone(db, project, name=payload.get("name", ""),
                                 version=payload["version"],
                                 target_date=_parse_date(payload.get("target_date")))
    if payload.get("exit_criteria"):
        release.set_exit_criteria(db, m, payload["exit_criteria"])
    db.commit()
    return release.milestone_card(m)


@router.get("/milestones/{milestone_id}")
def get_milestone(milestone_id: str, db: Session = Depends(get_db),
                  project: Project = Depends(get_project)):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    return release.milestone_card(m)


@router.patch("/milestones/{milestone_id}/exit-criteria")
def set_exit_criteria(milestone_id: str, payload: dict, db: Session = Depends(get_db),
                      project: Project = Depends(get_project),
                      user: User = Depends(authorize(role=Role.AUTHOR))):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    release.set_exit_criteria(db, m, payload.get("exit_criteria", []))
    db.commit()
    return release.milestone_card(m)


# --- environments ----------------------------------------------------------
@router.get("/environments")
def list_environments(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    rows = db.execute(select(Environment).where(Environment.project_id == project.id)
                      .order_by(Environment.name)).scalars()
    return {"environments": [release.environment_card(e) for e in rows]}


@router.post("/environments", status_code=201)
def create_environment(payload: dict, project: Project = Depends(get_project),
                       db: Session = Depends(get_db),
                       user: User = Depends(authorize(role=Role.AUTHOR))):
    if not payload.get("name") or not payload.get("base_url"):
        raise HTTPException(400, "name and base_url are required")
    env = release.add_environment(db, project, name=payload["name"], base_url=payload["base_url"],
                                  build_label=payload.get("build_label", ""),
                                  tags=payload.get("tags"))
    db.commit()
    return release.environment_card(env)


# --- plans & cycles --------------------------------------------------------
@router.post("/milestones/{milestone_id}/plans", status_code=201)
def create_plan(milestone_id: str, payload: dict, project: Project = Depends(get_project),
                db: Session = Depends(get_db),
                user: User = Depends(authorize(role=Role.AUTHOR))):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    plan, count = release.create_plan(db, project, milestone=m,
                                      name=payload.get("name", "Plan"),
                                      selection=payload.get("selection", {"query": {}}),
                                      configurations=payload.get("configurations", []))
    db.commit()
    return release.plan_card(plan, count)


@router.post("/plans/{plan_id}/cycles", status_code=201)
def start_cycles(plan_id: str, project: Project = Depends(get_project),
                 db: Session = Depends(get_db),
                 user: User = Depends(authorize(role=Role.AUTHOR))):
    plan = db.get(TestPlan, plan_id)
    if plan is None or plan.project_id != project.id:
        raise HTTPException(404, "plan not found")
    cycles = release.start_cycles(db, plan)
    db.commit()
    return {"cycles": [release.cycle_card(c) for c in cycles]}


@router.get("/cycles")
def list_cycles(db: Session = Depends(get_db), project: Project = Depends(get_project),
                milestone_id: str = ""):
    q = select(Cycle).where(Cycle.project_id == project.id)
    if milestone_id:
        q = q.where(Cycle.milestone_id == milestone_id)
    return {"cycles": [release.cycle_card(c) for c in
                       db.execute(q.order_by(Cycle.created_at.desc())).scalars()]}


# --- readiness, sign-off, metrics ------------------------------------------
@router.get("/milestones/{milestone_id}/readiness")
def readiness(milestone_id: str, db: Session = Depends(get_db),
              project: Project = Depends(get_project)):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    return release.evaluate_readiness(db, m)


@router.post("/milestones/{milestone_id}/signoff")
async def signoff(milestone_id: str, payload: dict, project: Project = Depends(get_project),
                  db: Session = Depends(get_db),
                  user: User = Depends(authorize(role=Role.APPROVER, scope="approvals:decide"))):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    try:
        release.sign_off(db, m, decider=user, decision=payload.get("decision", "go"),
                         note=payload.get("note", ""))
    except SelfApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    db.commit()
    from ...core.events import Ev, Event, bus
    await bus.publish(Event(type=Ev.MILESTONE_SIGNED_OFF, project_id=project.id,
                            payload={"milestone_id": m.id, "version": m.version,
                                     "decision": m.signoff["decision"]}))
    return release.milestone_card(m)


@router.post("/milestones/{milestone_id}/publish", status_code=202)
def publish_report(milestone_id: str, payload: dict | None = None,
                   project: Project = Depends(get_project), db: Session = Depends(get_db),
                   user: User = Depends(authorize(role=Role.AUTHOR))):
    """Propose publishing this release's report to Confluence (gated)."""
    payload = payload or {}
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    from ...core import approvals
    req = approvals.request(
        db, action="report.publish",
        title=f"Publish release {m.version} report to Confluence",
        project_id=project.id, resource_type="milestone", resource_id=m.id,
        payload={"arguments": {"version": m.version, "space_key": payload.get("space_key", "")}},
        requested_by=user.id, requested_by_kind="human")
    db.commit()
    return {"status": "awaiting_approval", "approval_id": req.id}


@router.get("/milestones/{milestone_id}/metrics.json")
def metrics_json(milestone_id: str, db: Session = Depends(get_db),
                 project: Project = Depends(get_project)):
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    return release_metrics.compute(db, m)


@router.get("/milestones/{milestone_id}/metrics.md")
def metrics_md(milestone_id: str, db: Session = Depends(get_db),
               project: Project = Depends(get_project)):
    from fastapi.responses import PlainTextResponse
    m = db.get(Milestone, milestone_id)
    if m is None or m.project_id != project.id:
        raise HTTPException(404, "milestone not found")
    from ...reports.release_report import to_markdown
    return PlainTextResponse(to_markdown(db, m))
