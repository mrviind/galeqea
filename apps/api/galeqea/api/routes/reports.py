"""AI-readable report endpoints.

Every report the platform can produce is available in three shapes at a stable URL,
selected by file extension so an agent (or a human, or curl) picks the rendering it
wants without content-negotiation headers:

    GET .../runs/{id}/report.json | .md | .junit.xml
    GET .../coverage/report.json | .md
    GET .../traceability/report.json | .md | .html
    GET .../flaky/report.json | .md
    GET .../runs/{id}/rca/report.json | .md
    GET .../heals/report.json | .md

JSON is the machine source of truth; Markdown is the LLM-friendly rendering; JUnit
XML (runs only) is what CI already understands.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from ...db import get_db
from ...models import Project, Run
from ...reports import coverage as coverage_report
from ...reports import flaky as flaky_report
from ...reports import heals as heals_report
from ...reports import rca as rca_report
from ...reports import runs as run_report
from ...reports import traceability as traceability_report
from ..deps import get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["reports"])


def _markdown(text: str) -> PlainTextResponse:
    return PlainTextResponse(text, media_type="text/markdown; charset=utf-8")


def _xml(text: str) -> Response:
    return Response(text, media_type="application/xml; charset=utf-8")


def _resolve_run(run_id: str, db: Session, project: Project) -> Run:
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        raise HTTPException(404, "run not found")
    return run


# --------------------------------------------------------------------------- #
# Run report: JSON / Markdown / JUnit
# --------------------------------------------------------------------------- #
@router.get("/runs/{run_id}/report.json")
def run_json(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    from ...reports import report_v2
    return JSONResponse(report_v2.build(db, project, _resolve_run(run_id, db, project)))


@router.get("/runs/{run_id}/report.md")
def run_md(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    from ...reports import report_v2
    rep = report_v2.build(db, project, _resolve_run(run_id, db, project))
    return _markdown(report_v2.to_markdown(rep))


@router.get("/runs/{run_id}/report.junit.xml")
def run_junit(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    from ...reports import report_v2
    rep = report_v2.build(db, project, _resolve_run(run_id, db, project))
    return _xml(run_report.run_report_junit(rep))


@router.get("/runs/{run_id}/report.docx")
def run_docx(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    """A branded, client-ready Word report: cover page, executive summary, scope,
    the test-case register, and every failure with its screenshot evidence."""
    from ...reports.report_docx import build_run_docx
    run = _resolve_run(run_id, db, project)
    # Pull the target + discovered pages/plan from the run's journey, if any.
    pages, plan, target = _run_scope(db, project, run)
    data = build_run_docx(db, project, run, target=target, pages=pages, plan=plan)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition":
                 f'attachment; filename="test-report-run-{run.number}.docx"'},
    )


@router.get("/runs/{run_id}/report.xlsx")
def run_xlsx(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    """The Test Completion Report as an Excel workbook (Summary / Results / By-Type)."""
    from ...reports.exports_xlsx import tcr_workbook
    run = _resolve_run(run_id, db, project)
    return Response(
        tcr_workbook(db, project, run),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="test-completion-report-run-{run.number}.xlsx"'},
    )


def _run_scope(db, project, run) -> tuple[list[str], dict, str]:
    """Best-effort target/pages/plan for the report from the run's journey."""
    pages: list[str] = []
    plan: dict = {}
    target = run.base_url or (project.environments or {}).get(project.default_environment, "")
    try:
        from sqlalchemy import select

        from ...models import Journey
        jrn = db.execute(select(Journey).where(Journey.run_id == run.id)).scalars().first()
        if jrn is None and target:
            jrn = db.execute(
                select(Journey).where(Journey.project_id == project.id, Journey.target == target)
                .order_by(Journey.created_at.desc())).scalars().first()
        if jrn:
            target = target or jrn.target
            meta = jrn.discovery or {}
            pages = meta.get("pages", []) if isinstance(meta, dict) else []
            plan = jrn.plan if isinstance(jrn.plan, dict) else {}
    except Exception:  # noqa: BLE001
        pass
    return pages, plan, target


# --------------------------------------------------------------------------- #
# RCA report (per run)
# --------------------------------------------------------------------------- #
@router.get("/runs/{run_id}/rca/report.json")
def rca_json(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return JSONResponse(rca_report.build_rca_report(db, project, _resolve_run(run_id, db, project)))


@router.get("/runs/{run_id}/rca/report.md")
def rca_md(run_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)):
    rep = rca_report.build_rca_report(db, project, _resolve_run(run_id, db, project))
    return _markdown(rca_report.rca_report_markdown(rep))


# --------------------------------------------------------------------------- #
# Project-level reports: coverage / traceability / flaky / heals
# --------------------------------------------------------------------------- #
@router.get("/coverage/report.json")
def coverage_json(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return JSONResponse(coverage_report.build_coverage_report(db, project))


@router.get("/coverage/report.md")
def coverage_md(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return _markdown(coverage_report.coverage_report_markdown(coverage_report.build_coverage_report(db, project)))


@router.get("/traceability/report.json")
def traceability_json(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return JSONResponse(traceability_report.build_traceability_report(db, project))


@router.get("/traceability/report.md")
def traceability_md(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return _markdown(traceability_report.traceability_report_markdown(traceability_report.build_traceability_report(db, project)))


@router.get("/traceability/report.html")
def traceability_html(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    report = traceability_report.build_traceability_report(db, project)
    return Response(traceability_report.traceability_report_html(report),
                    media_type="text/html; charset=utf-8")


@router.get("/test-plan.docx")
def test_plan_docx(journey_id: str = "", db: Session = Depends(get_db),
                   project: Project = Depends(get_project)):
    """The corporate Test Plan (IEEE-829 style) for a journey, or the latest one."""
    from sqlalchemy import select

    from ...models import Journey
    from ...reports.test_plan_docx import build_test_plan_docx
    jrn = None
    if journey_id:
        jrn = db.get(Journey, journey_id)
    if jrn is None:
        jrn = db.execute(
            select(Journey).where(Journey.project_id == project.id, Journey.plan.isnot(None))
            .order_by(Journey.created_at.desc())).scalars().first()
    data = build_test_plan_docx(db, project, jrn)
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="test-plan-{project.key.lower()}.docx"'},
    )


@router.get("/traceability/report.xlsx")
def traceability_xlsx(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    """The Requirements Traceability Matrix as an Excel workbook."""
    from ...intelligence.coverage import traceability_matrix
    from ...reports.exports_xlsx import rtm_workbook
    matrix = traceability_matrix(db, project.id)
    return Response(
        rtm_workbook(matrix, project_name=project.name),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="traceability-matrix.xlsx"'},
    )


@router.get("/flaky/report.json")
def flaky_json(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return JSONResponse(flaky_report.build_flaky_report(db, project))


@router.get("/flaky/report.md")
def flaky_md(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return _markdown(flaky_report.flaky_report_markdown(flaky_report.build_flaky_report(db, project)))


@router.get("/heals/report.json")
def heals_json(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return JSONResponse(heals_report.build_heals_report(db, project))


@router.get("/heals/report.md")
def heals_md(db: Session = Depends(get_db), project: Project = Depends(get_project)):
    return _markdown(heals_report.heals_report_markdown(heals_report.build_heals_report(db, project)))
