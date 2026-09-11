from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
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

#: The bundled sample requirements doc, the one-click "see the whole flow
#: work" template. Every new workspace can download it, or ingest it directly
#: via /sample, without having to write a spec first.
_TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "assets" / "sample-requirements.md"


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


@router.get("/template")
def download_template():
    """The bundled sample requirements doc, as a plain download, so a user can
    see the shape a good requirements document takes before writing (or
    uploading) their own."""
    if not _TEMPLATE_PATH.exists():
        raise HTTPException(404, "no sample requirements template is bundled")
    return Response(
        _TEMPLATE_PATH.read_bytes(), media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sample-requirements.md"'},
    )


@router.post("/sample")
async def use_sample(
    payload: dict | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """One click: ingest the bundled sample requirements doc exactly as if it
    had been uploaded, generate its test proposals, and, unless told not
    to, run the full floor (analyze the target, plan, approve, execute,
    report) against it. This is the "reset and try the whole flow" button:
    every workspace can see a complete Test Plan → test cases → run →
    Test Completion Report without writing a spec or crawling a real site
    first."""
    if not _TEMPLATE_PATH.exists():
        raise HTTPException(404, "no sample requirements template is bundled")
    payload = payload or {}
    data = _TEMPLATE_PATH.read_bytes()

    if payload.get("run", True):
        from ...services.full_floor import floor_from_requirements

        target = (payload.get("target") or "").strip() or settings.demo_target_url
        result = await floor_from_requirements(
            db, project=project, target=target, doc_bytes=data,
            doc_filename=_TEMPLATE_PATH.name, decider=user,
            doc_title="Sample Requirements: TaskFlow (template)",
            page_limit=payload.get("page_limit"),
        )
        db.commit()
        return result

    result = service.ingest_document(
        db, project_id=project.id, filename=_TEMPLATE_PATH.name,
        data=data, title="Sample Requirements: TaskFlow (template)",
        mime_type="text/markdown", uploaded_by=user.id,
    )
    generated = {"proposals": 0, "created": 0}
    if result.items:
        provider = default_provider() if settings.ai_enabled else None
        gen = await service.generate(db, project_id=project.id, doc_id=result.doc.id,
                                     provider=provider)
        created = service.persist_proposals(
            db, project_id=project.id, proposals=gen.get("proposals", [])
        ) if gen.get("proposals") else []
        db.commit()
        generated = {"proposals": len(gen.get("proposals", [])), "created": len(created)}
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
        "injection_scan": result.injection,
        "generated": generated,
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
         "archived": bool((d.meta or {}).get("archived")),
         "created_at": d.created_at.isoformat()}
        for d in rows
    ]


@router.post("/docs/{doc_id}/archive")
def archive_doc(doc_id: str, project: Project = Depends(get_project),
                db: Session = Depends(get_db)):
    result = service.archive_requirement_doc(db, project_id=project.id, doc_id=doc_id)
    if not result.get("ok"):
        raise HTTPException(404, result.get("error", "not found"))
    db.commit()
    return result


@router.post("/dedupe")
def dedupe_docs(payload: dict | None = None, project: Project = Depends(get_project),
                db: Session = Depends(get_db)):
    payload = payload or {}
    result = service.dedupe_requirement_docs(
        db, project_id=project.id, apply=bool(payload.get("apply")))
    db.commit()
    return result


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
         "open_questions": i.open_questions, "source_anchor": i.source_anchor or {}}
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
