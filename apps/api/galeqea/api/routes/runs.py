from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core.security import authorize
from ...db import get_db
from ...engine.supervisor import cancel_run, resume_handoff
from ...models import Artifact, Project, Role, Run, RunStatus, RunStepRecord, RunTest, User
from ...services.runs import start_run
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/runs", tags=["runs"])


def _queue_position(run) -> int | None:
    """A queued run's 1-based place in line, so the board can show 'queued · position N'."""
    if run.status != RunStatus.QUEUED:
        return None
    job_id = (run.ci_metadata or {}).get("job_id")
    if not job_id:
        return None
    try:
        from ...jobs import get_queue
        return get_queue().position(job_id)
    except Exception:  # noqa: BLE001
        return None


@router.get("")
def list_runs(
    project: Project = Depends(get_project), db: Session = Depends(get_db), limit: int = 50,
    environment: str = "", milestone_id: str = "",
):
    q = select(Run).where(Run.project_id == project.id)
    if environment:
        q = q.where(Run.environment == environment)
    if milestone_id:
        q = q.where(Run.milestone_id == milestone_id)
    rows = db.execute(q.order_by(Run.created_at.desc()).limit(min(limit, 200))).scalars()
    all_rows = list(rows)
    # Map a parent run's number so a child can render "↳ rerun of #22" without a
    # second lookup on the client, including parents outside this page.
    number_by_id = {r.id: r.number for r in all_rows}
    missing = {r.parent_run_id for r in all_rows
               if r.parent_run_id and r.parent_run_id not in number_by_id}
    if missing:
        for pid, num in db.execute(
            select(Run.id, Run.number).where(Run.id.in_(missing))
        ).all():
            number_by_id[pid] = num
    return [
        {"id": r.id, "number": r.number, "title": r.title, "status": r.status,
         "trigger": r.trigger, "environment": r.environment, "totals": r.totals,
         "milestone_id": r.milestone_id, "cycle_id": r.cycle_id,
         "duration_ms": r.duration_ms, "headline": (r.triage or {}).get("headline", ""),
         # Parent/child linkage, so a rerun's attempts read as related, not as a
         # handful of unrelated failed runs.
         "parent_run_id": r.parent_run_id,
         "parent_run_number": number_by_id.get(r.parent_run_id),
         "rerun_kind": (r.ci_metadata or {}).get("rerun_kind", ""),
         "attempt": (r.ci_metadata or {}).get("attempt", 0),
         "queue_position": _queue_position(r),
         "created_at": r.created_at.isoformat(),
         "finished_at": r.finished_at.isoformat() if r.finished_at else None}
        for r in all_rows
    ]


@router.post("")
async def create_run(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(authorize(role=Role.AUTHOR, scope="runs:write")),
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

    # Per-test-type breakdown (functional / a11y / visual / …) so the board can show
    # where the run's coverage, and its failures, actually landed.
    from ...models import TestCase
    from ...services.test_plan import golden_path_type, type_label
    case_ids = {r.test_case_id for r in results if r.test_case_id}
    cases = {c.id: c for c in db.execute(
        select(TestCase).where(TestCase.id.in_(case_ids))).scalars()} if case_ids else {}
    # Execution modes aren't test *types*: an untyped test shouldn't read as "Automated"
    # on a by-type bar. Fold them into "Other" so the bars are genuinely per-type.
    _MODES = {"automated", "manual", "exploratory"}
    by_type: dict[str, dict] = {}
    for r in results:
        ty = golden_path_type(cases.get(r.test_case_id), r.test_key)
        if not ty or ty in _MODES:
            ty = "other"
        g = by_type.setdefault(ty, {"type": ty, "label": "Other" if ty == "other" else type_label(ty),
                                    "total": 0, "passed": 0, "failed": 0, "skipped": 0})
        g["total"] += 1
        if r.status in ("passed", "flaky"):
            g["passed"] += 1
        elif r.status in ("skipped", "cancelled"):
            g["skipped"] += 1
        else:
            g["failed"] += 1
    by_test_type = sorted(by_type.values(), key=lambda g: (-g["total"], g["label"]))
    usage = _run_model_usage(db, run, [r.id for r in results])

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
        "by_test_type": by_test_type,
        "model_usage": usage,
    }


