from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
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
_EXPORT_MEDIA = {
    "testrail_csv": "text/csv", "testrail_steps_csv": "text/csv", "xray_csv": "text/csv",
    "qase_json": "application/json", "gherkin": "text/plain", "playwright": "text/plain",
}


@router.get("/export.bulk")
def export_bulk(
    format: str = "testrail_csv",
    ref: str | None = None,
    status: str | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    """Export many cases at once to a TMS/import format (WO#9-C). Optionally scope by
    a requirement ref (matches requirement_refs or rule-level covers) or a status."""
    from ...exporters import EXPORT_FORMATS, export_cases
    if format not in EXPORT_FORMATS:
        raise HTTPException(400, f"unknown format {format!r}; one of {', '.join(EXPORT_FORMATS)}")
    stmt = select(TestCase).where(TestCase.project_id == project.id)
    if status:
        stmt = stmt.where(TestCase.status == status)
    cases = list(db.execute(stmt.order_by(TestCase.key)).scalars())
    if ref:
        needle = ref.upper()
        cases = [
            c for c in cases
            if needle in {r.upper() for r in (c.requirement_refs or [])}
            or any(needle in cov.upper() for cov in (c.covers or []))
        ]
    base_url = (project.environments or {}).get(project.default_environment, "")
    content, filename = export_cases(cases, format, base_url=base_url)
    return Response(
        content, media_type=_EXPORT_MEDIA.get(format, "text/plain"),
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.xlsx")
def export_xlsx(
    ref: str | None = None,
    status: str | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    """The test-case register as an Excel workbook. Optionally scope by a requirement
    ref (matches requirement_refs or rule-level covers) or a status."""
    from ...reports.exports_xlsx import test_cases_workbook
    stmt = select(TestCase).where(TestCase.project_id == project.id)
    if status:
        stmt = stmt.where(TestCase.status == status)
    cases = list(db.execute(stmt.order_by(TestCase.key)).scalars())
    if ref:
        needle = ref.upper()
        cases = [c for c in cases
                 if needle in {r.upper() for r in (c.requirement_refs or [])}
                 or any(needle in cov.upper() for cov in (c.covers or []))]
    return Response(
        test_cases_workbook(cases, project_name=project.name),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="test-cases.xlsx"'},
    )


@router.post("/roundtrip")
async def roundtrip(
    file: UploadFile = File(...),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    """Round-trip: given an existing TestRail/Xray CSV export or a Gherkin .feature,
    report which imported cases are **stale** against the current requirements (their
    ref vanished), which are **unlinked**, and which requirements are **newly
    uncovered** (WO#9-C)."""
    from ...exporters import parse_imported_refs, stale_cases
    from ...intelligence.coverage import traceability_matrix

    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("latin-1", errors="replace")
    imported = parse_imported_refs(content, file.filename or "")
    # Current refs: requirement refs AND their rule ids, from the de-duplicated RTM.
    matrix = traceability_matrix(db, project.id)
    current: set[str] = set()
    for row in matrix:
        current.add(row["ref"].upper())
        for rule in row.get("rules", []):
            current.add(rule["rule_id"].upper())
    report = stale_cases(imported, current)
    report["imported_cases"] = len(imported)
    return report


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


@router.post("/import")
async def import_tests(
    file: UploadFile = File(...),
    fmt: str = Form(""),
    mapping: str = Form(""),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Import test cases (Gherkin/CSV → proposals) or results (JUnit → a run). A CSV with
    no mapping returns a suggested column mapping to confirm (the mapping dialogue)."""
    if not user.at_least(Role.AUTHOR):
        raise HTTPException(403, "importing requires the author role")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "the uploaded file is empty")
    from ...services import tms_import
    try:
        content = raw.decode("utf-8", errors="replace")
        parsed_mapping = json.loads(mapping) if mapping else None
        out = tms_import.import_file(db, project, filename=file.filename or "upload",
                                     content=content, actor=user, mapping=parsed_mapping, fmt=fmt)
    except tms_import.TmsImportError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return out


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
    # The rules this case covers, with their source anchors, so a reviewer (or an
    # agent) can jump from the test to the exact place in the spec (WO#9-C).
    covered_rules = []
    if case.covers:
        from ...models import RequirementRule
        rows = db.execute(
            select(RequirementRule).where(
                RequirementRule.project_id == project.id,
                RequirementRule.rule_id.in_(case.covers),
            )
        ).scalars()
        covered_rules = [
            {"rule_id": r.rule_id, "rule_type": r.rule_type, "technique": r.technique,
             "text": r.text, "requirement_ref": r.requirement_ref,
             "source_anchor": r.source_anchor or {}}
            for r in rows
        ]
    return {"test": _serialize_test(case), "versions": versions,
            "covered_rules": covered_rules}


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
    decision = payload.get("decision")
    if decision not in {"approve", "reject", "save"}:
        raise HTTPException(400, "decision must be 'approve', 'reject' or 'save'")
    # Editing ('save') is an author action; deciding (approve/reject) needs an approver
    # and can never be a machine; the gate is the whole point of the review board.
    if decision == "save":
        if not user.at_least(Role.AUTHOR):
            raise HTTPException(403, "editing a test requires the author role or above")
    else:
        if not user.at_least(Role.APPROVER):
            raise HTTPException(403, "approving a test requires the approver role or above")
        if user.is_machine:
            raise HTTPException(403, "an AI principal can never approve a test")

    if edits := payload.get("edits"):
        before = {"key": case.key, "title": case.title, "rationale": case.rationale}
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
        # A human correction is the best style signal the generator has (WO#9-D).
        from ...services.requirements import record_edit_example
        record_edit_example(db, project_id=project.id, before=before,
                            after={"title": case.title, "rationale": case.rationale},
                            comment=payload.get("comment", ""))

    if decision == "approve":
        case.status = TestStatus.APPROVED
    elif decision == "reject":
        case.status = TestStatus.REJECTED
    # 'save' applies the edits and leaves the case where it is (edit without deciding).
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


@router.post("/{test_id}/regenerate")
async def regenerate(
    test_id: str,
    payload: dict,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
):
    """Revise a proposal per a plain-English instruction (WO#9-D), e.g. "make the
    assertions stronger" or "add a mobile viewport case". Model-backed; the case is
    updated in place and a new version recorded."""
    case = db.get(TestCase, test_id)
    if case is None or case.project_id != project.id:
        raise HTTPException(404, "test not found")
    instruction = (payload.get("instruction") or "").strip()
    if not instruction:
        raise HTTPException(400, "an instruction is required")
    from ...ai.providers.registry import default_provider
    from ...config import settings as _settings
    from ...services.requirements import regenerate_case
    provider = default_provider() if _settings.ai_enabled else None
    result = await regenerate_case(db, project_id=project.id, case=case,
                                   instruction=instruction, provider=provider)
    if not result.get("ok"):
        db.rollback()
        return {"ok": False, "note": result.get("note", "could not regenerate")}
    db.add(TestVersion(
        test_case_id=case.id, version=case.version, snapshot=_serialize_test(case),
        change_summary=f"regenerated: {instruction[:80]}",
        author_kind="human", author_id=user.id))
    audit.record(db, action="test.regenerated", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="test_case", resource_id=case.id,
                 detail={"key": case.key, "instruction": instruction[:200]})
    db.commit()
    return {"ok": True, "test": _serialize_test(case)}


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
    # The step cache travels with the test (WO#8-B) so resolved ladders re-run at zero
    # tokens on any checkout of the repo.
    from ...engine.step_cache import yaml_for_intents
    step_cache = yaml_for_intents(db, project.id, [s.intent for s in case.steps])
    out = {"target": target, "filename": f"{case.key.lower()}.{_ext(target)}", "code": code}
    # Always return the key, even with nothing cached yet (a valid empty doc), so a
    # consumer never has to branch on its presence (WO#8 nit).
    out["step_cache_yaml"] = step_cache or "version: 1\nsteps: []\n"
    out["step_cache_filename"] = f"{case.key.lower()}.step_cache.yaml"
    return out


def _ext(target: str) -> str:
    return {"playwright": "spec.ts", "playwright_python": "py", "pytest": "py",
            "robot": "robot", "cucumber": "feature"}.get(target, "txt")
