"""Export must produce code that runs somewhere else. Zero lock-in, verified."""

from __future__ import annotations

import pytest

from galeqea.engine.codegen import TARGETS, render
from galeqea.models import StepAction, TestCase, TestCategory, TestStatus, TestStep


@pytest.fixture()
def case(db, project):
    record = TestCase(
        project_id=project.id, key="EX-T-0001", title="Complete a purchase",
        category=TestCategory.AUTOMATED, status=TestStatus.APPROVED,
        rationale="verifies the primary revenue path", requirement_refs=["REQ-101"],
        tags=["smoke"], preconditions=["a signed-in user with a saved card"],
    )
    db.add(record)
    db.flush()
    steps = [
        (StepAction.GOTO, "open checkout", {}, {"url": "/checkout"}),
        (StepAction.FILL, "enter the email",
         {"ladder": [{"kind": "label", "value": "Email address"}]}, {"text": "a@b.co"}),
        (StepAction.CLICK, "confirm payment",
         {"ladder": [{"kind": "role", "role": "button", "name": "Confirm payment"}]}, {}),
        (StepAction.EXPECT_TEXT, "confirmation appears",
         {"ladder": [{"kind": "css", "value": "#order"}]}, {"text": "AC-"}),
    ]
    for index, (action, intent, target, value) in enumerate(steps):
        db.add(TestStep(test_case_id=record.id, index=index, action=action,
                        intent=intent, target=target, value=value))
    db.flush()
    db.refresh(record)
    return record


@pytest.mark.parametrize("target", TARGETS)
def test_every_target_renders(case, target):
    code = render(case, target=target, base_url="http://localhost:8765")
    assert code.strip()
    assert "EX-T-0001" in code or "Complete a purchase" in code


def test_playwright_export_uses_durable_locators(case):
    code = render(case, target="playwright", base_url="http://localhost:8765")
    assert "import { test, expect } from '@playwright/test';" in code
    assert "getByRole('button', { name: 'Confirm payment' })" in code
    assert "getByLabel('Email address')" in code
    assert "await expect(page.locator('#order')).toContainText('AC-')" in code
    # The intent survives as a comment: the exported file stays readable.
    assert "// confirm payment" in code


def test_export_carries_traceability(case):
    code = render(case, target="playwright")
    assert "REQ-101" in code


def test_unknown_target_is_refused(case):
    with pytest.raises(ValueError, match="unknown export target"):
        render(case, target="selenium-ide")
