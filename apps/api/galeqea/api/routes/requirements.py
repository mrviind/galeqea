from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai.providers.registry import default_provider
from ...config import settings
from ...db import get_db
from ...models import DocKind, Project, RequirementDoc, RequirementItem, User
from ...services import requirements as service
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/requirements", tags=["requirements"])

MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("/upload")
async def upload(
    file: UploadFile = File(...),
    kind: str = Form(DocKind.REQUIREMENT),
    title: str = Form(""),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    data = await file.read()
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"file exceeds the {MAX_UPLOAD_BYTES // 1024 // 1024}MB limit")
    if not data:
        raise HTTPException(400, "the uploaded file is empty")

    result = service.ingest_document(
        db, project_id=project.id, filename=file.filename or "upload",
        data=data, title=title, kind=kind,
        mime_type=file.content_type or "", uploaded_by=user.id,
    )
    return {
        "doc": {"id": result.doc.id, "title": result.doc.title, "kind": result.doc.kind,
                "page_count": result.doc.page_count, "sha256": result.doc.content_sha256},
        "requirements": [
            {"id": i.id, "ref": i.ref, "title": i.title, "risk": i.risk, "kind": i.kind,
             "acceptance_criteria": i.acceptance_criteria, "open_questions": i.open_questions}
            for i in result.items
        ],
        "summary": result.summary,
        "warnings": result.warnings,
        # Surfaced, never silently stripped: the user must know the document
        # tried to talk to the agent.
        "injection_scan": result.injection,
    }


@router.get("/docs")
def list_docs(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(RequirementDoc).where(RequirementDoc.project_id == project.id)
        .order_by(RequirementDoc.created_at.desc())
    ).scalars()
    return [
        {"id": d.id, "title": d.title, "kind": d.kind, "filename": d.source_filename,
         "page_count": d.page_count, "items": len(d.items), "meta": d.meta,
         "created_at": d.created_at.isoformat()}
        for d in rows
    ]


@router.get("")
def list_items(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    doc_id: str | None = None,
):
    stmt = select(RequirementItem).where(RequirementItem.project_id == project.id)
    if doc_id:
        stmt = stmt.where(RequirementItem.doc_id == doc_id)
    rows = db.execute(stmt.order_by(RequirementItem.ref)).scalars()
    return [
        {"id": i.id, "ref": i.ref, "title": i.title, "text": i.text, "section": i.section,
         "kind": i.kind, "risk": i.risk, "acceptance_criteria": i.acceptance_criteria,
         "open_questions": i.open_questions}
        for i in rows
    ]


@router.post("/generate")
async def generate(
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    payload = payload or {}
    provider = default_provider() if settings.ai_enabled else None
    result = await service.generate(
        db, project_id=project.id, doc_id=payload.get("doc_id"), provider=provider
    )
    if payload.get("persist", True) and result["proposals"]:
        created = service.persist_proposals(
            db, project_id=project.id, proposals=result["proposals"]
        )
        db.commit()
        result["created"] = [
            {"id": c.id, "key": c.key, "title": c.title, "category": c.category,
             "priority": c.priority, "risk": c.risk, "rationale": c.rationale,
             "requirement_refs": c.requirement_refs, "tags": c.tags,
             "steps": len(c.steps), "charter": c.charter}
            for c in created
        ]
    for proposal in result["proposals"]:
        proposal.pop("_embedding", None)
    return result


@router.get("/traceability")
def traceability(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    from ...intelligence.coverage import traceability_matrix

    return {"matrix": traceability_matrix(db, project.id)}


@router.get("/coverage")
def coverage(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    from ...intelligence.coverage import compute

    return compute(db, project.id, persist=False)
