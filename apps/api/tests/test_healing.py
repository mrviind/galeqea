"""Tiered healing, including the case that made naive scoring fail.

Healing must work with no model configured. These tests run the deterministic
tiers only - if they need an LLM to pass, the feature is not what it claims.
"""

from __future__ import annotations

import pytest

from galeqea.engine.healing import HealingEngine, _parse_locator, _render_locator


def candidate(**kwargs):
    base = {
        "role": "textbox", "name": "", "text": "", "testId": "", "id": "",
        "tag": "input", "score": 0.0, "evidenceCoverage": 0.57,
        "suggested": {"kind": "testid", "value": "x"}, "disabled": False,
        "ancestry": [], "classes": [],
    }
    base.update(kwargs)
    return base


def request_for(candidates, **overrides):
    payload = {
        "requestId": "req_1",
        "intent": "enter the card number",
        "action": "fill",
        "failedLadder": ["getByTestid('card-input-RENAMED')"],
        "candidates": candidates,
        "ariaSnapshot": "- textbox \"Card number\"",
        "url": "http://localhost/checkout",
        "elementId": None,
        "testCaseId": None,
        "stepId": None,
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_decisive_winner_heals_without_a_model(db, project):
    """The regression that motivated normalised scoring.

    A renamed test id drags the absolute score down while leaving the candidate
    an obvious winner. Requiring a high absolute score alone made this
    unhealable, which is precisely the case healing exists for.
    """
    engine = HealingEngine(db, provider=None, project_id=project.id)
    outcome = await engine.heal(request_for([
        candidate(name="Card number", testId="card-input", score=0.62,
                  suggested={"kind": "testid", "value": "card-input"}),
        candidate(name="Email address", score=0.20,
                  suggested={"kind": "role", "role": "textbox", "name": "Email address"}),
    ]))
    assert outcome.ok
    assert outcome.strategy == "fingerprint"
    assert outcome.locator == {"kind": "testid", "value": "card-input"}


@pytest.mark.asyncio
async def test_ambiguous_candidates_are_refused(db, project):
    """Two equally plausible matches must fail loudly, not pick one."""
    engine = HealingEngine(db, provider=None, project_id=project.id)
    outcome = await engine.heal(request_for([
        candidate(name="Card number", testId="a", score=0.80),
        candidate(name="Card number", testId="b", score=0.79),
    ]))
    assert not outcome.ok
    assert "ambiguous" in outcome.reason


@pytest.mark.asyncio
async def test_weak_match_is_refused(db, project):
    engine = HealingEngine(db, provider=None, project_id=project.id)
    outcome = await engine.heal(request_for([candidate(name="Unrelated", score=0.20)]))
    assert not outcome.ok
    assert "confidence floor" in outcome.reason


@pytest.mark.asyncio
async def test_empty_page_is_diagnosed_as_such(db, project):
    """No candidates means the app failed to render, not that a locator is stale."""
    engine = HealingEngine(db, provider=None, project_id=project.id)
    outcome = await engine.heal(request_for([]))
    assert not outcome.ok
    assert "failed to render" in outcome.reason


@pytest.mark.asyncio
async def test_every_heal_decision_is_recorded_as_evidence(db, project):
    from galeqea.models import HealEvent

    engine = HealingEngine(db, provider=None, project_id=project.id)
    await engine.heal(request_for([candidate(name="Card number", testId="card-input", score=0.62),
                                   candidate(name="Email", score=0.10)]))
    await engine.heal(request_for([candidate(name="Unrelated", score=0.15)]))
    db.commit()

    events = db.query(HealEvent).filter(HealEvent.project_id == project.id).all()
    statuses = {e.status for e in events}
    # Declines are recorded too: "why didn't it heal?" deserves an answer.
    assert "proposed" in statuses and "declined" in statuses


@pytest.mark.asyncio
async def test_heal_is_transient_until_a_human_approves(db, project):
    from galeqea.models import HealEvent

    engine = HealingEngine(db, provider=None, project_id=project.id)
    await engine.heal(request_for([candidate(name="Card number", testId="card-input", score=0.65),
                                   candidate(name="Email", score=0.10)]))
    db.commit()
    event = db.query(HealEvent).filter(HealEvent.status == "proposed").first()
    assert event.used_transiently is True

    with pytest.raises(ValueError, match="must be approved"):
        engine.apply_to_model(event.id, approved_by="someone")


def test_locator_rendering_round_trips():
    for rung in (
        {"kind": "testid", "value": "pay-button"},
        {"kind": "role", "role": "button", "name": "Confirm payment"},
        {"kind": "css", "value": "#order"},
    ):
        assert _parse_locator(_render_locator(rung)) == rung


# --------------------------------------------------------------------------- #
# App Model discovery and shared repair
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_one_element_produces_one_proposal_however_many_tests_hit_it(db, project):
    """Six tests hitting the same renamed button must yield ONE review item.

    Filing one proposal per test would recreate, inside the review queue, the
    exact per-test churn the App Model exists to eliminate.
    """
    from galeqea.models import AppElement, AppScreen, HealEvent

    screen = AppScreen(project_id=project.id, name="Checkout", route_signature="/checkout")
    db.add(screen); db.flush()
    element = AppElement(
        project_id=project.id, screen_id=screen.id, role="textbox",
        accessible_name="Card number", tag="input",
        locators=[{"kind": "testid", "value": "card-input"}],
        fingerprint={"attrs": {"data-testid": "card-input"}},
    )
    db.add(element); db.flush()

    engine = HealingEngine(db, provider=None, project_id=project.id)
    for i in range(4):
        await engine.heal(request_for(
            [candidate(name="Card number", testId="cc-number-v2", score=0.65,
                       suggested={"kind": "testid", "value": "cc-number-v2"}),
             candidate(name="Email", score=0.10)],
            elementId=element.id, testCaseId=f"test-{i}",
        ))
    db.commit()

    proposals = db.query(HealEvent).filter(
        HealEvent.element_id == element.id, HealEvent.status == "proposed"
    ).all()
    assert len(proposals) == 1
    assert proposals[0].evidence["occurrences"] == 4
    assert len(proposals[0].evidence["affected_tests"]) == 4


def test_repaired_count_reflects_tests_not_heal_events(db, project):
    """`tests_repaired` must count tests bound to the element.

    Counting heal events under-reports by exactly the amount deduplication
    saves - which would make the payoff invisible in the one place it is shown.
    """
    from galeqea.models import (
        AppElement, AppScreen, HealEvent, StepAction, TestCase,
        TestCategory, TestStatus, TestStep,
    )

    screen = AppScreen(project_id=project.id, name="S", route_signature="/s")
    db.add(screen); db.flush()
    element = AppElement(project_id=project.id, screen_id=screen.id, role="button",
                         accessible_name="Pay", locators=[{"kind": "testid", "value": "old"}])
    db.add(element); db.flush()

    for i in range(3):
        case = TestCase(project_id=project.id, key=f"K-{i}", title=f"t{i}",
                        category=TestCategory.AUTOMATED, status=TestStatus.APPROVED)
        db.add(case); db.flush()
        db.add(TestStep(test_case_id=case.id, index=0, action=StepAction.CLICK,
                        intent="pay", element_id=element.id))

    event = HealEvent(project_id=project.id, element_id=element.id, status="approved",
                      strategy="fingerprint", old_locator="testid=old",
                      new_locator="testid=new", score=0.9)
    db.add(event); db.commit()

    result = HealingEngine(db, project_id=project.id).apply_to_model(
        event.id, approved_by="reviewer"
    )
    assert result["tests_repaired"] == 3
    assert result["ladder_depth"] == 2   # new primary, old kept as fallback
