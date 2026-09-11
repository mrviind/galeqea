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
    """Requirement → rules → tests → last result → defects. The full join an
    auditor asks for; the rules layer makes a coverage gap precise: not "this
    requirement is untested" but "this specific rule within it has no case" (WO#9-B).
    """
    from datetime import UTC, datetime

    from ..models import DefectLink, RequirementDoc, RequirementRule, TestStat

    # De-duplicate by requirement ref, keeping the item from the LATEST document
    # version. Older uploads of the same spec are superseded and must never
    # multi-count coverage or gaps in the matrix (WO#9-C).
    doc_time, archived_docs = {}, set()
    for d in db.execute(
        select(RequirementDoc).where(RequirementDoc.project_id == project_id)
    ).scalars():
        doc_time[d.id] = d.created_at
        if (d.meta or {}).get("archived"):
            archived_docs.add(d.id)
    _floor = datetime(1970, 1, 1, tzinfo=UTC)
    latest_by_ref: dict[str, RequirementItem] = {}
    for item in db.execute(
        select(RequirementItem).where(RequirementItem.project_id == project_id)
    ).scalars():
        if item.doc_id in archived_docs:      # superseded/hidden, so skip (WO#9-C)
            continue
        key = item.ref.upper()
        current = latest_by_ref.get(key)
        if current is None or (doc_time.get(item.doc_id) or _floor) > (
            doc_time.get(current.doc_id) or _floor
        ):
            latest_by_ref[key] = item
    requirements = sorted(latest_by_ref.values(), key=lambda i: i.ref)
    cases = list(
        db.execute(select(TestCase).where(TestCase.project_id == project_id)).scalars()
    )
    stats = {
        s.test_case_id: s
        for s in db.execute(select(TestStat).where(TestStat.project_id == project_id)).scalars()
    }
    # Same latest-version-only rule as `requirements` above: a rule belongs to
    # exactly one item, so keep it only if its parent item is the one that
    # survived the dedup. Otherwise two unrelated docs that happen to reuse the
    # same ref (e.g. two different uploads both using "DEMO-001") double the
    # rule rows under one requirement section in the matrix.
    kept_item_ids = {item.id for item in latest_by_ref.values()}
    rules = list(
        db.execute(select(RequirementRule).where(RequirementRule.project_id == project_id)
                   .order_by(RequirementRule.rule_id)).scalars()
    )
    rules_by_ref: dict[str, list] = {}
    for r in rules:
        if r.item_id is not None and r.item_id not in kept_item_ids:
            continue
        rules_by_ref.setdefault(r.requirement_ref.upper(), []).append(r)
    defects_by_case: dict[str, list] = {}
    for d in db.execute(select(DefectLink).where(DefectLink.project_id == project_id)).scalars():
        if d.test_case_id:
            defects_by_case.setdefault(d.test_case_id, []).append(d)

    def _defects(case) -> list[dict]:
        return [{"key": d.key, "tracker": d.tracker, "url": d.url,
                 "status": d.status_cached} for d in defects_by_case.get(case.id, [])]

    rows: list[dict] = []
    for req in requirements:
        linked = [c for c in cases if req.ref.upper() in {r.upper() for r in (c.requirement_refs or [])}]
        req_rules = rules_by_ref.get(req.ref.upper(), [])
        rule_rows = []
        for rule in req_rules:
            covering = [c for c in cases if rule.rule_id in (c.covers or [])]
            rule_rows.append({
                "rule_id": rule.rule_id,
                "rule_type": rule.rule_type,
                "technique": rule.technique,
                "text": rule.text,
                "tests": [c.key for c in covering],
                "covered": any(c.status == TestStatus.APPROVED for c in covering),
                "open_questions": rule.open_questions or [],
            })
        rows.append({
            "ref": req.ref,
            "title": req.title,
            "risk": req.risk,
            "priority": _risk_priority(req, cases),
            "acceptance_criteria": req.acceptance_criteria or [],
            "open_questions": req.open_questions or [],
            "source_anchor": req.source_anchor or {},
            "rules": rule_rows,
            "rules_total": len(rule_rows),
            "rules_covered": sum(1 for r in rule_rows if r["covered"]),
            "tests": [
                {
                    "key": c.key, "title": c.title, "category": c.category,
                    "status": c.status, "priority": c.priority,
                    "last_status": (stats.get(c.id).last_status if stats.get(c.id) else "never run"),
                    "flake_score": round(c.flake_score, 2),
                    "approved_by": c.approved_by,
                    "covers": c.covers or [],
                    "technique": (c.provenance or {}).get("technique", ""),
                    "defects": _defects(c),
                    "provenance": c.provenance,
                }
                for c in linked
            ],
            "covered": any(c.status == TestStatus.APPROVED for c in linked),
        })
    return rows


def _risk_priority(req, cases: list) -> str:
    """P1-P4 = business impact × change proximity × failure history (WO#9-B).

    Impact from the requirement's own risk; proximity from whether any of its cases
    is in flight (proposed/draft, i.e. actively being worked); history from whether a
    covering case is currently failing. Deliberately coarse and explainable.
    """
    impact = {"critical": 3, "high": 2, "medium": 1, "low": 0}.get(req.risk, 1)
    linked = [c for c in cases if req.ref.upper() in {r.upper() for r in (c.requirement_refs or [])}]
    proximity = 1 if any(c.status in ("proposed", "draft") for c in linked) else 0
    history = 1 if any((c.flake_score or 0) > 0.2 for c in linked) else 0
    score = impact + proximity + history
    if score >= 4:
        return "P1"
    if score == 3:
        return "P2"
    if score == 2:
        return "P3"
    return "P4"
