"""Analysis endpoints: healing, flakiness, RCA, selection, anomalies, app model."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...ai.providers.registry import default_provider
from ...config import settings
from ...db import get_db
from ...models import (
    AnomalyRecord,
    AppElement,
    AppScreen,
    HealEvent,
    JudgeVerdict,
    Project,
    RCAReport,
    RunTest,
    User,
)
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}", tags=["intelligence"])


@router.get("/heals")
def list_heals(
    project: Project = Depends(get_project), db: Session = Depends(get_db),
    status: str = "proposed",
):
    rows = db.execute(
        select(HealEvent).where(
            HealEvent.project_id == project.id, HealEvent.status == status
        ).order_by(HealEvent.created_at.desc()).limit(200)
    ).scalars()
    return [
        {"id": h.id, "strategy": h.strategy, "old_locator": h.old_locator,
         "new_locator": h.new_locator, "score": round(h.score, 3), "status": h.status,
         "used_transiently": h.used_transiently, "element_id": h.element_id,
         "test_case_id": h.test_case_id, "candidates": h.candidates[:4],
         "evidence": h.evidence, "at": h.created_at.isoformat(),
         # How many tests one approval will repair - the number that makes the
         # App Model worth having.
         "affected_tests": len((h.evidence or {}).get("affected_tests") or []) or 1}
        for h in rows
    ]


@router.post("/heals/{heal_id}/decide")
def decide_heal(
    heal_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Approving a heal repairs every test that references the element."""
    from ...core import audit
    from ...engine.healing import HealingEngine
    from ...models import Role

    if not user.at_least(Role.APPROVER) or user.is_machine:
        raise HTTPException(403, "applying a heal requires a human approver")
    event = db.get(HealEvent, heal_id)
    if event is None or event.project_id != project.id:
        raise HTTPException(404, "heal event not found")

    decision = payload.get("decision")
    if decision not in {"approve", "reject"}:
        raise HTTPException(400, "decision must be 'approve' or 'reject'")

    event.status = "approved" if decision == "approve" else "rejected"
    event.reviewed_by = user.id
    db.flush()

    result = {}
    if decision == "approve":
        result = HealingEngine(db, project_id=project.id).apply_to_model(
            heal_id, approved_by=user.id
        )
    audit.record(db, action=f"heal.{decision}d", actor_id=user.id, actor_label=user.email,
                 project_id=project.id, resource_type="heal_event", resource_id=heal_id,
                 detail={"strategy": event.strategy, "from": event.old_locator,
                         "to": event.new_locator, **result})
    db.commit()
    return {"status": event.status, **result}


@router.get("/flaky")
def flaky(project: Project = Depends(get_project), db: Session = Depends(get_db),
          min_score: float = 0.2):
    from ...intelligence.flaky import assess, quarantine_candidates
    from ...models import TestCase, TestStat

    stats = list(
        db.execute(select(TestStat).where(TestStat.project_id == project.id)).scalars()
    )
    rows = []
    for stat in stats:
        verdict = assess(stat)
        if verdict.score < min_score:
            continue
        case = db.get(TestCase, stat.test_case_id)
        rows.append({
            "test_case_id": stat.test_case_id,
            "key": case.key if case else "", "title": case.title if case else "",
            "runs": stat.runs, "passes": stat.passes, "failures": stat.failures,
            "pass_rate": round(stat.passes / max(1, stat.runs), 3),
            "mean_duration_ms": round(stat.mean_duration_ms),
            "p95_duration_ms": round(stat.p95_duration_ms),
            "heal_count": stat.heal_count,
            "window": (stat.outcome_window or [])[:20],
            **verdict.as_dict(),
        })
    rows.sort(key=lambda r: r["score"], reverse=True)
    return {"flaky": rows, "quarantine_candidates": quarantine_candidates(db, project.id)}


