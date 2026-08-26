"""Exploratory testing: the guards, the traversal, and the de-duplication.

Every test here runs the deterministic strategy - if exploration needs a model
to work at all, it is not the feature it claims to be.
"""

from __future__ import annotations

import pytest

from galeqea.ai.agents.explorer import (
    ExplorerState,
    decide_deterministic,
    element_key,
    forbidden_reason,
)
from galeqea.ai.agents.findings import Finding, check, dedupe


def observation(candidates, url="https://app.test/checkout", **extra):
    return {"url": url, "route": url, "candidates": candidates, **extra}


def control(role="button", name="Continue", **extra):
    return {"role": role, "name": name, "text": name, "disabled": False, **extra}


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("label", ["Delete account", "Remove item", "Revoke access", "Sign out"])
def test_destructive_controls_are_never_clicked(label):
    """No environment, no flag, no model makes these acceptable."""
    assert forbidden_reason(control(name=label), allow_transactional=True) == "destructive"


@pytest.mark.parametrize("label", ["Pay now", "Place order", "Confirm payment", "Transfer funds"])
def test_transactional_controls_are_guarded_but_unlockable(label):
    """Blocked by default; allowed on a staging environment.

    Refusing these unconditionally means exploration can only ever look at the
    form and never at what submitting it does - which is most of the behaviour.
    """
    assert forbidden_reason(control(name=label), allow_transactional=False) == "transactional"
    assert forbidden_reason(control(name=label), allow_transactional=True) is None


def test_ordinary_controls_are_allowed():
    assert forbidden_reason(control(name="Add to basket"), allow_transactional=False) is None


# --------------------------------------------------------------------------- #
# Traversal
# --------------------------------------------------------------------------- #
def test_leaving_the_application_returns_to_the_start():
    """Going back past the entry page lands on about:blank.

    Left alone that becomes a loop - dead end, go back, dead end - which
    consumed 15 of an 18-step budget the first time this ran.
    """
    state = ExplorerState(base_url="https://app.test/")
    decision = decide_deterministic(observation([], url="about:blank"), state)
    assert decision.action == "goto"
    assert decision.url == "https://app.test/"


def test_repeated_backtracking_restarts_rather_than_looping():
    state = ExplorerState(base_url="https://app.test/")
    actions = [decide_deterministic(observation([]), state).action for _ in range(3)]
    assert actions[:2] == ["back", "back"]
    assert actions[2] == "goto"


def test_empty_inputs_are_probed_before_anything_is_clicked():
    """A form nobody filled tells you nothing about what submitting it does."""
    state = ExplorerState()
    decision = decide_deterministic(
        observation([control(role="link", name="Help", href="/help"),
                     control(role="textbox", name="Email address", value="")]),
        state,
    )
    assert decision.action == "fill"
    assert "Email address" in decision.rationale


def test_untouched_controls_are_preferred():
    state = ExplorerState()
    seen = control(name="Already tried")
    state.touched.add(element_key(seen))
    decision = decide_deterministic(observation([seen, control(name="Fresh")]), state)
    assert "Fresh" in decision.rationale


def test_skipped_controls_are_recorded_not_silently_ignored():
    """A coverage hole the user cannot see is worse than one they can."""
    state = ExplorerState()
    decide_deterministic(observation([control(name="Delete everything")]), state)
    assert state.skipped and state.skipped[0]["reason"] == "destructive"


# --------------------------------------------------------------------------- #
# Findings
# --------------------------------------------------------------------------- #
def test_console_errors_and_server_errors_are_found_without_a_model():
    found = check(observation(
        [control()],
        consoleErrors=[{"text": "TypeError: x is not a function"}],
        networkFailures=[{"url": "https://app.test/api/orders", "status": 503}],
    ))
    kinds = {f.kind for f in found}
    assert "console_error" in kinds
    assert "server_error" in kinds
    assert any(f.severity == "high" for f in found if f.kind == "server_error")


def test_cleared_input_without_navigation_is_reported_as_data_loss():
    before = observation([control(role="textbox", name="Bio", value="hello")])
    after = observation([control(role="textbox", name="Bio", value="")])
    found = check(after, before)
    assert any(f.kind == "data_loss" and f.severity == "high" for f in found)


def test_nothing_is_reported_against_pages_outside_the_application():
    """about:blank is not a dead end in the product; it is the end of history."""
    assert check(observation([], url="about:blank")) == []


def test_identical_findings_in_one_batch_collapse():
    """Two unlabelled inputs on a page are one form-label finding, not two."""
    duplicate = Finding(kind="accessibility", severity="medium",
                        title="Accessibility: form-label", detail="x")
    assert len(dedupe([duplicate, duplicate, duplicate])) == 1


# --------------------------------------------------------------------------- #
# Triage
# --------------------------------------------------------------------------- #
def test_promoting_a_finding_twice_returns_the_same_test(db, project, humans):
    """A double-click on "Promote to a test" must not create two tests."""
    from fastapi.testclient import TestClient

    from galeqea.main import app
    from galeqea.models import ExplorationFinding, TestCase

    finding = ExplorationFinding(
        project_id=project.id, session_id="s1", kind="console_error",
        severity="high", title="Console error: boom", detail="x",
        url="https://app.test/", reproduction=[{"action": "click", "target": "Save"}],
        signature="sig-1",
    )
    db.add(finding)
    db.commit()

    with TestClient(app) as client:
        first = client.post(
            f"/api/projects/{project.id}/findings/{finding.id}/decide",
            json={"decision": "promote"},
        ).json()
        second = client.post(
            f"/api/projects/{project.id}/findings/{finding.id}/decide",
            json={"decision": "promote"},
        ).json()

    assert first["test_id"] == second["test_id"]
    assert "already promoted" in second.get("note", "")

    promoted = db.query(TestCase).filter(
        TestCase.title.like("Regression: Console error%")
    ).all()
    assert len(promoted) == 1


def test_promotion_carries_the_reproduction_and_its_provenance(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    from galeqea.models import ExplorationFinding, TestCase

    finding = ExplorationFinding(
        project_id=project.id, session_id="s2", kind="data_loss", severity="high",
        title="Input cleared without navigating", detail="Fields were emptied.",
        url="https://app.test/account",
        reproduction=[
            {"action": "fill", "target": "Bio", "value": "hello"},
            {"action": "click", "target": "icon button"},
        ],
        signature="sig-2", found_by="deterministic",
    )
    db.add(finding)
    db.commit()

    with TestClient(app) as client:
        result = client.post(
            f"/api/projects/{project.id}/findings/{finding.id}/decide",
            json={"decision": "promote"},
        ).json()

    case = db.get(TestCase, result["test_id"])
    assert case.provenance["origin"] == "exploration"
    assert case.provenance["finding_id"] == finding.id
    # The trail plus a confirmation step: a defect promoted without its
    # reproduction is a title, not a test.
    assert len(case.steps) == 3
    assert "Bio" in case.steps[0].intent
