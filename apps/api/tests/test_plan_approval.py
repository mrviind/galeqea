"""First-class Golden Path plan approval: proposing files an ApprovalRequest, and
approving it (one decide service across chat/HTTP/CLI/MCP) builds runnable tests and
records the human principal on the hash-chained ledger. A machine can never approve."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from galeqea.config import settings
from galeqea.core import approvals
from galeqea.core.approvals import SelfApprovalError
from galeqea.main import app
from galeqea.models import (
    ApprovalStatus,
    AuditEvent,
    Role,
    TestStatus,
    User,
)
from galeqea.models.base import new_id
from galeqea.services import plan_approval


@pytest.fixture()
def crawl(monkeypatch):
    """A deterministic crawl; no runner/browser needed for the plan flow."""
    def fake_discover(url, limit=8, timeout=90.0):
        from galeqea.services.onramp import normalize_url
        base = normalize_url(url)
        return {"ok": True, "base": base, "pages": [base, base + "/pricing"],
                "forms": 1, "status": 200, "method": "http"}
    monkeypatch.setattr("galeqea.services.website_test.discover_pages", fake_discover)


def _human(db, role=Role.AUTHOR):
    u = User(email=f"h-{new_id()[:8]}@corp.example", name="H", role=role,
             is_active=True, password_hash="x")
    db.add(u); db.commit()
    return u


def test_propose_files_a_pending_plan_approval(crawl, db, project):
    journey, req = plan_approval.propose(db, project, "https://shop.example.com")
    assert req.action == "plan.approve"
    assert req.status == ApprovalStatus.PENDING
    assert req.required_role == Role.AUTHOR  # risk LOW
    assert req.requested_by is None and req.requested_by_kind == "agent"
    assert plan_approval.pending_for_journey(db, journey).id == req.id


def test_human_approval_builds_runnable_tests_and_audits_the_person(crawl, db, project):
    from galeqea.cli import _ci_plan_keys

    journey, req = plan_approval.propose(db, project, "https://shop.example.com")
    human = _human(db)

    outcome = approvals.approve(db, req.id, human)
    db.commit()
    assert outcome.applied and outcome.result["keys"]

    # The built golden-path tests are APPROVED and listed on the journey, so the CI
    # command will run them.
    _j, keys = _ci_plan_keys(db, project, "https://shop.example.com")
    assert keys, "approving the plan makes tests runnable"
    from sqlalchemy import select

    from galeqea.models import TestCase
    for tc in db.execute(select(TestCase).where(TestCase.key.in_(keys))).scalars():
        assert tc.status == TestStatus.APPROVED

    # The approval is on the hash-chained ledger with the HUMAN as principal.
    from sqlalchemy import select
    ev = db.execute(
        select(AuditEvent).where(AuditEvent.action == "approval.approved",
                                 AuditEvent.approval_id == req.id)
    ).scalar_one()
    assert ev.actor_kind == "human" and ev.actor_id == human.id


def test_a_machine_can_never_approve_a_plan(crawl, db, project):
    journey, req = plan_approval.propose(db, project, "https://shop.example.com")
    agent = User(email=f"a-{new_id()[:8]}@galeqea.local", name="Agent",
                 role=Role.AGENT, is_machine=True, password_hash="")
    db.add(agent); db.commit()
    with pytest.raises(SelfApprovalError):
        approvals.approve(db, req.id, agent)


def test_http_propose_then_approve(crawl, db, project, monkeypatch):
    monkeypatch.setattr(settings, "single_user_mode", True)  # implicit local owner (human)
    c = TestClient(app)

    r = c.post(f"/api/projects/{project.id}/journeys", json={"target": "https://shop.example.com"})
    assert r.status_code == 201, r.text
    jid = r.json()["journey"]["id"]
    assert r.json()["approval_id"]

    a = c.post(f"/api/projects/{project.id}/journeys/{jid}/plan/approve", json={})
    assert a.status_code == 200 and a.json()["applied"] is True
    assert a.json()["result"]["keys"]
