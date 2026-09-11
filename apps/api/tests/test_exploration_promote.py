"""Exploratory: an anomaly becomes a proposed, guarded test."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import StepAction, TestCase, TestStatus
from galeqea.models.intel import ExplorationFinding, ExplorationSession
from galeqea.services import exploration


def _finding(db, project, kind="accessibility", url="https://x.com/checkout", sev="high"):
    s = ExplorationSession(project_id=project.id, charter="poke /checkout",
                           base_url="https://x.com/checkout", status="completed",
                           summary="Explored 1 screen; 1 new finding.")
    db.add(s)
    db.flush()
    f = ExplorationFinding(project_id=project.id, session_id=s.id, kind=kind,
                           severity=sev, title=f"{kind} on checkout", detail="details here",
                           url=url, signature=f"sig-{kind}")
    db.add(f)
    db.commit()
    return s, f


def test_promote_finding_files_a_guarded_test(db, project):
    _s, f = _finding(db, project, kind="accessibility")
    result = exploration.promote_finding(db, project_id=project.id, finding_id=f.id, actor="u1")
    assert result["ok"] and result["kind"] == "accessibility"
    tc = db.execute(select(TestCase).where(TestCase.key == result["key"])).scalar_one()
    assert tc.status == TestStatus.PROPOSED
    assert tc.provenance["origin"] == "exploratory" and tc.provenance["finding_id"] == f.id
    assert "exploratory" in tc.tags and "accessibility" in tc.tags
    # An a11y finding gets a real axe assertion, not just a note.
    assert any(s.action == StepAction.ASSERT_A11Y for s in tc.steps)
    # The finding is marked promoted and linked to the test.
    db.refresh(f)
    assert f.status == "promoted" and f.promoted_test_id == tc.id


def test_promote_is_idempotent(db, project):
    _s, f = _finding(db, project, kind="dead_end")
    first = exploration.promote_finding(db, project_id=project.id, finding_id=f.id)
    second = exploration.promote_finding(db, project_id=project.id, finding_id=f.id)
    assert second["already"] is True and second["test_id"] == first["test_id"]


def test_dead_end_finding_becomes_a_goto_check(db, project):
    _s, f = _finding(db, project, kind="dead_end", url="https://x.com/gone")
    result = exploration.promote_finding(db, project_id=project.id, finding_id=f.id)
    tc = db.execute(select(TestCase).where(TestCase.key == result["key"])).scalar_one()
    gotos = [s for s in tc.steps if s.action == StepAction.GOTO]
    assert gotos and gotos[0].value["url"] == "https://x.com/gone"


def test_exploratory_section_in_report(db, project):
    from galeqea.models import Run
    from galeqea.reports import report_v2
    from galeqea.services import journeys
    j = journeys.start(db, project.id, "https://x.com")
    _finding(db, project, kind="slow_response", url="https://x.com/checkout")
    # Point the session at the journey's base so sessions_for_journey finds it.
    s = db.execute(select(ExplorationSession)).scalars().first()
    s.base_url = "https://x.com/checkout"
    run = Run(project_id=project.id, number=1, trigger="golden_path", base_url="https://x.com",
              environment="production", totals={})
    db.add(run)
    db.commit()
    rep = report_v2.build(db, project, run, j)
    assert rep["exploratory"], "the report surfaces exploratory sessions"
    md = report_v2.to_markdown(rep)
    assert "## Exploratory" in md and "slow_response" in md