def _run_model_usage(db: Session, run: Run, result_ids: list[str]) -> dict:
    """The model spend attributable to a run, i.e. any in-run healing / RCA / judge
    calls in the run's window. Execution itself uses no model, so a healthy or No-AI
    run is $0: the point of the ticker is to make "re-running is free" visible. Also
    reports step-cache hit rate (heals resolved from cache = zero-token)."""
    from ...models import AgentTrace, HealEvent
    from ...models.base import utcnow
    usage = {"calls": 0, "tokens": 0, "cost_usd": 0.0, "cache_hits": 0, "heals": 0}
    if run.started_at is not None:
        end = run.finished_at or utcnow()
        rows = db.execute(select(AgentTrace).where(
            AgentTrace.project_id == run.project_id,
            AgentTrace.agent_role.in_(["healer", "rca_analyst", "judge"]),
            AgentTrace.created_at >= run.started_at,
            AgentTrace.created_at <= end)).scalars().all()
        usage["calls"] = len(rows)
        usage["tokens"] = sum((t.input_tokens or 0) + (t.output_tokens or 0) for t in rows)
        usage["cost_usd"] = round(sum(t.cost_usd or 0.0 for t in rows), 4)
    if result_ids:
        heals = db.execute(select(HealEvent.strategy).where(
            HealEvent.run_test_id.in_(result_ids))).scalars().all()
        usage["heals"] = len(heals)
        usage["cache_hits"] = sum(1 for s in heals if s == "cache")
    return usage


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


@router.get("/{run_id}/results/{result_id}/evidence.zip")
def evidence_bundle(run_id: str, result_id: str, project: Project = Depends(get_project),
                    db: Session = Depends(get_db)):
    """One-click evidence bundle (zip) for a failing result."""
    rt = db.get(RunTest, result_id)
    if rt is None or rt.run_id != run_id or (rt.run and rt.run.project_id != project.id):
        raise HTTPException(404, "result not found")
    from ...services import evidence_bundle
    try:
        data, name = evidence_bundle.build_zip(db, result_id)
    except evidence_bundle.EvidenceError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get("/{run_id}/next-untested")
def next_untested(run_id: str, project: Project = Depends(get_project),
                  db: Session = Depends(get_db)):
    """The next result in a run still needing a manual verdict (for the keyboard runner)."""
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    pending = {RunStatus.QUEUED, RunStatus.NEEDS_REVIEW, RunStatus.BLOCKED}
    rt = db.execute(
        select(RunTest).where(RunTest.run_id == run_id, RunTest.status.in_(pending))
        .order_by(RunTest.created_at)).scalars().first()
    if rt is None:
        return {"done": True, "next": None}
    return {"done": False, "next": {"id": rt.id, "key": rt.test_key, "title": rt.title,
                                    "status": rt.status}}


@router.post("/{run_id}/results/{result_id}/attachment", status_code=201)
async def attach_to_result(run_id: str, result_id: str, file: UploadFile = File(...),
                           label: str = Form(""), project: Project = Depends(get_project),
                           db: Session = Depends(get_db),
                           user: User = Depends(authorize(role=Role.AUTHOR))):
    """Attach a pasted screenshot (or any file) to a manual result."""
    rt = db.get(RunTest, result_id)
    if rt is None or rt.run_id != run_id:
        raise HTTPException(404, "result not found")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "empty file")
    from ...config import settings
    from ...services.storage import get_storage
    storage = get_storage()
    filename = file.filename or "pasted.png"
    art = Artifact(run_id=run_id, run_test_id=result_id, kind="screenshot",
                   label=label or "pasted", size_bytes=len(raw))
    if storage.is_remote:
        key = f"artifacts/{run_id}/{result_id}/{filename}"
        storage.put(key, raw, content_type=file.content_type or "application/octet-stream")
        art.path = key
        art.meta = {"backend": "s3", "filename": filename}
    else:
        dest = Path(settings.artifacts_dir) / "manual" / run_id
        dest.mkdir(parents=True, exist_ok=True)
        (dest / filename).write_bytes(raw)
        art.path = str(dest / filename)
        art.meta = {"backend": "local"}
    db.add(art)
    db.commit()
    return {"ok": True, "artifact_id": art.id, "kind": art.kind}


@router.post("/{run_id}/push", status_code=202)
def push_results_to_tms(run_id: str, payload: dict, project: Project = Depends(get_project),
                        db: Session = Depends(get_db),
                        user: User = Depends(authorize(role=Role.AUTHOR))):
    """Propose pushing a run's results to Xray / Zephyr Scale / TestRail (gated)."""
    from ...core import approvals
    from ...services import results_push
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    provider = payload.get("provider", "")
    if provider not in results_push.connected_result_targets(db, project.id):
        raise HTTPException(409, f"{provider or 'that target'} is not connected")
    req = approvals.request(
        db, action="results.push",
        title=f"Push run #{run.number} to {provider}",
        project_id=project.id, resource_type="run", resource_id=run.id,
        payload={"arguments": {"run_id": run.id, "provider": provider,
                               "target": payload.get("target", ""),
                               "environments": payload.get("environments", "")}},
        requested_by=user.id, requested_by_kind="human")
    db.commit()
    return {"status": "awaiting_approval", "approval_id": req.id, "provider": provider}


