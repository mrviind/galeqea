"""The Full Floor: point GaleQEA at a site (or a requirement doc) and get the whole
QA arc: analyse → plan → approve → run → evidence → a client-ready report.

This is the product's headline flow. It is deliberately two-phase around a human gate:
``analyze`` crawls the target, decides which pages to cover, and lays out the plan
(pages, elements, test types, cases) for review; nothing is executed until a person
approves it. ``run_and_report`` then executes every case, captures a screenshot for
each failure, and renders the branded Word report.

The default target is the configured demo site, so a brand-new user with nothing set
up can click one button and watch the full floor happen.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Journey, Project, User


def analyze(db: Session, project: Project, target: str = "",
            *, page_limit: int | None = None, pages: list[str] | None = None) -> dict:
    """Phase 1: crawl the target, build the typed plan, file it for approval.
    Returns a review-ready summary (pages, elements, test types, counts)."""
    from . import plan_approval

    target = (target or settings.demo_target_url).strip()
    journey, req = plan_approval.propose(db, project, target, page_limit=page_limit, pages=pages)
    return {"ok": True, "target": journey.target, "journey_id": journey.id,
            "approval_id": req.id, "plan": plan_summary(journey)}


def plan_summary(journey: Journey) -> dict:
    """A human-readable digest of a journey's plan: pages, elements, types, cases."""
    plan = journey.plan or {}
    discovery = journey.discovery or {}
    typed = plan.get("typed", {})
    types = [{"type": r["key"], "count": r.get("count", 0)}
             for r in typed.get("types", []) if r.get("enabled")]
    return {
        "target": journey.target,
        "pages": plan.get("pages", []),
        "page_count": len(plan.get("pages", [])),
        "forms": discovery.get("forms", 0),
        "elements": _element_digest(discovery),
        "functional_checks": plan.get("functional", []),
        "non_functional_checks": plan.get("non_functional", []),
        "test_types": types,
        "test_count": typed.get("totals", {}).get("test_count", 0),
        "notes": plan.get("notes", []),
    }


def _element_digest(discovery: dict) -> dict:
    """A coarse element inventory from the crawl, for the plan preview."""
    return {
        "pages_discovered": discovery.get("discovered", len(discovery.get("pages", []))),
        "forms": discovery.get("forms", 0),
        "method": discovery.get("method", "http"),
    }


async def floor_from_requirements(
    db: Session, *, project: Project, target: str, doc_bytes: bytes, doc_filename: str,
    decider: User, doc_title: str = "", page_limit: int | None = None,
    pages: list[str] | None = None, timeout: float = 300.0,
) -> dict:
    """One flow for "here's my spec and my app": ingest the requirement document,
    generate its cases (spec coverage), then crawl + run the executable floor against
    the target (a live site or a localhost app). The report ties both together:
    what was executed, with evidence, and how the requirements are covered (WO-owner).
    """
    from ..ai.providers.registry import default_provider
    from . import requirements as req

    ingested = req.ingest_document(db, project_id=project.id, filename=doc_filename,
                                   data=doc_bytes, title=doc_title)
    provider = default_provider() if settings.ai_enabled else None
    gen = await req.generate(db, project_id=project.id, provider=provider)
    cases = req.persist_proposals(db, project_id=project.id,
                                  proposals=gen.get("proposals", [])) if gen.get("proposals") else []
    db.commit()

    analysis = analyze(db, project, target, page_limit=page_limit, pages=pages)
    result = await run_and_report(db, project=project, journey_id=analysis["journey_id"],
                                  decider=decider, timeout=timeout)
    result["requirements"] = {
        "doc_id": ingested.doc.id,
        "doc_title": ingested.doc.title,
        "requirements": len(ingested.items),
        "cases_generated": len(cases),
    }
    result["plan"] = analysis["plan"]
    return result


async def run_and_report(db: Session, *, project: Project, journey_id: str,
                         decider: User, timeout: float = 300.0) -> dict:
    """Phase 2: approve the plan (as a human), run every case, and report. Returns
    the run id and the report locations. The Word report is fetched separately."""
    from ..core import approvals
    from ..reports.common import api_href
    from . import plan_approval
    from .runs import start_run, wait_for_run

    journey = db.get(Journey, journey_id)
    if journey is None:
        return {"ok": False, "error": "no such journey"}
    pending = plan_approval.pending_for_journey(db, journey)
    if pending is not None:
        approvals.approve(db, pending.id, decider)
        db.flush()
    journey = db.get(Journey, journey_id)
    keys = journey.test_ids or []
    if not keys:
        return {"ok": False, "error": "the approved plan produced no runnable tests"}

    started = await start_run(
        db, project_id=project.id, selection={"keys": keys},
        base_url=journey.target, trigger="floor",
        title=f"Full Floor: {journey.target}")
    run_id = started.id
    journey.run_id = run_id
    db.commit()
    await wait_for_run(run_id, timeout=timeout)

    from ..models import Run
    db.expire_all()                      # the run finished in another session
    run = db.get(Run, run_id)
    return {
        "ok": True,
        "run_id": run_id,
        "run_number": run.number,
        "status": run.status,
        "reports": {
            "docx": api_href(project.id, "runs", run_id, "report.docx"),
            "xlsx": api_href(project.id, "runs", run_id, "report.xlsx"),
            "html_rtm": api_href(project.id, "traceability", "report.html"),
            "xlsx_rtm": api_href(project.id, "traceability", "report.xlsx"),
            "json": api_href(project.id, "runs", run_id, "report.json"),
            "test_plan_docx": api_href(project.id, "test-plan.docx") + f"?journey_id={journey_id}",
        },
    }
