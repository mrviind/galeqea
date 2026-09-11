"""Manual checks: human-executed rows that live in the same run as the automated
tests.

A plan's manual and exploratory-charter tests can't be driven by a browser, but
they are still part of the release's evidence. They ride the run as a checklist:
each is a RunTest a person marks pass / fail / blocked / skipped, with a note and
an optional attachment, and the executor is recorded. Until every manual row is
executed it counts as an open blocker for readiness; an unfilled checklist is not
a green release.
"""

from __future__ import annotations

from sqlalchemy import select

from ..models import RunTest, TestCase, TestCategory
from ..models.base import utcnow
from ..models.testing import RunStatus

_MANUAL_CATEGORIES = {TestCategory.MANUAL, TestCategory.EXPLORATORY}
_MARKS = {"passed": RunStatus.PASSED, "failed": RunStatus.FAILED,
          "blocked": RunStatus.BLOCKED, "skipped": RunStatus.SKIPPED}


def seed_manual_rows(db, project_id: str, journey, run) -> int:
    """Add a pending checklist RunTest for each manual/exploratory built test that
    isn't already on the run. Returns how many were added."""
    built = list((journey.test_ids if journey else None) or [])
    if not built:
        return 0
    have = {r.test_key for r in db.execute(
        select(RunTest).where(RunTest.run_id == run.id)).scalars()}
    cases = db.execute(select(TestCase).where(
        TestCase.project_id == project_id, TestCase.key.in_(built))).scalars()
    added = 0
    for tc in cases:
        if tc.category not in _MANUAL_CATEGORIES or tc.key in have:
            continue
        db.add(RunTest(
            run_id=run.id, test_case_id=tc.id, test_key=tc.key, title=tc.title,
            status=RunStatus.NEEDS_REVIEW,
            metrics={"manual": True, "pending": True, "charter": tc.charter or tc.description},
        ))
        added += 1
    if added:
        db.commit()
    return added


def manual_rows(db, run) -> list[dict]:
    """The manual checklist for a run, for the board and the report."""
    rows = []
    for r in db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars():
        m = r.metrics or {}
        if not m.get("manual"):
            continue
        rows.append({
            "id": r.id, "key": r.test_key, "title": r.title,
            "status": r.status, "pending": bool(m.get("pending")),
            "note": m.get("note", ""), "attachment": m.get("attachment", ""),
            "executed_by": m.get("executed_by_name") or m.get("executed_by", ""),
        })
    return rows


def pending_manual(db, run) -> int:
    return sum(1 for r in db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars()
               if (r.metrics or {}).get("manual") and (r.metrics or {}).get("pending"))


def mark(db, run, run_test_id: str, *, status: str, note: str = "",
         attachment: str = "", user=None) -> dict:
    """Record a human's verdict on one manual row."""
    if status not in _MARKS:
        return {"ok": False, "error": f"status must be one of {sorted(_MARKS)}"}
    rt = db.get(RunTest, run_test_id)
    if rt is None or rt.run_id != run.id or not (rt.metrics or {}).get("manual"):
        return {"ok": False, "error": "no such manual row on this run"}
    rt.status = _MARKS[status]
    rt.metrics = {**(rt.metrics or {}), "pending": False, "note": note,
                  "attachment": attachment,
                  "executed_by": getattr(user, "id", None),
                  "executed_by_name": getattr(user, "name", "") or getattr(user, "email", ""),
                  "executed_at": utcnow().isoformat()}
    db.commit()
    return {"ok": True, "id": rt.id, "status": rt.status, "pending_manual": pending_manual(db, run)}