@router.post("/rca")
async def rca(
    payload: dict, project: Project = Depends(get_project), db: Session = Depends(get_db)
):
    from ...intelligence.rca import analyze

    run_test = db.get(RunTest, payload.get("run_test_id", ""))
    if run_test is None:
        raise HTTPException(404, "result not found")
    report = await analyze(
        db, run_test,
        provider=default_provider() if settings.ai_enabled else None,
        project_id=project.id,
    )
    db.commit()
    return {"id": report.id, "category": report.category, "summary": report.summary,
            "confidence": report.confidence, "hypotheses": report.hypotheses,
            "evidence": report.evidence, "suggested_fix": report.suggested_fix,
            "generated_by": report.generated_by, "model": report.model}


@router.get("/rca")
def list_rca(project: Project = Depends(get_project), db: Session = Depends(get_db),
             limit: int = 50):
    rows = db.execute(
        select(RCAReport).where(RCAReport.project_id == project.id)
        .order_by(RCAReport.created_at.desc()).limit(limit)
    ).scalars()
    return [
        {"id": r.id, "category": r.category, "summary": r.summary,
         "confidence": r.confidence, "run_test_id": r.run_test_id,
         "ticket_ref": r.ticket_ref, "generated_by": r.generated_by,
         "at": r.created_at.isoformat()}
        for r in rows
    ]


@router.post("/select")
def select_for_change(
    payload: dict, project: Project = Depends(get_project), db: Session = Depends(get_db)
):
    from ...intelligence.selection import select_for_change as run_selection

    return run_selection(
        db, project.id,
        changed_paths=payload.get("changed_paths") or [],
        budget=payload.get("budget"),
        min_score=payload.get("min_score", 0.25),
    )


@router.get("/anomalies")
def anomalies(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(AnomalyRecord).where(AnomalyRecord.project_id == project.id)
        .order_by(AnomalyRecord.created_at.desc()).limit(100)
    ).scalars()
    return [
        {"id": a.id, "metric": a.metric, "observed": a.observed, "expected": a.expected,
         "sigma": round(a.deviation_sigma, 2), "severity": a.severity,
         "detail": a.detail, "run_id": a.run_id, "acknowledged": a.acknowledged,
         "at": a.created_at.isoformat()}
        for a in rows
    ]


