"""The Readiness Go/No-Go gate: deterministic criteria, human-only sign-off."""

from __future__ import annotations

import pytest

from galeqea.core.approvals import SelfApprovalError
from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.services import journeys, readiness


def _journey_with_plan(db, project, planned=2, built_keys=("P-GP-A11Y-01", "P-GP-FUNCTIONAL-01")):
    j = journeys.start(db, project.id, "https://x.com")
    j.plan = {"typed": {"totals": {"test_count": planned}}}
    j.test_ids = list(built_keys)
    for key in built_keys:
        db.add(TestCase(project_id=project.id, key=key, title=key,
                        status=TestStatus.APPROVED, category=TestCategory.AUTOMATED))
    db.flush()
    return j


def _run(db, project, results):
    run = Run(project_id=project.id, number=1, title="GP", status="passed",
              environment="production", base_url="https://x.com",
              selection={"keys": [k for k, _ in results]}, totals={})
    db.add(run)
    db.flush()
    for key, status in results:
        rt = RunTest(run_id=run.id, test_case_id="", test_key=key, title=f"t {key}", status=status)
        if status in ("failed", "error"):
            rt.error_message = "boom"
        db.add(rt)
    db.commit()
    return run


def test_go_when_every_criterion_passes(db, project):
    j = _journey_with_plan(db, project)
    run = _run(db, project, [("P-GP-A11Y-01", "passed"), ("P-GP-FUNCTIONAL-01", "passed")])
    result = readiness.evaluate(db, j, run)
    assert result["verdict"] == "go"
    assert all(c["pass"] for c in result["criteria"])
    assert {c["key"] for c in result["criteria"]} >= {
        "blockers", "coverage", "flaky", "perf", "a11y", "security"}


def test_no_go_on_a_critical_accessibility_failure(db, project):
    j = _journey_with_plan(db, project)
    run = _run(db, project, [("P-GP-A11Y-01", "failed"), ("P-GP-FUNCTIONAL-01", "passed")])
    result = readiness.evaluate(db, j, run)
    assert result["verdict"] == "no_go"
    assert "a11y" in result["failed_criteria"]
    a11y = next(c for c in result["criteria"] if c["key"] == "a11y")
    assert a11y["pass"] is False and a11y["evidence"]


def test_an_ai_principal_can_never_sign(db, project, humans):
    j = _journey_with_plan(db, project)
    run = _run(db, project, [("P-GP-A11Y-01", "passed"), ("P-GP-FUNCTIONAL-01", "passed")])
    with pytest.raises(SelfApprovalError):
        readiness.sign_off(db, j, run, decider=humans["agent"])


def test_no_go_sign_off_requires_an_override_note(db, project, humans):
    j = _journey_with_plan(db, project)
    run = _run(db, project, [("P-GP-A11Y-01", "failed"), ("P-GP-FUNCTIONAL-01", "passed")])
    refused = readiness.sign_off(db, j, run, decider=humans["approver"])
    assert refused["ok"] is False and refused["reason"] == "override_required"
    # With a note, the override is recorded.
    ok = readiness.sign_off(db, j, run, decider=humans["approver"], override_note="risk accepted for demo")
    assert ok["ok"] is True and ok["signoff"]["override"] is True
    assert ok["signoff"]["override_note"] == "risk accepted for demo"


def test_human_sign_off_is_recorded_and_audited(db, project, humans):
    from sqlalchemy import select

    from galeqea.models.governance import AuditEvent
    j = _journey_with_plan(db, project)
    run = _run(db, project, [("P-GP-A11Y-01", "passed"), ("P-GP-FUNCTIONAL-01", "passed")])
    result = readiness.sign_off(db, j, run, decider=humans["approver"])
    assert result["ok"] is True
    assert run.ci_metadata["readiness_signoff"]["signer_name"] == "Approver"
    events = list(db.execute(select(AuditEvent).where(
        AuditEvent.resource_id == run.id, AuditEvent.action == "readiness.signed_off")).scalars())
    assert len(events) == 1 and events[0].actor_kind == "human"
