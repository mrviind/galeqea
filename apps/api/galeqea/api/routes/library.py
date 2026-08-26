"""Suites and schedules.

Note on the approval gate: a human creating a schedule *in the UI* is acting
directly, and is themselves the approver - routing that through the gate would
require them to approve their own request, which the gate structurally forbids.
The gate exists for writes an **agent** proposes. Agent-proposed schedules still
arrive via ``schedule.create`` and land in the approval queue exactly as before.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import audit
from ...db import get_db
from ...models import (
    Project,
    Role,
    Schedule,
    SuiteMember,
    TestCase,
    TestSuite,
    User,
)
from ...services import scheduler
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["library"])


# --------------------------------------------------------------------------- #
# Suites
# --------------------------------------------------------------------------- #
def _serialize_suite(db: Session, suite: TestSuite) -> dict:
    cases = {
        c.id: c
        for c in db.execute(
            select(TestCase).where(
                TestCase.id.in_([m.test_case_id for m in suite.members] or [""])
            )
        ).scalars()
    }
    members = [
        {
            "test_case_id": m.test_case_id,
            "position": m.position,
            "key": cases[m.test_case_id].key if m.test_case_id in cases else "",
            "title": cases[m.test_case_id].title if m.test_case_id in cases else "(deleted)",
            "category": cases[m.test_case_id].category if m.test_case_id in cases else "",
            "last_status": cases[m.test_case_id].last_status if m.test_case_id in cases else "",
            "flake_score": round(cases[m.test_case_id].flake_score, 2) if m.test_case_id in cases else 0,
        }
        for m in sorted(suite.members, key=lambda m: m.position)
    ]
    return {
        "id": suite.id, "name": suite.name, "description": suite.description,
        "kind": suite.kind, "query": suite.query, "browsers": suite.browsers,
        "parallelism": suite.parallelism, "members": members, "size": len(members),
    }


@router.get("/suites")
def list_suites(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(TestSuite).where(TestSuite.project_id == project.id).order_by(TestSuite.name)
    ).scalars()
    return [_serialize_suite(db, s) for s in rows]


@router.post("/suites")
def create_suite(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.AUTHOR):
        raise HTTPException(403, "creating a suite requires the author role or above")
    name = (payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "a suite needs a name")

    suite = TestSuite(
        project_id=project.id,
        name=name,
        description=payload.get("description", ""),
        kind=payload.get("kind", "static"),
        query=payload.get("query") or {},
        browsers=payload.get("browsers") or ["chromium"],
        parallelism=payload.get("parallelism", 2),
    )
    db.add(suite)
    db.flush()
    for position, test_id in enumerate(payload.get("test_ids") or []):
        db.add(SuiteMember(suite_id=suite.id, test_case_id=test_id, position=position))
    db.flush()

    audit.record(db, action="suite.created", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="test_suite", resource_id=suite.id,
                 detail={"name": name, "members": len(payload.get("test_ids") or [])})
    db.commit()
    return _serialize_suite(db, suite)


@router.patch("/suites/{suite_id}")
def update_suite(
    suite_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    suite = db.get(TestSuite, suite_id)
    if suite is None or suite.project_id != project.id:
        raise HTTPException(404, "suite not found")
    for field in ("name", "description", "browsers", "parallelism", "query", "kind"):
        if field in payload:
            setattr(suite, field, payload[field])
    if "test_ids" in payload:
        for member in list(suite.members):
            db.delete(member)
        db.flush()
        for position, test_id in enumerate(payload["test_ids"]):
            db.add(SuiteMember(suite_id=suite.id, test_case_id=test_id, position=position))
    db.flush()
    audit.record(db, action="suite.updated", actor_id=user.id, project_id=project.id,
                 resource_type="test_suite", resource_id=suite.id,
                 detail={"fields": sorted(payload)})
    db.commit()
    return _serialize_suite(db, suite)


@router.delete("/suites/{suite_id}")
def delete_suite(
    suite_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    suite = db.get(TestSuite, suite_id)
    if suite is None or suite.project_id != project.id:
        raise HTTPException(404, "suite not found")
    # Schedules pointing at a deleted suite would fire against nothing, which is
    # worse than refusing: a green empty run looks like success.
    bound = db.execute(
        select(Schedule).where(Schedule.suite_id == suite_id, Schedule.enabled.is_(True))
    ).scalars().all()
    if bound:
        raise HTTPException(
            409,
            f"{len(bound)} enabled schedule(s) run this suite: "
            f"{', '.join(s.name for s in bound)}. Disable or repoint them first.",
        )
    audit.record(db, action="suite.deleted", actor_id=user.id, project_id=project.id,
                 resource_type="test_suite", resource_id=suite_id, detail={"name": suite.name})
    db.delete(suite)
    db.commit()
    return {"deleted": suite_id}


@router.post("/suites/{suite_id}/run")
async def run_suite(
    suite_id: str,
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    from ...services.runs import start_run

    suite = db.get(TestSuite, suite_id)
    if suite is None or suite.project_id != project.id:
        raise HTTPException(404, "suite not found")
    payload = payload or {}

    selection = _resolve_suite_selection(db, project.id, suite)
    if not selection.get("test_ids"):
        raise HTTPException(400, "that suite currently matches no approved tests")

    run = await start_run(
        db, project_id=project.id, selection=selection,
        environment=payload.get("environment", ""),
        browsers=payload.get("browsers") or suite.browsers,
        trigger="manual", triggered_by=user.id, suite_id=suite.id,
        title=f"Suite: {suite.name}",
    )
    return {"id": run.id, "number": run.number, "status": run.status, "totals": run.totals}


def _resolve_suite_selection(db: Session, project_id: str, suite: TestSuite) -> dict:
    """A dynamic suite is a saved query, resolved at run time, not a frozen list."""
    if suite.kind == "dynamic":
        from ...engine.plan import select_tests

        matched = select_tests(db, project_id, suite.query or {})
        return {"test_ids": [c.id for c in matched], "suite": suite.name}
    return {"test_ids": [m.test_case_id for m in suite.members], "suite": suite.name}


@router.post("/suites/{suite_id}/preview")
def preview_suite(
    suite_id: str, project: Project = Depends(get_project), db: Session = Depends(get_db)
):
    """What would this suite run right now? Matters most for dynamic suites."""
    suite = db.get(TestSuite, suite_id)
    if suite is None or suite.project_id != project.id:
        raise HTTPException(404, "suite not found")
    ids = _resolve_suite_selection(db, project.id, suite).get("test_ids") or []
    cases = db.execute(select(TestCase).where(TestCase.id.in_(ids or [""]))).scalars()
    return {
        "count": len(ids),
        "tests": [{"key": c.key, "title": c.title, "category": c.category,
                   "status": c.status} for c in cases],
    }


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #
def _serialize_schedule(schedule: Schedule) -> dict:
    return {
        "id": schedule.id, "name": schedule.name, "cron": schedule.cron,
        "description": scheduler.describe_cron(schedule.cron),
        "timezone": schedule.timezone, "suite_id": schedule.suite_id,
        "selection": schedule.selection, "environment": schedule.environment,
        "enabled": schedule.enabled,
        "last_fired_at": schedule.last_fired_at.isoformat() if schedule.last_fired_at else None,
        "next_fire_at": _next_fire(schedule),
        "created_at": schedule.created_at.isoformat(),
    }


def _next_fire(schedule: Schedule) -> str | None:
    job = scheduler.scheduler().get_job(f"schedule:{schedule.id}")
    nxt = getattr(job, "next_run_time", None) if job else None
    return nxt.isoformat() if nxt else None


@router.get("/schedules")
def list_schedules(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Schedule).where(Schedule.project_id == project.id).order_by(Schedule.name)
    ).scalars()
    return [_serialize_schedule(s) for s in rows]


@router.post("/schedules")
def create_schedule(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.AUTHOR):
        raise HTTPException(403, "creating a schedule requires the author role or above")
    cron = (payload.get("cron") or "").strip()
    name = (payload.get("name") or "").strip()
    if not name or not cron:
        raise HTTPException(400, "a schedule needs a name and a cron expression")

    schedule = Schedule(
        project_id=project.id, name=name, cron=cron,
        timezone=payload.get("timezone", "UTC"),
        suite_id=payload.get("suite_id"),
        selection=payload.get("selection") or {},
        environment=payload.get("environment") or project.default_environment,
        created_by=user.id,
    )
    db.add(schedule)
    db.flush()
    try:
        scheduler.register(schedule)
    except ValueError as exc:
        raise HTTPException(400, f"invalid cron expression {cron!r}: {exc}") from exc

    audit.record(db, action="schedule.created", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="schedule", resource_id=schedule.id,
                 detail={"name": name, "cron": cron})
    db.commit()
    return _serialize_schedule(schedule)


@router.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.project_id != project.id:
        raise HTTPException(404, "schedule not found")
    for field in ("name", "cron", "timezone", "environment", "selection", "suite_id", "enabled"):
        if field in payload:
            setattr(schedule, field, payload[field])
    db.flush()

    scheduler.unregister(schedule.id)
    if schedule.enabled:
        try:
            scheduler.register(schedule)
        except ValueError as exc:
            raise HTTPException(400, f"invalid cron expression: {exc}") from exc

    audit.record(db, action="schedule.updated", actor_id=user.id, project_id=project.id,
                 resource_type="schedule", resource_id=schedule.id,
                 detail={"fields": sorted(payload), "enabled": schedule.enabled})
    db.commit()
    return _serialize_schedule(schedule)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(
    schedule_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.project_id != project.id:
        raise HTTPException(404, "schedule not found")
    scheduler.unregister(schedule_id)
    audit.record(db, action="schedule.deleted", actor_id=user.id, project_id=project.id,
                 resource_type="schedule", resource_id=schedule_id,
                 detail={"name": schedule.name, "cron": schedule.cron})
    db.delete(schedule)
    db.commit()
    return {"deleted": schedule_id}


@router.post("/schedules/{schedule_id}/run-now")
async def run_schedule_now(
    schedule_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Fire a schedule immediately - the only honest way to test one."""
    from ...services.runs import start_run

    schedule = db.get(Schedule, schedule_id)
    if schedule is None or schedule.project_id != project.id:
        raise HTTPException(404, "schedule not found")

    selection = dict(schedule.selection or {})
    if schedule.suite_id:
        suite = db.get(TestSuite, schedule.suite_id)
        if suite:
            selection = _resolve_suite_selection(db, project.id, suite)

    run = await start_run(
        db, project_id=project.id, selection=selection,
        environment=schedule.environment, trigger="schedule", triggered_by=user.id,
        title=f"Manual fire: {schedule.name}", command=f"cron:{schedule.cron}",
    )
    return {"id": run.id, "number": run.number, "status": run.status, "totals": run.totals}


@router.post("/schedules/preview-cron")
def preview_cron(payload: dict):
    """Explain a cron expression in English so it is reviewable before saving."""
    from apscheduler.triggers.cron import CronTrigger

    expression = (payload.get("cron") or "").strip()
    try:
        CronTrigger.from_crontab(expression, timezone=payload.get("timezone", "UTC"))
    except ValueError as exc:
        return {"valid": False, "error": str(exc), "description": ""}
    return {"valid": True, "error": "", "description": scheduler.describe_cron(expression)}
