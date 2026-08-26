"""Import an OpenAPI specification and generate API tests from it."""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ...db import get_db
from ...engine.openapi import SpecError
from ...models import Project, User
from ...services import apispec as service
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/api-spec", tags=["api-spec"])


@router.get("")
def list_specs(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    return service.list_specs(db, project.id)


@router.post("/analyze")
async def analyze(
    file: UploadFile | None = File(None),
    text: str = Form(""),
    include_injection: bool = Form(True),
    locale: str = Form("en-US"),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    """Preview what would be generated, writing nothing.

    Separate from import on purpose: a hundred proposals is more than anyone
    reviews carefully, and the operation count plus the spec's own defects are
    what decide whether to import the whole document or narrow it first.
    """
    data = await file.read() if file is not None else text.encode()
    if not data.strip():
        raise HTTPException(400, "no specification was provided")
    try:
        result = service.analyse(
            data, seed=project.id, locale=locale, include_injection=include_injection
        )
    except SpecError as exc:
        raise HTTPException(422, str(exc)) from exc
    result["base_url"] = service.project_base_url(db, project.id, result["spec"]["servers"])
    return result


@router.post("/import")
async def import_spec(
    file: UploadFile | None = File(None),
    text: str = Form(""),
    title: str = Form(""),
    include_injection: bool = Form(True),
    locale: str = Form("en-US"),
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    data = await file.read() if file is not None else text.encode()
    if not data.strip():
        raise HTTPException(400, "no specification was provided")
    try:
        result = service.import_spec(
            db, project_id=project.id,
            filename=(file.filename if file is not None else "spec.yaml") or "spec.yaml",
            data=data, title=title, uploaded_by=user.id,
            locale=locale, include_injection=include_injection,
        )
    except SpecError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    result["base_url"] = service.project_base_url(db, project.id, result["spec"]["servers"])
    return result
