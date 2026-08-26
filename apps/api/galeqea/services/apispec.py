"""Importing an API specification and turning it into reviewable tests.

The service layer's job here is small but load-bearing: parse, generate, and
hand the result to the same review path every other generated test takes. A
specification is an *upload from outside*, so it is scanned for injection the
way requirement documents are before any of its text reaches an agent, and the
generated cases land as ``PROPOSED`` — never approved, never runnable — until a
human decides.
"""

from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import audit
from ..core.safety import scan
from ..engine import openapi
from ..models import DocKind, Project, RequirementDoc
from ..services.requirements import persist_proposals

MAX_SPEC_BYTES = 8 * 1024 * 1024


def analyse(data: bytes | str, *, seed: str = "openapi", locale: str = "en-US",
            include_injection: bool = True) -> dict:
    """Parse a specification and describe what would be generated from it.

    Deliberately separate from :func:`import_spec` so the UI can show the plan -
    how many operations, how many cases, which spec problems were found - before
    anything is written. Approving a hundred generated cases sight unseen is the
    failure mode this avoids.
    """
    if isinstance(data, bytes):
        if len(data) > MAX_SPEC_BYTES:
            raise openapi.SpecError(
                f"specification exceeds the {MAX_SPEC_BYTES // 1024 // 1024}MB limit"
            )
        text = data.decode("utf-8", errors="replace")
    else:
        text = data

    spec = openapi.parse(openapi.load(text))
    proposals = openapi.generate(
        spec, seed=seed, locale=locale, include_injection=include_injection
    )

    by_technique: dict[str, int] = {}
    for proposal in proposals:
        by_technique[proposal["technique"]] = by_technique.get(proposal["technique"], 0) + 1

    return {
        "spec": {
            "title": spec.title,
            "version": spec.version,
            "servers": spec.servers,
            "operations": [
                {"method": op.method, "path": op.path, "operation_id": op.operation_id,
                 "summary": op.summary, "secured": op.secured, "tags": op.tags,
                 "success_status": op.success_status(),
                 "has_response_schema": op.success_schema() is not None}
                for op in spec.operations
            ],
        },
        # Spec defects, not test failures: an operation with no declared 2xx or a
        # response with no schema limits what can be asserted, and the reviewer
        # should know the coverage is thinner there rather than assume otherwise.
        "spec_issues": spec.issues,
        "proposals": proposals,
        "summary": {
            "operations": len(spec.operations),
            "proposals": len(proposals),
            "by_technique": by_technique,
            "secured_operations": sum(1 for op in spec.operations if op.secured),
            "operations_without_response_schema": sum(
                1 for op in spec.operations if op.success_schema() is None
            ),
        },
        "injection_scan": scan(text[:200_000]).as_dict(),
    }


def import_spec(
    db: Session,
    *,
    project_id: str,
    filename: str,
    data: bytes | str,
    title: str = "",
    uploaded_by: str | None = None,
    seed: str = "",
    locale: str = "en-US",
    include_injection: bool = True,
) -> dict:
    """Store the specification and file its generated cases for review."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    result = analyse(raw, seed=seed or project_id, locale=locale,
                     include_injection=include_injection)

    digest = hashlib.sha256(raw).hexdigest()
    existing = db.execute(
        select(RequirementDoc).where(
            RequirementDoc.project_id == project_id,
            RequirementDoc.content_sha256 == digest,
        )
    ).scalars().first()

    if existing is not None:
        # Re-uploading a byte-identical spec must not double every test case.
        # Returning the previous import is the honest answer, and the caller is
        # told plainly rather than being shown a silent no-op.
        return {**result, "doc": _doc_summary(existing), "created": [], "unchanged": True}

    # A specification is a supporting document, not a requirement register: it
    # says how the API behaves, not what the business asked for.
    doc = RequirementDoc(
        project_id=project_id,
        title=title or f"{result['spec']['title']} {result['spec']['version']}".strip(),
        kind=DocKind.SUPPORTING,
        source_filename=filename,
        mime_type="application/openapi",
        content=raw.decode("utf-8", errors="replace")[:1_000_000],
        content_sha256=digest,
        uploaded_by=uploaded_by,
        meta={
            "format": "openapi",
            "spec_version": result["spec"]["version"],
            "operations": result["summary"]["operations"],
            "servers": result["spec"]["servers"],
            "spec_issues": result["spec_issues"],
        },
    )
    db.add(doc)
    db.flush()

    created = persist_proposals(
        db, project_id=project_id, proposals=result["proposals"], author_kind="agent"
    )
    for case in created:
        case.provenance = {**(case.provenance or {}), "spec_doc_id": doc.id}

    audit.record(
        db,
        action="apispec.imported",
        actor_id=uploaded_by,
        actor_kind="user",
        actor_label=uploaded_by or "user",
        project_id=project_id,
        resource_type="requirement_doc",
        resource_id=doc.id,
        detail={
            "filename": filename,
            "operations": result["summary"]["operations"],
            "proposed_tests": len(created),
            "sha256": digest,
        },
    )
    db.flush()

    return {
        **result,
        "doc": _doc_summary(doc),
        "created": [
            {"id": c.id, "key": c.key, "title": c.title, "status": c.status,
             "priority": c.priority, "risk": c.risk, "tags": c.tags, "steps": len(c.steps)}
            for c in created
        ],
        "unchanged": False,
    }


def _doc_summary(doc: RequirementDoc) -> dict:
    return {
        "id": doc.id, "title": doc.title, "filename": doc.source_filename,
        "sha256": doc.content_sha256, "meta": doc.meta,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def list_specs(db: Session, project_id: str) -> list[dict]:
    rows = db.execute(
        select(RequirementDoc).where(
            RequirementDoc.project_id == project_id,
            RequirementDoc.mime_type == "application/openapi",
        ).order_by(RequirementDoc.created_at.desc())
    ).scalars()
    return [_doc_summary(d) for d in rows]


def project_base_url(db: Session, project_id: str, spec_servers: list[str]) -> str:
    """Where generated API tests should point.

    The project's configured environment wins over the specification's
    ``servers`` entry. A published spec almost always names production, and a
    generated suite that defaults to calling production - with write operations
    and injection probes in it - is a serious incident waiting to be filed as a
    feature request.
    """
    project = db.get(Project, project_id)
    if project:
        environments = project.environments or {}
        chosen = environments.get(project.default_environment) or next(iter(environments.values()), "")
        if chosen:
            return str(chosen)
    return spec_servers[0] if spec_servers else ""
