"""The Golden Path journey: state, the rail, resume, and chat navigation.

Slice 1's contract: a journey tracks where a target is on the rail; a reload
resumes it; and "what's next" resolves against the active journey. All
deterministic; no model touches journey state.
"""

from __future__ import annotations

import uuid

from galeqea.ai.orchestrator import Orchestrator
from galeqea.models import ChatSession, JourneyStage, Role, User
from galeqea.services import journeys


def test_journey_lifecycle_and_rail(db, project):
    j = journeys.start(db, project.id, "https://the-internet.herokuapp.com")
    assert j.stage == JourneyStage.TARGET
    assert j.environment == "production"

    journeys.advance(db, j, JourneyStage.PLAN, plan_version=1)
    info = journeys.describe(j)
    assert info["stage"] == "plan"
    assert info["next"]["command"] == "approve"

    rail = {r["stage"]: r for r in info["rail"]}
    assert rail["plan"]["current"] is True
    assert rail["target"]["done"] is True
    assert rail["run"]["done"] is False


def test_start_is_idempotent_per_target(db, project):
    a = journeys.start(db, project.id, "https://x.com")
    b = journeys.start(db, project.id, "https://x.com/")  # normalises to the same target
    assert a.id == b.id


def test_active_journey_is_the_most_recent(db, project):
    journeys.start(db, project.id, "https://a.com")
    latest = journeys.start(db, project.id, "https://b.com")
    journeys.advance(db, latest, JourneyStage.EXPLORE)
    assert journeys.active_journey(db, project.id).id == latest.id


def test_journey_endpoint_resumes(db, project):
    journeys.start(db, project.id, "https://x.com")
    db.commit()
    from fastapi.testclient import TestClient

    from galeqea.main import app

    client = TestClient(app)
    r = client.get(f"/api/projects/{project.id}/journey")
    assert r.status_code == 200
    body = r.json()
    assert body["active"] is True and body["target"] == "https://x.com" and body["rail"]


def test_no_journey_reports_inactive(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app

    r = TestClient(app).get(f"/api/projects/{project.id}/journey")
    assert r.status_code == 200 and r.json() == {"active": False}


def test_default_guardrails_are_safe_for_production(db, project):
    prod = journeys.default_guardrails("https://the-internet.herokuapp.com", "production")
    assert prod["read_only"] is True and prod["data_policy"] == "synthetic"
    assert "payments" in prod["avoid"]
    local = journeys.default_guardrails("http://localhost:8765", "local")
    assert local["read_only"] is False


def test_advance_marks_ran_vs_skipped(db, project):
    j = journeys.start(db, project.id, "https://x.com")  # stage=target
    journeys.advance(db, j, JourneyStage.GUARDRAILS)      # skips access (ran=False path)
    journeys.advance(db, j, JourneyStage.PLAN)
    rail = {r["stage"]: r for r in journeys.describe(j)["rail"]}
    assert rail["target"]["done"] is True          # actually ran
    assert rail["guardrails"]["done"] is True
    assert rail["access"]["skipped"] is True        # passed over, not "done"
    assert rail["explore"]["skipped"] is True


async def test_first_visit_stops_at_guardrails_then_continues(db, project, monkeypatch):
    from galeqea.services import website_test as wt

    def fake_discover(url, limit=8, timeout=90.0):
        base = url.rstrip("/")
        return {"ok": True, "base": base, "pages": [base, base + "/a"], "forms": 0,
                "status": 200, "method": "browser", "findings": [],
                "skipped": {"auth": 0, "error": 0, "offsite": 0}, "truncated": False, "discovered": 2}

    async def fake_run(db2, *, project_id, plan, triggered_by=None, timeout=240.0):
        return {"ok": True, "status": "passed", "target": plan["target"], "passed": 2,
                "failed": 0, "pages": [], "run_id": "r1", "run_number": 1, "summary": "all passed."}

    monkeypatch.setattr(wt, "discover_pages", fake_discover)
    monkeypatch.setattr(wt, "run_website_test", fake_run)

    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x.io", name="U", role=Role.OWNER.value)
    session = ChatSession(project_id=project.id, title="t")
    db.add_all([user, session])
    db.commit()
    orch = Orchestrator(db, provider=None, project_id=project.id)

    r1 = await orch.handle(session=session, user=user, text="test https://example.com")
    assert r1.blocks[0]["type"] == "guardrails_card"
    assert journeys.active_journey(db, project.id).stage == "guardrails"

    r2 = await orch.handle(session=session, user=user, text="continue")
    assert r2.blocks[0]["type"] == "website_plan"
    assert journeys.active_journey(db, project.id).stage == "plan"


async def test_whats_next_resolves_against_the_active_journey(db, project):
    j = journeys.start(db, project.id, "https://x.com")
    journeys.advance(db, j, JourneyStage.PLAN)
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x.io", name="U", role=Role.OWNER.value)
    session = ChatSession(project_id=project.id, title="t")
    db.add_all([user, session])
    db.commit()

    orch = Orchestrator(db, provider=None, project_id=project.id)
    reply = await orch.handle(session=session, user=user, text="what's next")
    assert reply.blocks and reply.blocks[0]["type"] == "journey_card"
    assert reply.blocks[0]["stage"] == "plan"
    assert reply.suggestions[0]["text"] == "approve"
