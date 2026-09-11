"""Release metrics: the numbers a test manager reads a milestone by.

Every figure is computed deterministically from the milestone's cycles, their runs
and results, and the project's requirements. Formulas are the ones in the work order;
each is unit-tested. All ratios are 0..1 (render as % in cards) and degrade to 0 when
the denominator is empty rather than dividing by zero.
"""

from __future__ import annotations

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..models import (
    Cycle,
    DefectLink,
    Milestone,
    RequirementItem,
    Run,
    RunTest,
    TestCase,
    TestCategory,
)

_PASS = {"passed", "flaky"}
_FAIL = {"failed", "error"}
_EXECUTED = _PASS | _FAIL | {"blocked", "skipped", "needs_review"}


def _ratio(n: int, d: int) -> float:
    return round(n / d, 4) if d else 0.0


def _latest_by_case_cycle(runs: list[Run], results: list[RunTest]) -> dict:
    """The most recent result for each (cycle, case) slot."""
    cycle_of = {r.id: r.cycle_id for r in runs}
    latest: dict[tuple, RunTest] = {}
    for rt in results:
        key = (cycle_of.get(rt.run_id), rt.test_case_id)
        cur = latest.get(key)
        if cur is None or (rt.finished_at or rt.created_at) >= (cur.finished_at or cur.created_at):
            latest[key] = rt
    return latest


def compute(db: Session, milestone: Milestone) -> dict:
    cycles = db.execute(
        select(Cycle).where(Cycle.milestone_id == milestone.id)).scalars().all()
    cycle_ids = [c.id for c in cycles]
    runs = db.execute(
        select(Run).where(or_(Run.milestone_id == milestone.id,
                              Run.cycle_id.in_(cycle_ids) if cycle_ids else False))
    ).scalars().all()
    run_ids = [r.id for r in runs]
    results = list(db.execute(
        select(RunTest).where(RunTest.run_id.in_(run_ids))).scalars()) if run_ids else []

    # --- execution & pass rate (over cycle×case slots) ---------------------
    planned = sum(len(c.pinned_cases or []) for c in cycles)
    latest = _latest_by_case_cycle(runs, results)
    executed = sum(1 for rt in latest.values() if rt.status in _EXECUTED)
    passed = sum(1 for rt in latest.values() if rt.status in _PASS)
    failed_results = [rt for rt in latest.values() if rt.status in _FAIL]
    failed = len(failed_results)
    # Open blockers = failing results not already covered by a fixed (resolved) defect.
    from . import defects as _defects
    _resolved = _defects.resolved_result_ids(
        db, milestone.project_id, [rt.id for rt in failed_results])
    open_blockers = sum(1 for rt in failed_results if rt.id not in _resolved)

    # --- requirement coverage ----------------------------------------------
    reqs = db.execute(
        select(RequirementItem).where(RequirementItem.project_id == milestone.project_id)
    ).scalars().all()
    cases = db.execute(
        select(TestCase).where(TestCase.project_id == milestone.project_id)).scalars().all()
    refs_with_a_case: set[str] = set()
    for tc in cases:
        for ref in (tc.requirement_refs or []):
            refs_with_a_case.add(ref)
    covered = sum(1 for r in reqs if r.ref in refs_with_a_case)
    # tested = a covered requirement that has at least one executed result this milestone
    executed_case_ids = {rt.test_case_id for rt in latest.values() if rt.status in _EXECUTED}
    tested_refs = {ref for tc in cases if tc.id in executed_case_ids
                   for ref in (tc.requirement_refs or [])}
    tested = sum(1 for r in reqs if r.ref in tested_refs)
    # P1 = the high/critical-risk requirements; their coverage is often a gate.
    p1 = [r for r in reqs if r.risk in ("high", "critical")]
    p1_covered = sum(1 for r in p1 if r.ref in refs_with_a_case)

    # --- automation, defects, flakiness ------------------------------------
    milestone_case_ids = {pc.get("test_case_id") for c in cycles for pc in (c.pinned_cases or [])}
    scoped_cases = [tc for tc in cases if tc.id in milestone_case_ids] or cases
    automated = sum(1 for tc in scoped_cases if tc.category == TestCategory.AUTOMATED)
    defects = db.execute(
        select(DefectLink).where(DefectLink.result_id.in_([rt.id for rt in results]))
    ).scalars().all() if results else []
    flaky_execs = sum(1 for rt in latest.values() if rt.status == "flaky")

    # --- MTTR (mean time to repair): first fail → next pass, per case -------
    mttr_ms = _mttr(results)

    # --- effort variance (duration proxy: actual vs the cases' averages) ----
    estimated_ms = sum((tc.avg_duration_ms or 0) for tc in scoped_cases) * max(len(cycles), 1)
    actual_ms = sum((rt.duration_ms or 0) for rt in results)
    effort_variance = _ratio(actual_ms - estimated_ms, estimated_ms) if estimated_ms else 0.0

    return {
        "milestone": {"id": milestone.id, "name": milestone.name, "version": milestone.version,
                      "status": milestone.status},
        "counts": {"planned": planned, "executed": executed, "passed": passed,
                   "failed": failed, "cycles": len(cycles), "requirements": len(reqs),
                   "cases": len(scoped_cases), "defects": len(defects)},
        "execution_progress": _ratio(executed, planned),
        "pass_rate": _ratio(passed, executed),
        "open_blockers": open_blockers,
        "requirement_coverage": _ratio(covered, len(reqs)),
        "tested_coverage": _ratio(tested, len(reqs)),
        "p1_requirement_coverage": _ratio(p1_covered, len(p1)) if p1 else 1.0,
        "automation_ratio": _ratio(automated, len(scoped_cases)),
        "defect_density": round(len(defects) / (len(scoped_cases) / 100), 4) if scoped_cases else 0.0,
        "flaky_rate": _ratio(flaky_execs, executed),
        "mttr_ms": mttr_ms,
        "effort_variance": effort_variance,
    }


def _mttr(results: list[RunTest]) -> int:
    """Mean time from a case's first failure to its next pass, in ms."""
    by_case: dict[str, list[RunTest]] = {}
    for rt in results:
        by_case.setdefault(rt.test_case_id, []).append(rt)
    spans = []
    for rts in by_case.values():
        rts.sort(key=lambda r: r.finished_at or r.created_at)
        first_fail = None
        for rt in rts:
            ts = rt.finished_at or rt.created_at
            if rt.status in _FAIL and first_fail is None:
                first_fail = ts
            elif rt.status in _PASS and first_fail is not None:
                spans.append((ts - first_fail).total_seconds() * 1000)
                first_fail = None
    return int(sum(spans) / len(spans)) if spans else 0


def is_unstable(db: Session, test_case_id: str, *, window: int = 10) -> bool:
    """A case with ≥3 status transitions across its last N results is unstable."""
    rows = list(db.execute(
        select(RunTest.status).where(RunTest.test_case_id == test_case_id)
        .order_by(RunTest.created_at.desc()).limit(window)
    ).scalars())
    transitions = sum(1 for a, b in zip(rows, rows[1:], strict=False) if a != b)
    return transitions >= 3
