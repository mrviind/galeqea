"""Defect HTTP surface: propose a bug from a failing result (gated), list the
project's tracked defects, and refresh a defect's cached status."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import approvals
from ...core.security import authorize
from ...db import get_db
from ...models import DefectLink, DefectMap, Project, Role, RunTest, User
from ...services import defects
from ..deps import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["defects"])


@router.post("/results/{result_id}/defect", status_code=202)
def propose_defect(result_id: str, payload: dict | None = None,
                   project: Project = Depends(get_project), db: Session = Depends(get_db),
                   user: User = Depends(authorize(role=Role.AUTHOR))):
    """Propose filing a bug for a failing result. Returns the pending approval; the
    write reaches the tracker only once a human accepts it."""
    payload = payload or {}
    rt = db.get(RunTest, result_id)
    if rt is None or rt.run is None or rt.run.project_id != project.id:
        raise HTTPException(404, "result not found")
    provider = defects.connected_tracker(db, project.id, prefer=payload.get("provider"))
    if provider is None:
        raise HTTPException(409, "no issue tracker is connected. Add Jira, GitHub or "
                                 "GitLab under Settings → Integrations first")
    req = approvals.request(
        db, action="defect.create",
        title=f"File a bug for {rt.test_key or rt.title} ({provider})",
        project_id=project.id, resource_type="run_test", resource_id=rt.id,
        summary=f"{rt.error_type}: {rt.error_message}".strip(": ")[:300],
        payload={"arguments": {"run_test_id": rt.id, "provider": provider}},
        requested_by=user.id, requested_by_kind="human",
    )
    db.commit()
    return {"status": "awaiting_approval", "approval_id": req.id, "provider": provider}


@router.get("/defects")
def list_defects(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(DefectMap).where(DefectMap.project_id == project.id)
        .order_by(DefectMap.last_seen.desc())).scalars()
    return {"defects": [defects.defect_card(dm) for dm in rows]}


@router.get("/results/{result_id}/defects")
def defects_for_result(result_id: str, project: Project = Depends(get_project),
                       db: Session = Depends(get_db)):
    rows = db.execute(
        select(DefectLink).where(DefectLink.project_id == project.id,
                                 DefectLink.result_id == result_id)).scalars()
    return {"links": [{"key": link.key, "tracker": link.tracker, "url": link.url,
                       "status": link.status_cached,
                       "last_synced": link.last_synced.isoformat() if link.last_synced else None}
                      for link in rows]}


@router.post("/defects/{map_id}/refresh")
def refresh_defect(map_id: str, project: Project = Depends(get_project),
                   db: Session = Depends(get_db),
                   user: User = Depends(authorize(role=Role.AUTHOR))):
    dm = db.get(DefectMap, map_id)
    if dm is None or dm.project_id != project.id:
        raise HTTPException(404, "defect not found")
    try:
        defects.refresh_status(db, project, dm)
    except defects.DefectError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return defects.defect_card(dm)
