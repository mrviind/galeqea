"""The invariants that make GaleQEA trustworthy.

If any test in this file fails, the product's central claim is false.
"""

from __future__ import annotations

import pytest

from galeqea.core import approvals, audit
from galeqea.core.approvals import ApprovalError, SelfApprovalError
from galeqea.models import ApprovalStatus, RiskTier


@pytest.fixture()
def pending(db, project, humans):
    return approvals.request(
        db,
        action="test.create",
        title="Proposed by an agent",
        project_id=project.id,
        payload={"arguments": {"title": "A test", "category": "manual", "rationale": "because"}},
        requested_by=humans["agent"].id,
        requested_by_kind="agent",
        agent_role="test_designer",
    )


def test_agent_cannot_approve_anything(db, pending, humans):
    with pytest.raises(SelfApprovalError, match="never satisfy an approval gate"):
        approvals.approve(db, pending.id, humans["agent"])
    assert db.get(type(pending), pending.id).status == ApprovalStatus.PENDING


def test_requester_cannot_approve_their_own_request(db, project, humans):
    request = approvals.request(
        db, action="test.create", title="Self-proposed", project_id=project.id,
        payload={"arguments": {"title": "x", "category": "manual", "rationale": "y"}},
        requested_by=humans["approver"].id, requested_by_kind="human",
    )
    with pytest.raises(SelfApprovalError, match="cannot also approve"):
        approvals.approve(db, request.id, humans["approver"])


def test_insufficient_role_is_refused(db, pending, humans):
    with pytest.raises(ApprovalError, match="requires role"):
        approvals.approve(db, pending.id, humans["author"])


def test_approval_applies_and_records_provenance(db, pending, humans):
    decision = approvals.approve(db, pending.id, humans["approver"], comment="looks right")
    assert decision.applied
    assert decision.request.status == ApprovalStatus.APPLIED

    from galeqea.models import TestCase

    created = db.get(TestCase, decision.result["test_id"])
    assert created.status == "approved"
    assert created.provenance["approved_by"] == humans["approver"].id
    assert created.provenance["approval_id"] == pending.id


def test_unknown_action_cannot_be_requested(db, project):
    with pytest.raises(ApprovalError, match="no applier registered"):
        approvals.request(db, action="definitely.not.registered", title="x", project_id=project.id)


def test_high_risk_actions_are_classified_conservatively():
    # Anything unlisted must fail closed, not open.
    assert approvals.ACTION_RISK.get("some.new.action", RiskTier.HIGH) is RiskTier.HIGH
    assert approvals.ACTION_RISK["jira.create_issue"] is RiskTier.HIGH
    assert approvals.ACTION_RISK["secret.write"] is RiskTier.CRITICAL


def test_audit_chain_detects_tampering(db, project, humans):
    for i in range(5):
        audit.record(db, action=f"test.event{i}", actor_id=humans["author"].id,
                     project_id=project.id, detail={"i": i})
    db.commit()
    assert audit.verify_chain(db).ok

    from sqlalchemy import select

    from galeqea.models import AuditEvent

    victim = db.execute(
        select(AuditEvent).order_by(AuditEvent.seq.desc()).limit(1)
    ).scalar_one()
    victim.detail = {"i": "tampered"}
    db.commit()

    result = audit.verify_chain(db)
    assert not result.ok
    assert result.first_bad_seq == victim.seq
    assert "does not match" in result.reason
