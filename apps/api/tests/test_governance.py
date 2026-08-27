"""The invariants that make QE Agent trustworthy.

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


# --------------------------------------------------------------------------- #
# The git pull-request flow is wired to the gate, exactly like every other
# state-changing action. Before it was wired, requesting "git.open_pr" raised
# "no applier registered" - the tool existed but pointed at nothing.
# --------------------------------------------------------------------------- #
def _approved_test(db, project):
    from galeqea.models import StepAction, TestCase, TestStatus, TestStep

    case = TestCase(project_id=project.id, key="GATE-PR-1", title="Checkout charges the card",
                    status=TestStatus.APPROVED, category="automated")
    db.add(case)
    db.flush()
    db.add(TestStep(test_case_id=case.id, index=0, action=StepAction.GOTO,
                    intent="open checkout", value={"url": "/checkout"}))
    db.add(TestStep(test_case_id=case.id, index=1, action=StepAction.EXPECT_VISIBLE,
                    intent="see receipt", target={"ladder": [{"kind": "text", "value": "Receipt"}]}))
    db.commit()
    return case


def test_pull_request_action_is_wired_to_an_applier():
    # The whole point of the flow: a human approval can actually be applied.
    assert "git.open_pr" in approvals.registered_actions()
    assert approvals.ACTION_RISK["git.open_pr"] is RiskTier.HIGH


def test_opening_a_pr_always_routes_through_the_gate(db, project, humans):
    import asyncio

    from galeqea.ai.tools import ToolContext, registry

    _approved_test(db, project)
    ctx = ToolContext(db=db, project_id=project.id, user=humans["agent"],
                      actor_kind="agent", agent_role="test_designer")
    result = asyncio.run(registry.invoke("open_test_pull_request", {}, ctx))

    # An agent never opens a PR directly - it only files a request.
    from galeqea.models import ApprovalRequest

    assert result["status"] == "awaiting_approval"
    assert result["approval_id"]
    req = db.get(ApprovalRequest, result["approval_id"])
    assert req.action == "git.open_pr"
    assert req.status == "pending"


def test_applier_fails_safe_when_no_provider_is_connected(db, project, humans):
    # Approving the request must never crash or push blindly: with no git
    # provider configured it declines cleanly and pushes nothing.
    _approved_test(db, project)
    req = approvals.request(
        db, action="git.open_pr", title="Add tests",
        project_id=project.id,
        payload={"tool": "open_test_pull_request", "arguments": {}},
        requested_by=humans["agent"].id, requested_by_kind="agent",
    )
    decision = approvals.approve(db, req.id, humans["approver"])
    assert decision.applied  # the applier ran to completion...
    assert decision.result["ok"] is False  # ...and safely declined
    assert "git provider" in decision.result["error"].lower()
