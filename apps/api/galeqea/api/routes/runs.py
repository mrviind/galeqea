from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...db import get_db
from ...engine.supervisor import cancel_run, resume_handoff
from ...models import Artifact, Project, Run, RunStatus, RunStepRecord, RunTest, User
from ...services.runs import start_run
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/runs", tags=["runs"])


@router.get("")
def list_runs(
    project: Project = Depends(get_project), db: Session = Depends(get_db), limit: int = 50
):
    rows = db.execute(
        select(Run).where(Run.project_id == project.id)
        .order_by(Run.created_at.desc()).limit(min(limit, 200))
    ).scalars()
    return [
        {"id": r.id, "number": r.number, "title": r.title, "status": r.status,
         "trigger": r.trigger, "environment": r.environment, "totals": r.totals,
         "duration_ms": r.duration_ms, "headline": (r.triage or {}).get("headline", ""),
         "created_at": r.created_at.isoformat(),
         "finished_at": r.finished_at.isoformat() if r.finished_at else None}
        for r in rows
    ]


@router.post("")
async def create_run(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    run = await start_run(
        db,
        project_id=project.id,
        selection=payload.get("selection") or {},
        environment=payload.get("environment", ""),
        browsers=payload.get("browsers"),
        trigger=payload.get("trigger", "manual"),
        triggered_by=user.id,
        command=payload.get("command", ""),
        title=payload.get("title", ""),
        suite_id=payload.get("suite_id"),
        parent_run_id=payload.get("parent_run_id"),
        git_sha=payload.get("git_sha", ""),
        git_branch=payload.get("git_branch", ""),
    )
    return {"id": run.id, "number": run.number, "status": run.status, "totals": run.totals,
            "error": run.error}


@router.get("/{run_id}")
def read_run(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars())
    artifacts = list(db.execute(select(Artifact).where(Artifact.run_id == run.id)).scalars())
    return {
        "run": {
            "id": run.id, "number": run.number, "title": run.title, "status": run.status,
            "trigger": run.trigger, "command": run.command, "environment": run.environment,
            "base_url": run.base_url, "browsers": run.browsers, "totals": run.totals,
            "triage": run.triage, "duration_ms": run.duration_ms, "error": run.error,
            "git_sha": run.git_sha, "git_branch": run.git_branch,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        },
        "results": [
            {"id": r.id, "test_case_id": r.test_case_id, "key": r.test_key, "title": r.title,
             "status": r.status, "browser": r.browser, "duration_ms": r.duration_ms,
             "error_message": r.error_message, "error_type": r.error_type,
             "classification": r.classification, "healed": r.healed,
             "signature": r.failure_signature,
             "console_errors": r.console_errors, "network_failures": r.network_failures}
            for r in results
        ],
        "artifacts": [
            {"id": a.id, "kind": a.kind, "label": a.label, "run_test_id": a.run_test_id,
             "size_bytes": a.size_bytes}
            for a in artifacts
        ],
    }


@router.get("/{run_id}/results/{run_test_id}/steps")
def read_steps(
    run_id: str, run_test_id: str, db: Session = Depends(get_db),
    project: Project = Depends(get_project),
):
    rows = db.execute(
        select(RunStepRecord).where(RunStepRecord.run_test_id == run_test_id)
        .order_by(RunStepRecord.index)
    ).scalars()
    return [
        {"index": s.index, "action": s.action, "intent": s.intent, "status": s.status,
         "duration_ms": s.duration_ms, "resolved_locator": s.resolved_locator,
         "heal_applied": s.heal_applied, "error_message": s.error_message,
         "logs": s.logs, "artifacts": s.artifacts}
        for s in rows
    ]


@router.post("/{run_id}/cancel")
def cancel(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    cancel_run(run_id)
    return {"cancelled": run_id, "note": "the runner stops after the current step"}


@router.post("/{run_id}/resume-handoff")
def resume(run_id: str, payload: dict, project: Project = Depends(get_project)):
    """Hand a paused browser session back to the runner after a human unblocked it."""
    key = payload.get("handoff_key", "")
    if not resume_handoff(key):
        raise HTTPException(409, "that handoff is no longer waiting (it may have timed out)")
    return {"resumed": key}


@router.post("/{run_id}/rerun")
async def rerun(
    run_id: str,
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    payload = payload or {}
    original = db.get(Run, run_id)
    if original is None or original.project_id != project.id:
        raise HTTPException(404, "run not found")

    if payload.get("failed_only"):
        failed = list(
            db.execute(
                select(RunTest).where(
                    RunTest.run_id == run_id,
                    RunTest.status.in_([RunStatus.FAILED, RunStatus.ERROR]),
                )
            ).scalars()
        )
        if not failed:
            raise HTTPException(400, "nothing failed in that run")
        selection = {"test_ids": [f.test_case_id for f in failed]}
        title = f"Re-run failures from #{original.number}"
    else:
        selection = original.selection or {}
        title = f"Re-run of #{original.number}"

    run = await start_run(
        db, project_id=project.id, selection=selection,
        environment=payload.get("environment") or original.environment,
        browsers=payload.get("browsers") or original.browsers,
        trigger="rerun", triggered_by=user.id, title=title, parent_run_id=original.id,
    )
    return {"id": run.id, "number": run.number, "status": run.status}


@router.get("/{run_id}/artifacts/{artifact_id}")
def download_artifact(
    run_id: str, artifact_id: str, db: Session = Depends(get_db),
    project: Project = Depends(get_project),
):
    from ...config import settings

    artifact = db.get(Artifact, artifact_id)
    if artifact is None or artifact.run_id != run_id:
        raise HTTPException(404, "artifact not found")
    path = Path(artifact.path).resolve()
    root = Path(settings.artifacts_dir).resolve()
    # Path containment check: an artifact row must never be able to serve a file
    # from outside the artifacts root.
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(404, "artifact file is missing or outside the artifact root")
    return FileResponse(path, filename=path.name)
