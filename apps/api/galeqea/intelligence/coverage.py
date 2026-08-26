"""Requirement and journey coverage, including honest gap reporting.

Coverage that only counts what exists is marketing. The valuable half is the
list of requirements nothing tests, and the requirements whose only test is weak
- covered by a manual note, or by a single low-priority happy path.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    AppScreen,
    AppTransition,
    CoverageSnapshot,
    RequirementItem,
    TestCase,
    TestCategory,
    TestStatus,
)

RISK_ORDER = ["critical", "high", "medium", "low"]


def compute(db: Session, project_id: str, *, persist: bool = True) -> dict:
    requirements = list(
        db.execute(
            select(RequirementItem).where(RequirementItem.project_id == project_id)
        ).scalars()
    )
    cases = list(
        db.execute(
            select(TestCase).where(
                TestCase.project_id == project_id,
                TestCase.status.in_([TestStatus.APPROVED, TestStatus.PROPOSED]),
            )
        ).scalars()
    )

    by_ref: dict[str, list[TestCase]] = {}
    for case in cases:
        for ref in case.requirement_refs or []:
            by_ref.setdefault(ref.upper(), []).append(case)

    covered: list[dict] = []
    uncovered: list[dict] = []
    weak: list[dict] = []
    by_risk: dict[str, dict] = {r: {"total": 0, "covered": 0, "automated": 0} for r in RISK_ORDER}
    automated_refs = 0

    for req in requirements:
        risk = req.risk if req.risk in by_risk else "medium"
        by_risk[risk]["total"] += 1

        linked = by_ref.get(req.ref.upper(), [])
        approved = [c for c in linked if c.status == TestStatus.APPROVED]
        automated = [c for c in approved if c.category == TestCategory.AUTOMATED]

        entry = {
            "ref": req.ref,
            "title": req.title,
            "risk": req.risk,
            "tests": [{"key": c.key, "title": c.title, "category": c.category,
                       "status": c.status, "priority": c.priority} for c in linked],
            "automated_count": len(automated),
        }

        if not approved:
            entry["gap_reason"] = (
                "no approved test references this requirement"
                if not linked else
                f"{len(linked)} proposed test(s) exist but none is approved yet"
            )
            uncovered.append(entry)
            continue

        by_risk[risk]["covered"] += 1
        covered.append(entry)
        if automated:
            automated_refs += 1
            by_risk[risk]["automated"] += 1

        # Weakness heuristics - a covered requirement that should not be trusted.
        reasons: list[str] = []
        if req.risk in {"critical", "high"} and not automated:
            reasons.append(f"{req.risk}-risk but only covered manually")
        if len(approved) == 1 and (req.acceptance_criteria or []) and len(req.acceptance_criteria) > 2:
            reasons.append(
                f"{len(req.acceptance_criteria)} acceptance criteria covered by a single test"
            )
        if approved and all(c.priority in {"low"} for c in approved):
            reasons.append("only low-priority tests reference it")
        if any(c.quarantined for c in automated):
            reasons.append("its automated test is quarantined")
        if reasons:
            weak.append({**entry, "weakness": reasons})

    total = len(requirements)
    journeys = _journey_coverage(db, project_id)

    result = {
        "total_requirements": total,
        "covered_requirements": len(covered),
        "automated_requirements": automated_refs,
        "coverage_pct": round(len(covered) / total * 100, 1) if total else 0.0,
        "automation_pct": round(automated_refs / total * 100, 1) if total else 0.0,
        "uncovered": uncovered,
        "weak": weak,
        "by_risk": by_risk,
        "journeys": journeys,
        "headline": _headline(total, len(covered), uncovered, weak, by_risk),
    }

    if persist:
        db.add(CoverageSnapshot(
            project_id=project_id,
            total_requirements=total,
            covered_requirements=len(covered),
            automated_requirements=automated_refs,
            uncovered_refs=[u["ref"] for u in uncovered],
            weak_refs=[w["ref"] for w in weak],
            by_risk=by_risk,
            journey_coverage=journeys,
        ))
        db.flush()
    return result


def _journey_coverage(db: Session, project_id: str) -> dict:
    """How much of the discovered app graph any test actually walks."""
    screens = list(
        db.execute(select(AppScreen).where(AppScreen.project_id == project_id)).scalars()
    )
    transitions = list(
        db.execute(select(AppTransition).where(AppTransition.project_id == project_id)).scalars()
    )
    if not screens:
        return {"screens": 0, "transitions": 0, "note": "no App Model has been discovered yet"}

    visited = [s for s in screens if s.visit_count > 0]
    return {
        "screens": len(screens),
        "screens_visited": len(visited),
        "transitions": len(transitions),
        "unvisited_screens": [s.name for s in screens if s.visit_count == 0][:20],
        "coverage_pct": round(len(visited) / len(screens) * 100, 1),
    }


def _headline(total: int, covered: int, uncovered: list, weak: list, by_risk: dict) -> str:
    if total == 0:
        return "No requirements have been ingested yet."
    critical_gaps = [
        u for u in uncovered if u["risk"] in {"critical", "high"}
    ]
    parts = [f"{covered}/{total} requirements covered ({covered / total:.0%})"]
    if critical_gaps:
        parts.append(
            f"{len(critical_gaps)} high or critical-risk requirement(s) have no approved test: "
            + ", ".join(g["ref"] for g in critical_gaps[:4])
        )
    elif uncovered:
        parts.append(f"{len(uncovered)} lower-risk requirement(s) uncovered")
    if weak:
        parts.append(f"{len(weak)} covered requirement(s) are only weakly tested")
    return ". ".join(parts) + "."


def traceability_matrix(db: Session, project_id: str) -> list[dict]:
    """Requirement → test → last result. The artefact auditors ask for."""
    from ..models import TestStat

    requirements = list(
        db.execute(
            select(RequirementItem).where(RequirementItem.project_id == project_id)
            .order_by(RequirementItem.ref)
        ).scalars()
    )
    cases = list(
        db.execute(select(TestCase).where(TestCase.project_id == project_id)).scalars()
    )
    stats = {
        s.test_case_id: s
        for s in db.execute(select(TestStat).where(TestStat.project_id == project_id)).scalars()
    }

    rows: list[dict] = []
    for req in requirements:
        linked = [c for c in cases if req.ref.upper() in {r.upper() for r in (c.requirement_refs or [])}]
        rows.append({
            "ref": req.ref,
            "title": req.title,
            "risk": req.risk,
            "acceptance_criteria": req.acceptance_criteria or [],
            "open_questions": req.open_questions or [],
            "tests": [
                {
                    "key": c.key, "title": c.title, "category": c.category,
                    "status": c.status, "priority": c.priority,
                    "last_status": (stats.get(c.id).last_status if stats.get(c.id) else "never run"),
                    "flake_score": round(c.flake_score, 2),
                    "approved_by": c.approved_by,
                    "provenance": c.provenance,
                }
                for c in linked
            ],
            "covered": any(c.status == TestStatus.APPROVED for c in linked),
        })
    return rows
