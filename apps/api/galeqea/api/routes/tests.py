from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai.toolset import _serialize_test
from ...core import audit
from ...db import get_db
from ...models import (
    Project,
    Role,
    StepAction,
    TestCase,
    TestStatus,
    TestStep,
    TestVersion,
    User,
)
from ...models.base import utcnow
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/tests", tags=["tests"])


@router.get("")
def list_tests(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    status: str | None = None,
    category: str | None = None,
    search: str | None = None,
):
    stmt = select(TestCase).where(TestCase.project_id == project.id)
    if status:
        stmt = stmt.where(TestCase.status == status)
    if category:
        stmt = stmt.where(TestCase.category == category)
    rows = list(db.execute(stmt.order_by(TestCase.key)).scalars())
    if search:
        needle = search.lower()
        rows = [
            r for r in rows
            if needle in r.title.lower() or needle in (r.description or "").lower()
            or any(needle in t.lower() for t in (r.tags or []))
        ]
    return {"count": len(rows), "tests": [_serialize_test(r) for r in rows]}


# Declared before /{test_id}: FastAPI matches in declaration order, so a
# parameterised route above these would swallow 'push-targets' and 'push'
# as if they were test ids.
@router.get("/push-targets")
def push_targets(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    """Which test management systems this project can export to right now."""
    from sqlalchemy import select as sa_select

    from ...integrations.testcases import TARGETS
    from ...models import IntegrationConnection

    connected = {
        c.provider: c
        for c in db.execute(
            sa_select(IntegrationConnection).where(
                IntegrationConnection.project_id == project.id,
                IntegrationConnection.enabled.is_(True),
            )
        ).scalars()
    }
    labels = {
        "xray": "Xray Cloud", "zephyr_scale": "Zephyr Scale",
        "azure_devops": "Azure DevOps Test Plans", "testrail": "TestRail",
    }
    return [
        {"target": t, "label": labels[t], "connected": t in connected,
         "status": connected[t].status if t in connected else "not_connected"}
        for t in TARGETS
    ]


@router.post("/push")
async def push_tests(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Queue an export for approval. Pushing to another system is never direct."""
    from ...ai.tools import ToolContext
    from ...ai.toolset import registry

    ctx = ToolContext(db=db, project_id=project.id, user=user, actor_kind="human")
    result = await registry.invoke("push_test_cases", payload, ctx)
    db.commit()
    if not result.get("ok", True):
        raise HTTPException(400, result.get("error", "could not queue that export"))
    return result


@router.get("/{test_id}")
def read_test(test_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    case = db.get(TestCase, test_id)
    if case is None or case.project_id != project.id:
        raise HTTPException(404, "test not found")
    versions = [
        {"version": v.version, "summary": v.change_summary, "author_kind": v.author_kind,
         "approved_by": v.approved_by, "at": v.created_at.isoformat()}
        for v in sorted(case.versions, key=lambda v: v.version)
    ]
    return {"test": _serialize_test(case), "versions": versions}


@router.post("/{test_id}/review")
def review(
    test_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
):
    """The human-in-the-loop review board: approve, reject or edit a proposal."""
    case = db.get(TestCase, test_id)
    if case is None or case.project_id != project.id:
        raise HTTPException(404, "test not found")
    if not user.at_least(Role.APPROVER):
        raise HTTPException(403, "approving a test requires the approver role or above")
    if user.is_machine:
        raise HTTPException(403, "an AI principal can never approve a test")

    decision = payload.get("decision")
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "decision must be 'approve' or 'reject'")

    if edits := payload.get("edits"):
        for field in ("title", "description", "category", "priority", "risk", "tags",
                      "rationale", "charter", "preconditions", "requirement_refs"):
            if field in edits:
                setattr(case, field, edits[field])
        if "steps" in edits:
            for existing in list(case.steps):
                db.delete(existing)
            db.flush()
            for index, step in enumerate(edits["steps"]):
                db.add(TestStep(
                    test_case_id=case.id, index=index,
                    action=step.get("action", StepAction.NOTE),
                    intent=step.get("intent", ""), expected=step.get("expected", ""),
                    target=step.get("target", {}), value=step.get("value", {}),
                    options=step.get("options", {}),
                ))
        case.version += 1

    case.status = TestStatus.APPROVED if decision == "approve" else TestStatus.REJECTED
    case.review_comment = payload.get("comment", "")
    if decision == "approve":
        case.approved_by = user.id
        case.approved_at = utcnow()
        # Provenance records who took responsibility, which is the whole point
        # of the gate. It is written here and never mutated afterwards.
        case.provenance = {
            **(case.provenance or {}),
            "approved_by": user.id,
            "approved_by_email": user.email,
            "approved_at": utcnow().isoformat(),
            "edited_before_approval": bool(payload.get("edits")),
        }
    db.flush()

    db.add(TestVersion(
        test_case_id=case.id, version=case.version, snapshot=_serialize_test(case),
        change_summary=f"{decision} by {user.email}"
        + (" with edits" if payload.get("edits") else ""),
        author_kind="human", author_id=user.id,
        approved_by=user.id if decision == "approve" else None,
    ))
    audit.record(
        db, action=f"test.{decision}d", actor_id=user.id, actor_label=user.email,
        project_id=project.id, resource_type="test_case", resource_id=case.id,
        detail={"key": case.key, "edited": bool(payload.get("edits")),
                "comment": payload.get("comment", "")},
    )
    db.commit()
    return {"test": _serialize_test(case)}


@router.post("/bulk-review")
def bulk_review(
    payload: dict,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.APPROVER) or user.is_machine:
        raise HTTPException(403, "requires a human approver")
    decision = payload.get("decision")
    ids = payload.get("test_ids") or []
    if decision not in {"approve", "reject"} or not ids:
        raise HTTPException(400, "supply decision and test_ids")

    updated = []
    for test_id in ids:
        case = db.get(TestCase, test_id)
        if case is None or case.project_id != project.id:
            continue
        case.status = TestStatus.APPROVED if decision == "approve" else TestStatus.REJECTED
        if decision == "approve":
            case.approved_by = user.id
            case.approved_at = utcnow()
            case.provenance = {**(case.provenance or {}), "approved_by": user.id,
                               "approved_at": utcnow().isoformat(), "bulk": True}
        updated.append(case.key)
    audit.record(db, action=f"test.bulk_{decision}d", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="test_case",
                 detail={"count": len(updated), "keys": updated[:50]})
    db.commit()
    return {"updated": updated, "count": len(updated)}


@router.post("/{test_id}/quarantine")
def quarantine(
    test_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
):
    case = db.get(TestCase, test_id)
    if case is None or case.project_id != project.id:
        raise HTTPException(404, "test not found")
    case.quarantined = bool(payload.get("quarantined", True))
    audit.record(
        db, action="test.quarantined" if case.quarantined else "test.unquarantined",
        actor_id=user.id, actor_label=user.email, project_id=project.id,
        resource_type="test_case", resource_id=case.id,
        detail={"key": case.key, "reason": payload.get("reason", "")},
    )
    db.commit()
    return {"key": case.key, "quarantined": case.quarantined}


@router.get("/{test_id}/export")
def export_test(
    test_id: str,
    target: str = "playwright",
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
):
    """Render a stored test into runnable source. Zero lock-in, by construction."""
    from ...engine.codegen import render

    case = db.get(TestCase, test_id)
    if case is None or case.project_id != project.id:
        raise HTTPException(404, "test not found")
    try:
        code = render(case, target=target, base_url=(project.environments or {}).get(
            project.default_environment, ""))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"target": target, "filename": f"{case.key.lower()}.{_ext(target)}", "code": code}


def _ext(target: str) -> str:
    return {"playwright": "spec.ts", "playwright_python": "py", "pytest": "py",
            "robot": "robot", "cucumber": "feature"}.get(target, "txt")