@router.get("/{run_id}/exports")
def list_run_exports(run_id: str, project: Project = Depends(get_project),
                     db: Session = Depends(get_db)):
    from ...models import RunExport
    from ...services import results_push
    rows = db.execute(select(RunExport).where(RunExport.run_id == run_id)).scalars()
    return {"exports": [results_push.export_card(r) for r in rows]}


@router.post("/{run_id}/resume-handoff")
def resume(run_id: str, payload: dict, project: Project = Depends(get_project)):
    """Hand a paused browser session back to the runner after a human unblocked it."""
    key = payload.get("handoff_key", "")
    if not resume_handoff(key):
        raise HTTPException(409, "that handoff is no longer waiting (it may have timed out)")
    return {"resumed": key}


@router.post("/{run_id}/share")
async def publish_share(
    run_id: str, payload: dict | None = None, db: Session = Depends(get_db),
    project: Project = Depends(get_project), user: User = Depends(current_user),
):
    """Publish the release report as shareable artifacts (MD / HTML / JSON / JUnit)
    through the storage interface, with a public toggle, an expiry and an optional
    stakeholder (redacted) variant."""
    from ...services import journeys, share
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    journey = journeys.for_target(db, project.id, run.base_url) or journeys.active_journey(db, project.id)
    if journey is None:
        raise HTTPException(404, "no journey for this run")
    p = payload or {}
    return await share.publish(
        db, project, journey, run,
        formats=p.get("formats"), public=p.get("public", True),
        expires_days=int(p.get("expires_days", 30)), stakeholder=bool(p.get("stakeholder")),
        actor=user.id,
    )


@router.post("/{run_id}/manual/{run_test_id}")
def mark_manual(
    run_id: str, run_test_id: str, payload: dict, db: Session = Depends(get_db),
    project: Project = Depends(get_project), user: User = Depends(current_user),
):
    """A human marks a manual checklist row pass/fail/blocked/skipped, with a note
    and an optional attachment. The executor is recorded on the row."""
    from ...services import manual
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    result = manual.mark(db, run, run_test_id, status=payload.get("status", ""),
                         note=payload.get("note", ""), attachment=payload.get("attachment", ""),
                         user=user)
    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "could not mark"))
    return result


@router.get("/{run_id}/readiness")
def readiness_of(
    run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)
):
    """The Go/No-Go evaluation for a run, against its journey's plan."""
    from ...services import journeys, readiness
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    journey = journeys.for_target(db, project.id, run.base_url) or journeys.active_journey(db, project.id)
    if journey is None:
        raise HTTPException(404, "no journey for this run")
    return readiness.evaluate(db, journey, run)


@router.post("/{run_id}/readiness/sign-off")
def sign_off_readiness(
    run_id: str, payload: dict | None = None, db: Session = Depends(get_db),
    project: Project = Depends(get_project), user: User = Depends(current_user),
):
    """Record a human sign-off on the readiness gate. An AI principal is refused
    (``SelfApprovalError`` → 403); a No-Go needs an override note."""
    from ...core.approvals import SelfApprovalError
    from ...services import journeys, readiness
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    journey = journeys.for_target(db, project.id, run.base_url) or journeys.active_journey(db, project.id)
    if journey is None:
        raise HTTPException(404, "no journey for this run")
    try:
        result = readiness.sign_off(db, journey, run, decider=user,
                                    override_note=(payload or {}).get("override_note"))
    except SelfApprovalError as exc:
        raise HTTPException(403, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(422, result.get("error", "sign-off refused"))
    return result


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

    # S3-backed: stream the object through the API so the bucket stays private.
    if (artifact.meta or {}).get("backend") == "s3":
        from ...services.artifacts import open_artifact

        try:
            data, content_type, filename = open_artifact(artifact)
        except Exception as exc:  # noqa: BLE001 - a missing object is a 404
            raise HTTPException(404, "artifact object is missing") from exc
        return Response(content=data, media_type=content_type,
                        headers={"Content-Disposition": f'inline; filename="{filename}"'})

    path = Path(artifact.path).resolve()
    root = Path(settings.artifacts_dir).resolve()
    # Path containment check: an artifact row must never be able to serve a file
    # from outside the artifacts root.
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(404, "artifact file is missing or outside the artifact root")
    return FileResponse(path, filename=path.name)
