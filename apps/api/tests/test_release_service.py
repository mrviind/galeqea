"""WO#5 5-B/5-C: release operations, metrics formulas, readiness and sign-off."""

from __future__ import annotations

import pytest

from galeqea.core.approvals import SelfApprovalError
from galeqea.models import (
    Milestone,
    Role,
    Run,
    RunTest,
    TestCase,
    TestCategory,
    TestStatus,
    User,
)
from galeqea.models.base import new_id, utcnow
from galeqea.services import release, release_metrics


def _case(db, project, key, *, category=TestCategory.AUTOMATED, refs=None, risk="medium"):
    tc = TestCase(project_id=project.id, key=key, title=key, status=TestStatus.APPROVED,
                  category=category, risk=risk, requirement_refs=refs or [], version=1)
    db.add(tc); db.flush()
    return tc


def _result(db, run, tc, status):
    rt = RunTest(run_id=run.id, test_case_id=tc.id, test_key=tc.key, status=status,
                 finished_at=utcnow(), duration_ms=1000)
    db.add(rt); db.flush()
    return rt


def test_plan_resolves_selection_and_cycles_pin_versions(db, project):
    a = _case(db, project, "P-1", refs=["REQ-1"])
    _case(db, project, "P-2", refs=["REQ-2"])
    db.commit()
    m = release.create_milestone(db, project, name="R", version="1.4")
    plan, count = release.create_plan(db, project, milestone=m, name="Regression",
                                      selection={"query": {}},
                                      configurations=[{"browser": "chromium"}, {"browser": "firefox"}])
    assert count == 2
    cycles = release.start_cycles(db, plan)
    db.commit()
    assert len(cycles) == 2  # one per configuration
    assert cycles[0].pinned_cases[0]["version"] == a.version
    assert cycles[0].counters["planned"] == 2


def test_metrics_and_readiness(db, project):
    a = _case(db, project, "M-1", refs=["REQ-1"], risk="critical")
    b = _case(db, project, "M-2", refs=["REQ-2"], category=TestCategory.MANUAL)
    # requirements to cover
    from galeqea.models import RequirementDoc, RequirementItem
    doc = RequirementDoc(project_id=project.id, title="d"); db.add(doc); db.flush()
    for ref, risk in [("REQ-1", "critical"), ("REQ-2", "medium")]:
        db.add(RequirementItem(doc_id=doc.id, project_id=project.id, ref=ref, title=ref, risk=risk))
    db.commit()

    m = release.create_milestone(db, project, name="R", version="1.4")
    plan, _ = release.create_plan(db, project, milestone=m, name="Reg", selection={"query": {}},
                                  configurations=[{"browser": "chromium"}])
    cycle = release.start_cycles(db, plan)[0]
    run = Run(project_id=project.id, number=1, status="passed", cycle_id=cycle.id,
              milestone_id=m.id, browsers=["chromium"])
    db.add(run); db.flush()
    _result(db, run, a, "passed")
    _result(db, run, b, "failed")
    db.commit()

    mx = release_metrics.compute(db, m)
    assert mx["counts"]["planned"] == 2 and mx["counts"]["executed"] == 2
    assert mx["pass_rate"] == 0.5              # 1 of 2 passed
    assert mx["open_blockers"] == 1            # the failed result
    assert mx["requirement_coverage"] == 1.0   # both reqs have a case
    assert mx["automation_ratio"] == 0.5       # 1 automated of 2
    assert mx["p1_requirement_coverage"] == 1.0

    release.set_exit_criteria(db, m, [
        {"metric": "pass_rate", "op": ">=", "value": 0.95},
        {"metric": "open_blockers", "op": "==", "value": 0},
    ])
    ev = release.evaluate_readiness(db, m)
    assert ev["verdict"] == "no_go"            # pass_rate 0.5 < 0.95
    assert ev["criteria"][0]["met"] is False and ev["criteria"][1]["met"] is False


def test_sign_off_is_human_only_approver_and_immutable(db, project):
    m = release.create_milestone(db, project, name="R", version="2.0")
    db.commit()
    agent = User(email=f"a-{new_id()[:6]}@x", role=Role.AGENT, is_machine=True, password_hash="")
    author = User(email=f"au-{new_id()[:6]}@x", role=Role.AUTHOR, password_hash="x")
    approver = User(email=f"ap-{new_id()[:6]}@x", role=Role.APPROVER, password_hash="x")
    db.add_all([agent, author, approver]); db.commit()

    with pytest.raises(SelfApprovalError):
        release.sign_off(db, m, decider=agent, decision="go")     # machine
    with pytest.raises(SelfApprovalError):
        release.sign_off(db, m, decider=author, decision="go")    # not approver

    release.sign_off(db, m, decider=approver, decision="go", note="ship it")
    db.commit()
    assert db.get(Milestone, m.id).signoff["by"] == approver.email
    assert db.get(Milestone, m.id).status == "released"

    with pytest.raises(ValueError):  # immutable
        release.sign_off(db, m, decider=approver, decision="no_go")
    # and the audit event is on the ledger
    from sqlalchemy import select

    from galeqea.models import AuditEvent
    assert db.execute(select(AuditEvent).where(
        AuditEvent.action == "milestone.signed_off")).scalars().first() is not None