@router.get("/app-model")
def app_model(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    """The discovered digital twin: screens, elements and their stability."""
    screens = list(
        db.execute(select(AppScreen).where(AppScreen.project_id == project.id)).scalars()
    )
    elements = list(
        db.execute(select(AppElement).where(AppElement.project_id == project.id)).scalars()
    )
    by_screen: dict[str, list] = {}
    for element in elements:
        by_screen.setdefault(element.screen_id, []).append({
            "id": element.id, "intent": element.intent, "role": element.role,
            "accessible_name": element.accessible_name,
            "locators": element.locators, "confidence": round(element.confidence, 2),
            "stability": round(element.stability_score, 2), "heal_count": element.heal_count,
            "deprecated": element.deprecated,
        })
    return {
        "screens": [
            {"id": s.id, "name": s.name, "url_pattern": s.url_pattern,
             "visit_count": s.visit_count, "elements": by_screen.get(s.id, [])}
            for s in screens
        ],
        "element_count": len(elements),
        "fragile_elements": sorted(
            [e for row in by_screen.values() for e in row if e["heal_count"] > 0],
            key=lambda e: e["heal_count"], reverse=True,
        )[:20],
    }


@router.get("/judge-verdicts")
def judge_verdicts(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(JudgeVerdict).where(JudgeVerdict.project_id == project.id)
        .order_by(JudgeVerdict.created_at.desc()).limit(100)
    ).scalars()
    return [
        {"id": v.id, "question": v.question, "verdict": v.verdict,
         "confidence": round(v.confidence, 2), "reasoning": v.reasoning,
         "human_override": v.human_override, "model": v.model,
         "samples": len(v.samples or []), "run_test_id": v.run_test_id,
         "at": v.created_at.isoformat()}
        for v in rows
    ]


@router.post("/judge-verdicts/{verdict_id}/override")
def override_verdict(
    verdict_id: str, payload: dict, db: Session = Depends(get_db),
    project: Project = Depends(get_project), user: User = Depends(current_user),
):
    from ...intelligence.judge import override

    try:
        record = override(db, verdict_id, decision=payload.get("decision", ""), user_id=user.id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    db.commit()
    return {"id": record.id, "human_override": record.human_override}


@router.get("/memory")
def list_memory(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    from ...ai.memory import MemoryStore

    return {"memory": MemoryStore(db, project.id).export()}


@router.delete("/memory/{memory_id}")
def forget(
    memory_id: str, db: Session = Depends(get_db), project: Project = Depends(get_project)
):
    from ...ai.memory import MemoryStore

    if not MemoryStore(db, project.id).forget(memory_id):
        raise HTTPException(404, "memory item not found")
    db.commit()
    return {"forgotten": memory_id}


# --------------------------------------------------------------------------- #
# Exploratory testing
# --------------------------------------------------------------------------- #
@router.get("/explore")
def list_explorations(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    from ...models import ExplorationFinding, ExplorationSession

    sessions = list(
        db.execute(
            select(ExplorationSession).where(ExplorationSession.project_id == project.id)
            .order_by(ExplorationSession.created_at.desc()).limit(30)
        ).scalars()
    )
    counts: dict[str, int] = {}
    for finding in db.execute(
        select(ExplorationFinding).where(ExplorationFinding.project_id == project.id)
    ).scalars():
        counts[finding.session_id] = counts.get(finding.session_id, 0) + 1

    return [
        {"id": s.id, "charter": s.charter, "status": s.status, "strategy": s.strategy,
         "model": s.model, "environment": s.environment, "base_url": s.base_url,
         "steps_taken": s.steps_taken, "max_steps": s.max_steps,
         "screens_seen": s.screens_seen, "summary": s.summary,
         "findings": counts.get(s.id, 0), "trail": s.trail[-30:],
         "created_at": s.created_at.isoformat(),
         "finished_at": s.finished_at.isoformat() if s.finished_at else None}
        for s in sessions
    ]


@router.post("/explore")
async def start_exploration(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    from ...services import exploration

    try:
        session = await exploration.start(
            db, project_id=project.id,
            charter=payload.get("charter", ""),
            environment=payload.get("environment", ""),
            max_steps=int(payload.get("max_steps", 30)),
            # Off by default. On a staging environment the submit button is
            # where the interesting behaviour is; on production it is not.
            allow_transactional=bool(payload.get("allow_transactional", False)),
            started_by=user.id,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": session.id, "status": session.status, "strategy": session.strategy,
            "charter": session.charter, "max_steps": session.max_steps}


@router.get("/findings")
def list_findings(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    status: str = "new",
    session_id: str | None = None,
):
    from ...models import ExplorationFinding

    stmt = select(ExplorationFinding).where(ExplorationFinding.project_id == project.id)
    if status != "all":
        stmt = stmt.where(ExplorationFinding.status == status)
    if session_id:
        stmt = stmt.where(ExplorationFinding.session_id == session_id)

    rows = db.execute(stmt.order_by(ExplorationFinding.created_at.desc()).limit(200)).scalars()
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    findings = [
        {"id": f.id, "kind": f.kind, "severity": f.severity, "title": f.title,
         "detail": f.detail, "url": f.url, "confidence": round(f.confidence, 2),
         "found_by": f.found_by, "status": f.status, "session_id": f.session_id,
         "reproduction": f.reproduction, "evidence": f.evidence,
         "occurrences": (f.evidence or {}).get("occurrences", 1),
         "promoted_test_id": f.promoted_test_id, "at": f.created_at.isoformat()}
        for f in rows
    ]
    # Worst first: a findings list nobody can triage in order is a backlog.
    findings.sort(key=lambda f: (severity_rank.get(f["severity"], 3), -f["confidence"]))
    return findings


@router.post("/findings/{finding_id}/decide")
def decide_finding(
    finding_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Triage a finding: accept it, dismiss it, or promote it into a test."""
    from ...core import audit
    from ...models import (
        ExplorationFinding,
        StepAction,
        TestCase,
        TestCategory,
        TestStatus,
        TestStep,
    )
    from ...models.base import utcnow

    finding = db.get(ExplorationFinding, finding_id)
    if finding is None or finding.project_id != project.id:
        raise HTTPException(404, "finding not found")

    decision = payload.get("decision")
    if decision not in {"accept", "dismiss", "promote"}:
        raise HTTPException(400, "decision must be 'accept', 'dismiss' or 'promote'")

    # Promotion is idempotent. A double-click on "Promote to a test" produced two
    # identical regression tests, which is a small bug with an annoying blast
    # radius: someone has to notice and delete one.
    if decision == "promote" and finding.promoted_test_id:
        existing = db.get(TestCase, finding.promoted_test_id)
        if existing is not None:
            return {
                "status": finding.status,
                "test_id": existing.id,
                "key": existing.key,
                "note": "already promoted — returning the existing test",
            }

    finding.reviewed_by = user.id
    result: dict = {}

    if decision == "promote":
        # A finding becomes a regression test: the reproduction trail is
        # already an ordered list of actions, which is exactly what a test is.
        from sqlalchemy import func

        count = db.execute(
            select(func.count()).select_from(TestCase).where(TestCase.project_id == project.id)
        ).scalar_one()
        case = TestCase(
            project_id=project.id,
            key=f"{project.key.upper()}-T-{count + 1:04d}",
            title=f"Regression: {finding.title}"[:400],
            description=finding.detail,
            category=TestCategory.MANUAL,
            status=TestStatus.PROPOSED,
            priority="high" if finding.severity == "high" else "medium",
            risk=finding.severity,
            rationale=(
                f"Found by exploratory testing on {finding.url or 'the application'}. "
                "Promoted so the defect cannot return unnoticed."
            ),
            tags=["exploratory", finding.kind],
            provenance={
                "origin": "exploration",
                "finding_id": finding.id,
                "found_by": finding.found_by,
                "promoted_by": user.id,
                "promoted_at": utcnow().isoformat(),
            },
        )
        db.add(case)
        db.flush()

        for index, action in enumerate(finding.reproduction or []):
            db.add(TestStep(
                test_case_id=case.id, index=index, action=StepAction.NOTE,
                intent=f"{action.get('action')} {action.get('target', '')}".strip()[:400],
                expected=action.get("url", ""),
            ))
        db.add(TestStep(
            test_case_id=case.id, index=len(finding.reproduction or []),
            action=StepAction.NOTE,
            intent=f"Confirm the defect is gone: {finding.title}"[:400],
            expected=finding.detail[:400],
        ))
        db.flush()
        finding.promoted_test_id = case.id
        finding.status = "promoted"
        result = {"test_id": case.id, "key": case.key}
    else:
        finding.status = "accepted" if decision == "accept" else "dismissed"

    audit.record(
        db, action=f"finding.{decision}d", actor_id=user.id, actor_label=user.email,
        project_id=project.id, resource_type="exploration_finding", resource_id=finding.id,
        detail={"kind": finding.kind, "severity": finding.severity, **result},
    )
    db.commit()
    return {"status": finding.status, **result}


# --------------------------------------------------------------------------- #
# Visual regression review
# --------------------------------------------------------------------------- #
@router.get("/visual")
def list_visual(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    status: str = "new",
):
    from ...models import VisualBaseline, VisualComparison

    stmt = select(VisualComparison).where(VisualComparison.project_id == project.id)
    if status != "all":
        stmt = stmt.where(VisualComparison.status == status)
    rows = list(
        db.execute(stmt.order_by(VisualComparison.created_at.desc()).limit(100)).scalars()
    )

    # Current baseline per screen, plus how many versions preceded it - the
    # panel claims baselines are versioned rather than overwritten, and a claim
    # with no visible evidence is just a sentence.
    history: dict[str, list] = {}
    for b in db.execute(
        select(VisualBaseline).where(VisualBaseline.project_id == project.id)
        .order_by(VisualBaseline.version)
    ).scalars():
        history.setdefault(b.name, []).append(b)
    baselines = {name: versions[-1] for name, versions in history.items()}

    rank = {"breaking": 0, "notable": 1, "cosmetic": 2, "none": 3}
    rows.sort(key=lambda c: (rank.get(c.severity, 4), -c.changed_pct))

    return {
        "comparisons": [
            {
                "id": c.id, "name": c.name, "severity": c.severity, "summary": c.summary,
                "status": c.status, "url": c.url, "browser": c.browser,
                "structural": c.structural, "regions": c.regions,
                "changed_pct": round(c.changed_pct, 2),
                "perceptual_distance": c.perceptual_distance,
                "dimensions_changed": c.dimensions_changed,
                "has_diff_image": bool(c.diff_path),
                "judged_by": c.judged_by, "run_id": c.run_id,
                "reviewed_by": c.reviewed_by, "review_comment": c.review_comment,
                "at": c.created_at.isoformat(),
            }
            for c in rows
        ],
        "baselines": [
            {"name": b.name, "version": b.version, "browser": b.browser,
             "approved_by": b.approved_by, "at": b.created_at.isoformat(),
             "superseded": len(history[b.name]) - 1}
            for b in baselines.values()
        ],
    }


@router.get("/visual/{comparison_id}/image")
def visual_image(
    comparison_id: str,
    side: str = "candidate",
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    """Serve one side of a comparison. Path-contained, like run artifacts."""
    from fastapi.responses import FileResponse

    from ...config import settings
    from ...models import VisualComparison

    comparison = db.get(VisualComparison, comparison_id)
    if comparison is None or comparison.project_id != project.id:
        raise HTTPException(404, "comparison not found")

    source = {
        "candidate": comparison.candidate_path,
        "baseline": comparison.baseline_path,
        "diff": comparison.diff_path,
    }.get(side)
    if not source:
        raise HTTPException(404, f"no {side} image for this comparison")

    path = Path(source).resolve()
    root = Path(settings.artifacts_dir).resolve()
    # A stored path must never be able to serve a file from outside the
    # artifacts root, whatever wrote it.
    if not str(path).startswith(str(root)) or not path.exists():
        raise HTTPException(404, "image is missing or outside the artifact root")
    return FileResponse(path, media_type="image/png")


@router.post("/visual/{comparison_id}/decide")
def decide_visual(
    comparison_id: str,
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    """Accept the change as the new baseline, or reject it as a defect."""
    from ...core import audit
    from ...intelligence.visual import approve_baseline, reject_change
    from ...models import Role, VisualComparison

    if not user.at_least(Role.APPROVER) or user.is_machine:
        raise HTTPException(403, "updating a visual baseline requires a human approver")

    comparison = db.get(VisualComparison, comparison_id)
    if comparison is None or comparison.project_id != project.id:
        raise HTTPException(404, "comparison not found")

    decision = payload.get("decision")
    if decision not in {"accept", "reject"}:
        raise HTTPException(400, "decision must be 'accept' or 'reject'")

    comment = payload.get("comment", "")
    result: dict = {}
    if decision == "accept":
        baseline = approve_baseline(
            db, comparison_id=comparison_id, approved_by=user.id, comment=comment
        )
        result = {"baseline_version": baseline.version}
    else:
        reject_change(db, comparison_id=comparison_id, rejected_by=user.id, comment=comment)

    audit.record(
        db, action=f"visual.{decision}ed", actor_id=user.id, actor_label=user.email,
        project_id=project.id, resource_type="visual_comparison", resource_id=comparison_id,
        detail={"name": comparison.name, "severity": comparison.severity,
                "comment": comment, **result},
    )
    db.commit()
    return {"status": comparison.status, **result}
