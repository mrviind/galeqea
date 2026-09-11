"""Publish a release report to Confluence, updating the same page in place."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..integrations import confluence
from ..integrations.base import IntegrationError
from ..models import Project, ReportPage
from ..reports import release_report
from ..services import release


class PublishError(RuntimeError):
    """Safe-to-surface problem publishing a report."""


def publish_release_report(db: Session, project: Project, *, version: str, actor,
                           space_key: str = "") -> dict:
    milestone = release.milestone_for(db, project.id, version)
    if milestone is None:
        raise PublishError(f"no release {version}")

    title = f"GaleQEA · Release {milestone.version}"
    storage = release_report.to_confluence_storage(db, milestone)

    existing = db.execute(select(ReportPage).where(
        ReportPage.project_id == project.id, ReportPage.milestone_id == milestone.id)
    ).scalars().first()
    try:
        out = confluence.publish_page(
            db, project_id=project.id, title=title, storage_html=storage,
            space_key=space_key or (existing.space_key if existing else ""),
            known_page_id=existing.page_id if existing else "",
            known_version=existing.version if existing else 0)
    except IntegrationError as exc:
        raise PublishError(str(exc)) from exc

    row = existing or ReportPage(project_id=project.id, milestone_id=milestone.id)
    row.space_key = space_key or row.space_key
    row.page_id = out["page_id"]
    row.version = out["version"]
    row.title = title
    row.url = out["url"]
    if existing is None:
        db.add(row)
    db.flush()
    audit.record(db, action="report.published", actor_id=getattr(actor, "id", None),
                 actor_label=getattr(actor, "email", ""), project_id=project.id,
                 resource_type="milestone", resource_id=milestone.id,
                 detail={"version": milestone.version, "space": row.space_key,
                         "page_id": row.page_id, "page_version": row.version})
    return {"ok": True, "version": milestone.version, "page_id": row.page_id,
            "page_version": row.version, "url": row.url, "space": row.space_key}


def publish_card(row: ReportPage) -> dict:
    return {"type": "report_page_card", "title": row.title, "url": row.url,
            "version": row.version, "space": row.space_key}
